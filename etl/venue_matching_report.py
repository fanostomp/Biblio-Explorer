"""Generate a reproducible venue-matching markdown audit report."""

from __future__ import annotations

import logging
import argparse
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG

logger = logging.getLogger(__name__)
from venue_matching import classify_match_stage, compute_mapping_metrics, detect_dblp_csv_inputs


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONF_INPUT = ROOT / "data" / "cleaned" / "cleaned_inproceedings.csv"
DEFAULT_CONF_MAPPING = ROOT / "data" / "matched" / "booktitle_to_conf_id.csv"
DEFAULT_JOUR_INPUT = ROOT / "data" / "cleaned" / "cleaned_articles.csv"
DEFAULT_JOUR_MAPPING = ROOT / "data" / "matched" / "journal_name_to_id.csv"
DEFAULT_CONF_REVIEW = ROOT / "data" / "matched" / "conference_match_review.csv"
DEFAULT_JOUR_REVIEW = ROOT / "data" / "matched" / "journal_match_review.csv"
DEFAULT_ALIAS_REVIEW = ROOT / "data" / "matched" / "venue_alias_review.csv"
DEFAULT_CONF_SEEDS = ROOT / "data" / "matched" / "conference_seed_venues.csv"
DEFAULT_DBLP_DATASET_DIR = ROOT / "data" / "dblp_dataset"
DEFAULT_OUTPUT = ROOT / "data" / "matched" / "2026-03-29-venue-matching-report.md"
DEFAULT_REGRESSION_OUTPUT = ROOT / "data" / "matched" / "conference_regression_diff.csv"
DEFAULT_HIGH_CONF_OUTPUT = ROOT / "data" / "matched" / "high_confidence_alias_proposals.csv"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conference-input", type=Path, default=DEFAULT_CONF_INPUT)
    parser.add_argument("--conference-mapping", type=Path, default=DEFAULT_CONF_MAPPING)
    parser.add_argument("--journal-input", type=Path, default=DEFAULT_JOUR_INPUT)
    parser.add_argument("--journal-mapping", type=Path, default=DEFAULT_JOUR_MAPPING)
    parser.add_argument("--conference-review", type=Path, default=DEFAULT_CONF_REVIEW)
    parser.add_argument("--journal-review", type=Path, default=DEFAULT_JOUR_REVIEW)
    parser.add_argument("--alias-review", type=Path, default=DEFAULT_ALIAS_REVIEW)
    parser.add_argument("--conference-seeds", type=Path, default=DEFAULT_CONF_SEEDS)
    parser.add_argument("--dblp-dataset-dir", type=Path, default=DEFAULT_DBLP_DATASET_DIR)
    parser.add_argument("--baseline-conference-mapping", type=Path, required=True)
    parser.add_argument("--baseline-journal-mapping", type=Path, required=True)
    parser.add_argument("--previous-report", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--regression-output", type=Path, default=DEFAULT_REGRESSION_OUTPUT)
    parser.add_argument("--high-confidence-output", type=Path, default=DEFAULT_HIGH_CONF_OUTPUT)
    parser.add_argument("--command", action="append", default=[])
    return parser.parse_args()


def load_value_counts(path: Path, key_col: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = (row.get(key_col) or "").strip()
            if value:
                counts[value] += 1
    return counts


def load_mapping_rows(path: Path, key_col: str) -> dict[str, dict[str, str]]:
    rows = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (row.get(key_col) or "").strip()
            if key:
                rows[key] = {field: (value or "") for field, value in row.items()}
    return rows


def row_stage(row: dict[str, str]) -> str:
    return classify_match_stage((row.get("match_type") or row.get("status") or "").strip())


def is_matched(row: dict[str, str], id_field: str) -> bool:
    return bool((row.get(id_field) or "").strip())


def is_skipped(row: dict[str, str]) -> bool:
    return row_stage(row) == "explicit skip / non_venue"


def build_regression_rows(
    baseline_rows: dict[str, dict[str, str]],
    current_rows: dict[str, dict[str, str]],
    review_rows: dict[str, dict[str, str]],
    title_lookup: dict[str, str],
    existing_rows: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    regressions: list[dict[str, str]] = []
    existing_rows = existing_rows or {}
    for booktitle, baseline in baseline_rows.items():
        previous_conf_id = (baseline.get("conf_id") or "").strip()
        current = current_rows.get(booktitle, {})
        if not previous_conf_id or is_matched(current, "conf_id"):
            continue
        review = review_rows.get(booktitle, {})
        existing = existing_rows.get(booktitle, {})
        regressions.append(
            {
                "booktitle": booktitle,
                "normalized_booktitle": review.get("normalized_strict", ""),
                "previous_conf_id": previous_conf_id,
                "previous_matched_title": title_lookup.get(previous_conf_id, ""),
                "current_best_candidate": review.get("candidate_1_title", ""),
                "current_stage": current.get("match_stage", review.get("match_stage", "")),
                "current_match_type": current.get("match_type", review.get("match_type", "")),
                "current_score": current.get("score", review.get("best_score", "")),
                "reason_blocked": review.get("blocked_reason", ""),
                "justification": existing.get("justification", ""),
            }
        )
    regressions.sort(key=lambda item: item["booktitle"].casefold())
    return regressions


def unexplained_regression_count(rows: list[dict[str, str]]) -> int:
    return sum(1 for row in rows if not (row.get("justification") or "").strip())


def enforce_regression_zero_growth(
    previous_rows: list[dict[str, str]],
    current_rows: list[dict[str, str]],
) -> None:
    previous_unexplained = unexplained_regression_count(previous_rows)
    current_unexplained = unexplained_regression_count(current_rows)
    if current_unexplained > previous_unexplained:
        raise ValueError(
            "unexplained conference regressions increased "
            f"({previous_unexplained} -> {current_unexplained})"
        )


def _is_high_confidence_review_row(row: dict[str, str]) -> bool:
    match_type = (row.get("match_type") or "").strip()
    try:
        score = float((row.get("best_score") or "0").strip() or 0.0)
    except ValueError:
        score = 0.0
    return (
        score >= 0.90
        or match_type == "metadata-alias"
        or match_type == "derived-acronym"
    )


def build_high_confidence_proposals(
    conference_review_rows: list[dict[str, str]],
    journal_review_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    proposals: list[dict[str, str]] = []

    for row in conference_review_rows:
        if not _is_high_confidence_review_row(row):
            continue
        proposals.append(
            {
                "venue_type": "conference",
                "raw_name": row.get("booktitle", ""),
                "normalized_name": row.get("normalized_strict", ""),
                "row_count": row.get("row_count", ""),
                "proposed_canonical_id": row.get("candidate_1_conf_id", ""),
                "proposed_canonical_title": row.get("candidate_1_title", ""),
                "score": row.get("best_score", ""),
                "match_type": row.get("match_type", ""),
                "evidence_stage": row.get("match_stage", ""),
                "blocked_reason": row.get("blocked_reason", ""),
            }
        )

    for row in journal_review_rows:
        if not _is_high_confidence_review_row(row):
            continue
        proposals.append(
            {
                "venue_type": "journal",
                "raw_name": row.get("dblp_journal_name", ""),
                "normalized_name": row.get("normalized_strict", ""),
                "row_count": row.get("row_count", ""),
                "proposed_canonical_id": row.get("candidate_1_journal_id", ""),
                "proposed_canonical_title": row.get("candidate_1_title", ""),
                "score": row.get("best_score", ""),
                "match_type": row.get("match_type", ""),
                "evidence_stage": row.get("match_stage", ""),
                "blocked_reason": row.get("blocked_reason", ""),
            }
        )

    proposals.sort(
        key=lambda item: (
            item["venue_type"],
            -int(item["row_count"] or 0),
            item["raw_name"].casefold(),
        )
    )
    return proposals


def fetch_lookup_titles(table: str, id_field: str) -> dict[str, str]:
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {id_field}, title FROM {table}")
        return {str(row_id): title for row_id, title in cur.fetchall()}
    finally:
        cur.close()
        conn.close()


def build_new_match_rows(
    counts: Counter[str],
    baseline_rows: dict[str, dict[str, str]],
    current_rows: dict[str, dict[str, str]],
    id_field: str,
    title_lookup: dict[str, str],
) -> list[dict[str, object]]:
    rows = []
    for value, row_count in counts.items():
        baseline = baseline_rows.get(value, {})
        current = current_rows.get(value, {})
        if is_matched(baseline, id_field) or not is_matched(current, id_field):
            continue
        venue_id = (current.get(id_field) or "").strip()
        rows.append(
            {
                "value": value,
                "row_count": row_count,
                "venue_id": venue_id,
                "venue_title": title_lookup.get(venue_id, ""),
                "match_stage": row_stage(current),
                "match_type": current.get("match_type", ""),
                "score": current.get("score", ""),
            }
        )
    rows.sort(key=lambda item: (-int(item["row_count"]), str(item["value"]).casefold()))
    return rows


def build_unmatched_rows(
    counts: Counter[str],
    current_rows: dict[str, dict[str, str]],
    id_field: str,
) -> list[dict[str, object]]:
    rows = []
    for value, row_count in counts.items():
        current = current_rows.get(value, {})
        if is_matched(current, id_field) or is_skipped(current):
            continue
        rows.append(
            {
                "value": value,
                "row_count": row_count,
                "score": current.get("score", ""),
                "match_type": current.get("match_type", "unmatched"),
            }
        )
    rows.sort(key=lambda item: (-int(item["row_count"]), str(item["value"]).casefold()))
    return rows


def stage_counts_for_new_matches(
    counts: Counter[str],
    baseline_rows: dict[str, dict[str, str]],
    current_rows: dict[str, dict[str, str]],
    id_field: str,
) -> dict[str, dict[str, int]]:
    stage_counts: dict[str, dict[str, int]] = {}
    for value, row_count in counts.items():
        baseline = baseline_rows.get(value, {})
        current = current_rows.get(value, {})
        if is_matched(baseline, id_field) or not is_matched(current, id_field):
            continue
        stage = row_stage(current)
        bucket = stage_counts.setdefault(stage, {"distinct": 0, "rows": 0})
        bucket["distinct"] += 1
        bucket["rows"] += row_count
    return dict(sorted(stage_counts.items()))


def parse_previous_report_metrics(path: Path | None) -> dict[str, int]:
    if path is None or not path.exists():
        return {}
    content = path.read_text(encoding="utf-8")
    metrics = {}
    patterns = {
        "conference_matched_distinct": r"\| Distinct booktitles matched \| [^|]+ \| ([0-9,]+) \|",
        "conference_unmatched_distinct": r"\| Distinct unresolved booktitles \| [^|]+ \| ([0-9,]+) \|",
        "conference_unmatched_rows": r"\| Conference paper rows unresolved \| [^|]+ \| ([0-9,]+) \|",
        "journal_matched_distinct": r"\| Distinct journal names matched \| [^|]+ \| ([0-9,]+) \|",
        "journal_unmatched_distinct": r"\| Distinct unresolved journal names \| [^|]+ \| ([0-9,]+) \|",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            metrics[key] = int(match.group(1).replace(",", ""))
    return metrics


def format_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = []
    header = rows[0]
    lines.append("| " + " | ".join(str(item) for item in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def build_manual_seed_batch_summary(
    *,
    seed_path: Path,
    review_path: Path,
    reviewed_by: str | None = None,
    reviewed_at: str | None = None,
) -> dict[str, object]:
    seed_rows = load_mapping_rows(seed_path, "dblp_key") if seed_path.exists() else {}
    review_rows = list(load_mapping_rows(review_path, "id").values()) if review_path.exists() else []

    batch_pattern = re.compile(r"manual-reviewed-seed-batch-(\d+)$")
    stamps: list[tuple[str, int, str]] = []
    for row in list(seed_rows.values()) + review_rows:
        reviewer = (row.get("added_by") or row.get("reviewed_by") or "").strip()
        stamp_date = (row.get("date_added") or row.get("reviewed_at") or "").strip()
        match = batch_pattern.fullmatch(reviewer)
        if not match or not stamp_date:
            continue
        stamps.append((stamp_date, int(match.group(1)), reviewer))

    if reviewed_by is None or reviewed_at is None:
        if stamps:
            latest_date, _latest_batch, latest_reviewer = max(stamps, key=lambda item: (item[0], item[1]))
            reviewed_by = reviewed_by or latest_reviewer
            reviewed_at = reviewed_at or latest_date
        else:
            reviewed_by = reviewed_by or "manual-reviewed-seed-batch-2"
            reviewed_at = reviewed_at or "2026-03-31"

    filtered_seed_rows = [
        row
        for row in seed_rows.values()
        if (row.get("added_by") or "").strip() == reviewed_by
        and (row.get("date_added") or "").strip() == reviewed_at
    ]
    filtered_review_rows = [
        row
        for row in review_rows
        if (row.get("venue_type") or "").strip() == "conference"
        and (row.get("status") or "").strip() == "approved"
        and (row.get("reviewed_by") or "").strip() == reviewed_by
        and (row.get("reviewed_at") or "").strip() == reviewed_at
    ]

    return {
        "seed_rows_added": len(filtered_seed_rows),
        "approved_review_rows": len(filtered_review_rows),
        "seed_titles": sorted((row.get("title") or "").strip() for row in filtered_seed_rows if (row.get("title") or "").strip()),
        "approved_raw_names": sorted(
            (row.get("raw_name") or "").strip()
            for row in filtered_review_rows
            if (row.get("raw_name") or "").strip()
        ),
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
    }


def build_manual_review_batch_summary(
    *,
    review_path: Path,
    venue_type: str,
    reviewed_by: str | None = None,
    reviewed_at: str | None = None,
) -> dict[str, object]:
    review_rows = list(load_mapping_rows(review_path, "id").values()) if review_path.exists() else []
    batch_pattern = re.compile(
        rf"manual-reviewed-{re.escape(venue_type)}(?:-catalog)?-batch-(\d+)$"
    )

    stamps: list[tuple[str, int, int, str]] = []
    for row in review_rows:
        if (row.get("venue_type") or "").strip() != venue_type:
            continue
        if (row.get("status") or "").strip() != "approved":
            continue
        reviewer = (row.get("reviewed_by") or "").strip()
        stamp_date = (row.get("reviewed_at") or "").strip()
        match = batch_pattern.fullmatch(reviewer)
        if not match or not stamp_date:
            continue
        stamps.append(
            (
                stamp_date,
                1 if "-catalog-batch-" in reviewer else 0,
                int(match.group(1)),
                reviewer,
            )
        )

    if reviewed_by is None or reviewed_at is None:
        if stamps:
            latest_date, _catalog_weight, _latest_batch, latest_reviewer = max(
                stamps,
                key=lambda item: (item[0], item[1], item[2]),
            )
            reviewed_by = reviewed_by or latest_reviewer
            reviewed_at = reviewed_at or latest_date
        else:
            reviewed_by = reviewed_by or ""
            reviewed_at = reviewed_at or ""

    filtered_review_rows = [
        row
        for row in review_rows
        if (row.get("venue_type") or "").strip() == venue_type
        and (row.get("status") or "").strip() == "approved"
        and (row.get("reviewed_by") or "").strip() == reviewed_by
        and (row.get("reviewed_at") or "").strip() == reviewed_at
    ]

    return {
        "approved_review_rows": len(filtered_review_rows),
        "approved_raw_names": sorted(
            (row.get("raw_name") or "").strip()
            for row in filtered_review_rows
            if (row.get("raw_name") or "").strip()
        ),
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
    }


def render_report(args) -> str:
    baseline_metrics = compute_mapping_metrics(
        conference_input_csv=args.conference_input,
        conference_mapping_csv=args.baseline_conference_mapping,
        journal_input_csv=args.journal_input,
        journal_mapping_csv=args.baseline_journal_mapping,
    )
    current_metrics = compute_mapping_metrics(
        conference_input_csv=args.conference_input,
        conference_mapping_csv=args.conference_mapping,
        journal_input_csv=args.journal_input,
        journal_mapping_csv=args.journal_mapping,
    )

    conf_counts = load_value_counts(args.conference_input, "booktitle")
    jour_counts = load_value_counts(args.journal_input, "journal")
    baseline_conf_rows = load_mapping_rows(args.baseline_conference_mapping, "booktitle")
    current_conf_rows = load_mapping_rows(args.conference_mapping, "booktitle")
    baseline_jour_rows = load_mapping_rows(args.baseline_journal_mapping, "dblp_journal_name")
    current_jour_rows = load_mapping_rows(args.journal_mapping, "dblp_journal_name")
    conference_review_rows = load_mapping_rows(args.conference_review, "booktitle")
    journal_review_rows = load_mapping_rows(args.journal_review, "dblp_journal_name")
    existing_regression_rows = (
        load_mapping_rows(args.regression_output, "booktitle")
        if args.regression_output.exists()
        else {}
    )
    conference_titles = fetch_lookup_titles("conferences", "conf_id")
    journal_titles = fetch_lookup_titles("journals", "journal_id")

    new_conf_matches = build_new_match_rows(
        conf_counts,
        baseline_conf_rows,
        current_conf_rows,
        "conf_id",
        conference_titles,
    )
    new_jour_matches = build_new_match_rows(
        jour_counts,
        baseline_jour_rows,
        current_jour_rows,
        "journal_id",
        journal_titles,
    )
    unmatched_conf = build_unmatched_rows(conf_counts, current_conf_rows, "conf_id")
    unmatched_jour = build_unmatched_rows(jour_counts, current_jour_rows, "journal_id")
    conf_stage_deltas = stage_counts_for_new_matches(
        conf_counts,
        baseline_conf_rows,
        current_conf_rows,
        "conf_id",
    )
    jour_stage_deltas = stage_counts_for_new_matches(
        jour_counts,
        baseline_jour_rows,
        current_jour_rows,
        "journal_id",
    )
    regression_rows = build_regression_rows(
        baseline_rows=baseline_conf_rows,
        current_rows=current_conf_rows,
        review_rows=conference_review_rows,
        title_lookup=conference_titles,
        existing_rows=existing_regression_rows,
    )
    enforce_regression_zero_growth(list(existing_regression_rows.values()), regression_rows)
    high_confidence_rows = build_high_confidence_proposals(
        list(conference_review_rows.values()),
        list(journal_review_rows.values()),
    )
    write_csv(
        args.regression_output,
        [
            "booktitle",
            "normalized_booktitle",
            "previous_conf_id",
            "previous_matched_title",
            "current_best_candidate",
            "current_stage",
            "current_match_type",
            "current_score",
            "reason_blocked",
            "justification",
        ],
        regression_rows,
    )
    write_csv(
        args.high_confidence_output,
        [
            "venue_type",
            "raw_name",
            "normalized_name",
            "row_count",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_stage",
            "blocked_reason",
        ],
        high_confidence_rows,
    )

    previous_metrics = parse_previous_report_metrics(args.previous_report)
    previous_consistency = "No previous report was provided."
    if previous_metrics:
        checks = {
            "conference_matched_distinct": baseline_metrics["conference"]["matched_distinct"],
            "conference_unmatched_distinct": baseline_metrics["conference"]["unmatched_distinct"],
            "conference_unmatched_rows": baseline_metrics["conference"]["unmatched_rows"],
            "journal_matched_distinct": baseline_metrics["journal"]["matched_distinct"],
            "journal_unmatched_distinct": baseline_metrics["journal"]["unmatched_distinct"],
        }
        if all(previous_metrics.get(key) == value for key, value in checks.items()):
            previous_consistency = (
                "Consistent. The metrics captured in the previous report match the baseline mappings "
                "that were copied before this rerun."
            )
        else:
            previous_consistency = (
                "Inconsistent. The previous report numbers do not line up with the copied baseline "
                "mapping outputs, which suggests the prior comparison mixed different output states."
            )

    dblp_inputs = detect_dblp_csv_inputs(args.dblp_dataset_dir)
    seed_batch_summary = build_manual_seed_batch_summary(
        seed_path=args.conference_seeds,
        review_path=args.alias_review,
    )
    journal_batch_summary = build_manual_review_batch_summary(
        review_path=args.alias_review,
        venue_type="journal",
    )
    dblp_note = (
        f"Detected local DBLP CSV inputs under `{args.dblp_dataset_dir}` and used them as the primary "
        "offline authority for conference and journal matching."
        if dblp_inputs.get("files")
        else f"No local DBLP CSV inputs were detected under `{args.dblp_dataset_dir}`."
    )

    lines = ["# Venue Matching Audit Report", ""]
    lines.append("## Commands Run")
    if args.command:
        lines.append("```text")
        lines.extend(args.command)
        lines.append("```")
    else:
        lines.append("No commands were supplied to the report generator.")
    lines.append("")

    lines.append("## Manual-Reviewed Seed Batch")
    lines.append(
        f"- Reviewer stamp: `{seed_batch_summary['reviewed_by']}` on `{seed_batch_summary['reviewed_at']}`."
    )
    lines.append(
        f"- Conference seed rows added in this batch: `{seed_batch_summary['seed_rows_added']}`."
    )
    lines.append(
        f"- Approved conference review-memory rows in this batch: `{seed_batch_summary['approved_review_rows']}`."
    )
    seed_titles = seed_batch_summary["seed_titles"]
    if seed_titles:
        lines.append(
            f"- Seed titles: {', '.join(f'`{title}`' for title in seed_titles)}"
        )
    approved_raw_names = seed_batch_summary["approved_raw_names"]
    if approved_raw_names:
        lines.append(
            f"- Approved review-memory names: {', '.join(f'`{name}`' for name in approved_raw_names)}"
        )
    lines.append("")

    if journal_batch_summary["reviewed_by"]:
        lines.append("## Manual-Reviewed Journal Batch")
        lines.append(
            f"- Reviewer stamp: `{journal_batch_summary['reviewed_by']}` on `{journal_batch_summary['reviewed_at']}`."
        )
        lines.append(
            f"- Approved journal review-memory rows in this batch: `{journal_batch_summary['approved_review_rows']}`."
        )
        approved_journal_names = journal_batch_summary["approved_raw_names"]
        if approved_journal_names:
            lines.append(
                f"- Approved journal names: {', '.join(f'`{name}`' for name in approved_journal_names)}"
            )
        lines.append("")

    lines.append("## Local DBLP CSV Inputs")
    for label in ("article", "inproceedings"):
        payload = dblp_inputs.get(label)
        if not payload:
            continue
        lines.append(
            f"- `{label}`: `{payload['path']}`"
        )
        lines.append(
            f"  columns: {', '.join(payload['columns'])}"
        )
    if not dblp_inputs.get("article") and not dblp_inputs.get("inproceedings"):
        lines.append("- No `data/dblp_dataset/input_*.csv` files were detected.")
    lines.append("")

    lines.append("## Before / After")
    lines.append("")
    lines.append("### Conferences")
    lines.append(
        format_table(
            [
                ["Metric", "Before", "After", "Delta"],
                [
                    "Matched distinct conference booktitles",
                    f"{baseline_metrics['conference']['matched_distinct']:,}",
                    f"{current_metrics['conference']['matched_distinct']:,}",
                    f"{current_metrics['conference']['matched_distinct'] - baseline_metrics['conference']['matched_distinct']:+,}",
                ],
                [
                    "Unmatched distinct conference booktitles",
                    f"{baseline_metrics['conference']['unmatched_distinct']:,}",
                    f"{current_metrics['conference']['unmatched_distinct']:,}",
                    f"{current_metrics['conference']['unmatched_distinct'] - baseline_metrics['conference']['unmatched_distinct']:+,}",
                ],
                [
                    "Unmatched conference rows",
                    f"{baseline_metrics['conference']['unmatched_rows']:,}",
                    f"{current_metrics['conference']['unmatched_rows']:,}",
                    f"{current_metrics['conference']['unmatched_rows'] - baseline_metrics['conference']['unmatched_rows']:+,}",
                ],
            ]
        )
    )
    lines.append("")
    lines.append("### Journals")
    lines.append(
        format_table(
            [
                ["Metric", "Before", "After", "Delta"],
                [
                    "Matched distinct journal names",
                    f"{baseline_metrics['journal']['matched_distinct']:,}",
                    f"{current_metrics['journal']['matched_distinct']:,}",
                    f"{current_metrics['journal']['matched_distinct'] - baseline_metrics['journal']['matched_distinct']:+,}",
                ],
                [
                    "Unmatched distinct journal names",
                    f"{baseline_metrics['journal']['unmatched_distinct']:,}",
                    f"{current_metrics['journal']['unmatched_distinct']:,}",
                    f"{current_metrics['journal']['unmatched_distinct'] - baseline_metrics['journal']['unmatched_distinct']:+,}",
                ],
                [
                    "Intentional-null / skip journal rows",
                    f"{baseline_metrics['journal']['skipped_or_non_venue_rows']:,}",
                    f"{current_metrics['journal']['skipped_or_non_venue_rows']:,}",
                    f"{current_metrics['journal']['skipped_or_non_venue_rows'] - baseline_metrics['journal']['skipped_or_non_venue_rows']:+,}",
                ],
            ]
        )
    )
    lines.append("")

    lines.append("## Stage-Level New Matches")
    lines.append("")
    lines.append("### Conferences")
    lines.append(
        format_table(
            [["Stage", "New distinct", "New rows"]]
            + [
                [stage, f"{payload['distinct']:,}", f"{payload['rows']:,}"]
                for stage, payload in conf_stage_deltas.items()
            ]
        )
        or "No newly matched conference booktitles."
    )
    lines.append("")
    lines.append("### Journals")
    lines.append(
        format_table(
            [["Stage", "New distinct", "New rows"]]
            + [
                [stage, f"{payload['distinct']:,}", f"{payload['rows']:,}"]
                for stage, payload in jour_stage_deltas.items()
            ]
        )
        or "No newly matched journal names."
    )
    lines.append("")

    lines.append("## Top Newly Matched Conference Families")
    lines.append(
        format_table(
            [["Rows", "Booktitle", "conf_id", "Conference title", "Stage", "Match type"]]
            + [
                [
                    f"{row['row_count']:,}",
                    row["value"],
                    row["venue_id"],
                    row["venue_title"],
                    row["match_stage"],
                    row["match_type"],
                ]
                for row in new_conf_matches[:15]
            ]
        )
        or "No newly matched conference families."
    )
    lines.append("")

    abbreviated_new_journals = [
        row
        for row in new_jour_matches
        if "." in str(row["value"]) or len(str(row["value"])) <= 12 or str(row["value"]).isupper()
    ]
    lines.append("## Top Newly Matched Abbreviated Journals")
    lines.append(
        format_table(
            [["Rows", "Journal name", "journal_id", "Journal title", "Stage", "Match type"]]
            + [
                [
                    f"{row['row_count']:,}",
                    row["value"],
                    row["venue_id"],
                    row["venue_title"],
                    row["match_stage"],
                    row["match_type"],
                ]
                for row in abbreviated_new_journals[:15]
            ]
        )
        or "No newly matched abbreviated journals."
    )
    lines.append("")

    lines.append("## Remaining Top Unmatched Conferences By Row Frequency")
    lines.append(
        format_table(
            [["Rows", "Booktitle", "Best stage", "Best match type", "Score"]]
            + [
                [
                    f"{row['row_count']:,}",
                    row["value"],
                    row_stage(current_conf_rows.get(str(row["value"]), {})),
                    row["match_type"],
                    row["score"],
                ]
                for row in unmatched_conf[:20]
            ]
        )
    )
    lines.append("")

    lines.append("## Remaining Top Unmatched Journals")
    lines.append(
        format_table(
            [["Rows", "Journal name", "Best match type", "Score"]]
            + [
                [
                    f"{row['row_count']:,}",
                    row["value"],
                    row["match_type"],
                    row["score"],
                ]
                for row in unmatched_jour[:20]
            ]
        )
    )
    lines.append("")

    lines.append("## Conference Regression Diff")
    lines.append(
        format_table(
            [[
                "Booktitle",
                "Prev conf_id",
                "Prev title",
                "Current best",
                "Stage",
                "Match type",
                "Score",
                "Blocked",
                "Justification",
            ]]
            + [
                [
                    row["booktitle"],
                    row["previous_conf_id"],
                    row["previous_matched_title"],
                    row["current_best_candidate"],
                    row["current_stage"],
                    row["current_match_type"],
                    row["current_score"],
                    row["reason_blocked"],
                    row["justification"],
                ]
                for row in regression_rows[:20]
            ]
        )
        or "No conference matched-to-unmatched regressions remain."
    )
    lines.append("")

    lines.append("## High-Confidence Review Candidates")
    lines.append(
        format_table(
            [["Type", "Raw name", "Rows", "Proposed title", "Stage", "Match type", "Score", "Blocked"]]
            + [
                [
                    row["venue_type"],
                    row["raw_name"],
                    row["row_count"],
                    row["proposed_canonical_title"],
                    row["evidence_stage"],
                    row["match_type"],
                    row["score"],
                    row["blocked_reason"],
                ]
                for row in high_confidence_rows[:20]
            ]
        )
        or "No high-confidence review candidates were detected."
    )
    lines.append("")

    lines.append("## Audit Notes")
    lines.append(f"- Previous metrics consistency: {previous_consistency}")
    lines.append(f"- Local DBLP CSV usage: {dblp_note}")
    lines.append(f"- Conference regression diff CSV: `{args.regression_output}` ({len(regression_rows):,} rows).")
    lines.append(
        f"- High-confidence proposal CSV: `{args.high_confidence_output}` ({len(high_confidence_rows):,} rows)."
    )
    lines.append(
        "- Remaining blockers: the highest-yield conference misses are still acronym collisions or series absent from the local lookup, and journal misses remain constrained by missing ISSN/title-variant metadata in the ranking source."
    )
    lines.append(
        "- Removal path: keep the local DBLP CSV dataset refreshed, add more ISSN/title-variant evidence through OpenAlex/Crossref or curated aliases, and review the proposal CSVs for the top unresolved families."
    )
    lines.append("")
    return "\n".join(lines)


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(level=logging.INFO):
    logging.basicConfig(level=level, format=LOG_FORMAT)


def main():
    args = parse_args()
    report = render_report(args)
    args.output.write_text(report, encoding="utf-8")
    logger.info(f"Wrote {args.output}")


if __name__ == "__main__":
    configure_logging()
    main()
