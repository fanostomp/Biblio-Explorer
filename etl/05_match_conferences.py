"""
05_match_conferences.py
-----------------------
Match DBLP conference booktitles onto the local conferences lookup table.

The matcher now uses:
  1. Manual overrides from conference_manual_aliases.csv
  2. Exact/local normalized title and acronym matches
  3. Historical aliases parsed from conference titles
  4. Segment-aware acronym matching (for example HLT-NAACL)
  5. DBLP local CSV registry fallback from data/dblp_dataset/input_inproceedings*.csv
  6. Review queues for unresolved or ambiguous cases

Additional seed venues can be maintained in data/matched/conference_seed_venues.csv.
They are keyed by stable DBLP series slugs rather than row-order assumptions.

Run: python etl/05_match_conferences.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG
from venue_matching import (
    MatchCandidate,
    MatchResult,
    ManualOverride,
    VenueMatcher,
    VenueNormalizer,
    VenueRecord,
    build_blocked_reason,
    build_dblp_csv_index,
    classify_match_stage,
    classify_conference_review_bucket,
    clean_for_match,
    compact_key,
    load_persistent_venue_aliases,
    split_conference_review_rows_by_action,
    stable_canonical_venue_id,
    sync_alias_review_statuses,
    sync_review_statuses_from_resolved_rows,
    upsert_alias_review_records,
    write_match_audit_rows,
)


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CLEANED_DIR = os.path.join(DATA_DIR, "cleaned")
OUT_DIR = os.path.join(DATA_DIR, "matched")
os.makedirs(OUT_DIR, exist_ok=True)

INPROC_CSV = os.path.join(CLEANED_DIR, "cleaned_inproceedings.csv")
DBLP_DATASET_DIR = os.environ.get(
    "BIBEXP_DBLP_DATASET_DIR",
    os.path.join(DATA_DIR, "dblp_dataset"),
)
CACHE_DIR = os.environ.get(
    "BIBEXP_MATCH_CACHE_DIR",
    os.path.join(OUT_DIR, "cache", "venue_matching"),
)
OUT_MAPPING = os.path.join(OUT_DIR, "booktitle_to_conf_id.csv")
OUT_UNMATCHED = os.path.join(OUT_DIR, "unmatched_conferences.txt")
OUT_UNMATCHED_BY_ROWS = os.path.join(OUT_DIR, "unmatched_conferences_by_rows.txt")
OUT_REVIEW = os.path.join(OUT_DIR, "conference_match_review.csv")
OUT_PROPOSALS = os.path.join(OUT_DIR, "conference_alias_proposals.csv")
OUT_APPROVE_CANDIDATES = os.path.join(OUT_DIR, "conference_alias_approve_candidates.csv")
OUT_LOOKUP_ENRICHMENT = os.path.join(OUT_DIR, "conference_lookup_enrichment_candidates.csv")
OUT_COLLISION_REVIEW_ONLY = os.path.join(OUT_DIR, "conference_collision_review_only.csv")
OUT_GENERATED_REGISTRY = os.path.join(OUT_DIR, "conference_generated_registry.csv")
OUT_ALIAS_REVIEW = os.path.join(OUT_DIR, "venue_alias_review.csv")
OUT_ALIAS_MEMORY = os.path.join(OUT_DIR, "venue_aliases.csv")
OUT_MATCH_AUDIT = os.path.join(OUT_DIR, "venue_match_audit.csv")
MANUAL_ALIAS_CSV = os.path.join(OUT_DIR, "conference_manual_aliases.csv")
CONFLICTS_CSV = os.path.join(OUT_DIR, "conference_source_conflicts.csv")
SEED_VENUES_CSV = os.path.join(OUT_DIR, "conference_seed_venues.csv")
TOP_REVIEW_CANDIDATES = 3
MIN_GENERATED_SERIES_ROWS = 10
REVIEWABLE_MATCH_TYPES = {"review", "unmatched", "ambiguous-acronym"}


def console_safe(text):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


def load_local_dblp_csv_index():
    return build_dblp_csv_index(
        dataset_dir=DBLP_DATASET_DIR,
        cache_dir=CACHE_DIR,
        persist_dir=OUT_DIR,
    )


def local_dblp_query_keys(booktitle, normalizer, conflict_acronyms):
    forms = normalizer.normalize(booktitle)
    keys = [forms.strict, forms.loose, forms.parent, *forms.segment_candidates]
    if not any(value in conflict_acronyms for value in forms.acronym_candidates):
        keys.extend(forms.acronym_candidates)
    deduped = []
    seen = set()
    for key in keys:
        value = (key or "").strip()
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def resolve_series_from_local_dblp(booktitle, local_dblp_index, normalizer, conflict_acronyms):
    direct = local_dblp_index.get("conference_series_by_booktitle", {}).get(booktitle)
    if direct:
        return direct

    candidates = Counter()
    alias_map = local_dblp_index.get("conference_aliases", {})
    for key in local_dblp_query_keys(booktitle, normalizer, conflict_acronyms):
        for payload in alias_map.get(key, []):
            series_key = (payload.get("series_key") or "").strip()
            if series_key:
                candidates[series_key] += int(payload.get("count") or 1)

    if not candidates:
        return ""
    top_series, top_count = candidates.most_common(1)[0]
    runner_up_count = candidates.most_common(2)[1][1] if len(candidates) > 1 else 0
    if runner_up_count and top_count <= runner_up_count * 1.10:
        return ""
    return top_series


def build_conference_metadata(dblp_key, local_dblp_index):
    metadata = {"dblp_key": dblp_key or ""}
    if not dblp_key:
        return metadata
    registry_entry = local_dblp_index.get("conference_series_registry", {}).get(dblp_key, {})
    if not registry_entry:
        return metadata
    alternate_titles = []
    for value in [
        registry_entry.get("representative_title", ""),
        registry_entry.get("dominant_booktitle", ""),
        *(registry_entry.get("top_booktitles", []) or []),
    ]:
        title = (value or "").strip()
        if title and title not in alternate_titles:
            alternate_titles.append(title)
    if alternate_titles:
        metadata["alternate_titles"] = tuple(alternate_titles)
    return metadata


def extract_series_key(row):
    crossref = (row.get("crossref") or "").strip()
    dblp_key = (row.get("dblp_key") or "").strip()

    for value in (crossref, dblp_key):
        if value.startswith("conf/"):
            parts = value.split("/")
            if len(parts) >= 2:
                return "/".join(parts[:2])
    return ""


def load_booktitle_context():
    booktitle_counts = Counter()
    cleaned_series_counts = defaultdict(Counter)

    with open(INPROC_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            booktitle = (row.get("booktitle") or "").strip()
            if not booktitle:
                continue
            booktitle_counts[booktitle] += 1
            series_key = extract_series_key(row)
            if series_key:
                cleaned_series_counts[booktitle][series_key] += 1

    cleaned_series_by_booktitle = {
        booktitle: counts.most_common(1)[0][0]
        for booktitle, counts in cleaned_series_counts.items()
        if counts
    }
    return booktitle_counts, cleaned_series_by_booktitle


def load_conflict_acronyms():
    conflict_acronyms = set()
    if not os.path.exists(CONFLICTS_CSV):
        return conflict_acronyms

    with open(CONFLICTS_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            acronym = compact_key(row.get("acronym") or "")
            if acronym:
                conflict_acronyms.add(acronym)
    return conflict_acronyms


def ensure_manual_alias_file():
    if os.path.exists(MANUAL_ALIAS_CSV):
        return

    with open(MANUAL_ALIAS_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "booktitle",
                "conf_id",
                "action",
                "conf_acronym",
                "conf_title",
                "notes",
                "date_added",
                "added_by",
            ]
        )


def ensure_seed_venues(cursor):
    if not os.path.exists(SEED_VENUES_CSV):
        return 0

    cursor.execute("SELECT acronym FROM conferences")
    existing_acronyms = {compact_key(acronym) for (acronym,) in cursor.fetchall() if acronym}

    rows_to_insert = []
    with open(SEED_VENUES_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            title = (row.get("title") or "").strip()
            acronym = (row.get("acronym") or "").strip()
            dblp_key = (row.get("dblp_key") or "").strip() or None
            if not title or not acronym:
                continue
            if compact_key(acronym) in existing_acronyms:
                continue
            rows_to_insert.append((title, acronym, None, None, dblp_key))

    if not rows_to_insert:
        return 0

    cursor.executemany(
        """
        INSERT INTO conferences (title, acronym, rank, primary_for, dblp_key)
        VALUES (%s, %s, %s, %s, %s)
        """,
        rows_to_insert,
    )
    return cursor.rowcount


def load_seed_review_metadata():
    metadata_by_series = {}
    if not os.path.exists(SEED_VENUES_CSV):
        return metadata_by_series

    with open(SEED_VENUES_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            series_key = (row.get("dblp_key") or "").strip()
            reviewed_by = (row.get("added_by") or "").strip()
            reviewed_at = (row.get("date_added") or "").strip()
            if not series_key or not reviewed_by or not reviewed_at:
                continue
            metadata_by_series[series_key] = {
                "reviewed_by": reviewed_by,
                "reviewed_at": reviewed_at,
            }
    return metadata_by_series


def fetch_conference_rows(cursor):
    cursor.execute(
        "SELECT conf_id, acronym, title, COALESCE(dblp_key, '') FROM conferences"
    )
    return cursor.fetchall()


def accepted_series_bridge(result):
    return (
        result.status == "matched"
        and result.venue_id is not None
        and result.match_type in {
            "manual-match",
            "exact-title",
            "exact-acronym",
            "historical-alias",
            "segment-acronym",
            "metadata-alias",
        }
        and result.score >= 0.94
    )


def resolve_manual_conf_id(row, valid_conf_ids, by_acronym, by_title):
    conf_id_raw = (row.get("conf_id") or "").strip()
    conf_acronym = compact_key(row.get("conf_acronym") or "")
    conf_title = clean_for_match(row.get("conf_title") or "")

    if conf_id_raw:
        try:
            conf_id = int(conf_id_raw)
        except ValueError:
            return None
        return conf_id if conf_id in valid_conf_ids else None

    if conf_acronym:
        return by_acronym.get(conf_acronym)

    if conf_title:
        return by_title.get(conf_title)

    return None


def load_manual_overrides(valid_conf_ids, by_acronym, by_title):
    ensure_manual_alias_file()
    overrides = {}

    with open(MANUAL_ALIAS_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "booktitle" not in reader.fieldnames:
            raise ValueError(f"{MANUAL_ALIAS_CSV} must contain a booktitle column")

        for line_no, row in enumerate(reader, start=2):
            booktitle = (row.get("booktitle") or "").strip()
            if not booktitle:
                continue

            action = (row.get("action") or "match").strip().casefold()
            if action not in {"match", "skip", "non_venue", "unmatch"}:
                print(f"WARNING: ignoring unknown conference action at line {line_no}: {row!r}")
                continue

            conf_id = resolve_manual_conf_id(row, valid_conf_ids, by_acronym, by_title)
            if action == "match" and conf_id is None:
                print(
                    f"WARNING: ignoring unresolved conference manual match at line {line_no}: {row!r}"
                )
                continue

            overrides[booktitle] = ManualOverride(action=action, venue_id=conf_id)

    return overrides


def review_action_for_result(result):
    if result.status == "matched":
        return "auto_accept"
    if result.status == "non_venue":
        return "non_venue"
    if result.status == "skipped":
        return "skip"
    return "review"


def should_backfill_series_key(result, series_key, records_by_id):
    if not series_key or result.status != "matched" or result.venue_id is None:
        return False
    if result.score < 0.96:
        return False
    if result.match_type not in {
        "manual-match",
        "exact-title",
        "exact-acronym",
        "metadata-alias",
        "segment-acronym",
        "parent-series",
        "variant-strip",
        "historical-alias",
        "dblp-local-csv-registry",
    }:
        return False
    record = records_by_id.get(result.venue_id)
    return bool(record and not record.metadata.get("dblp_key"))


def build_review_row(booktitle, row_count, series_key, result, normalizer):
    forms = normalizer.normalize(booktitle)
    stage = classify_match_stage(result.match_type)
    candidates = list(result.candidates[:TOP_REVIEW_CANDIDATES])
    runner_up_score = candidates[1].score if len(candidates) > 1 else 0.0
    score_gap = result.score - runner_up_score
    approval_ready = (
        result.status == "review"
        and len(candidates) == 1
        and result.score >= 0.94
        and result.match_type in {"metadata-alias", "historical-alias", "exact-title", "dblp-local-csv-registry"}
    )
    evidence_summary = (
        f"{candidates[0].title} [{candidates[0].match_type} @ {candidates[0].score:.2f}]"
        if candidates
        else "no surviving constrained candidate"
    )
    row = {
        "booktitle": booktitle,
        "normalized_strict": forms.strict,
        "normalized_loose": forms.loose,
        "normalized_parent": forms.parent,
        "row_count": row_count,
        "status": result.status,
        "recommended_action": review_action_for_result(result),
        "match_stage": stage,
        "match_type": result.match_type,
        "best_score": f"{result.score:.2f}",
        "score_gap": f"{score_gap:.2f}",
        "top_candidate_margin": f"{score_gap:.2f}",
        "candidate_count_after_constraints": len(candidates),
        "series_key": series_key,
        "canonical_venue_id": stable_canonical_venue_id(series_key) if series_key else "",
        "evidence_source": result.evidence_source,
        "blocked_reason": build_blocked_reason(
            raw_name=booktitle,
            venue_type="conference",
            result=result,
            score_gap=score_gap,
            series_key=series_key,
        ),
        "bucket": classify_conference_review_bucket(booktitle, result, series_key=series_key),
        "evidence_summary": evidence_summary,
        "approval_ready": "yes" if approval_ready else "no",
    }

    for index in range(TOP_REVIEW_CANDIDATES):
        prefix = f"candidate_{index + 1}"
        if index < len(candidates):
            candidate = candidates[index]
            row[f"{prefix}_conf_id"] = candidate.venue_id
            row[f"{prefix}_title"] = candidate.title
            row[f"{prefix}_match_type"] = candidate.match_type
            row[f"{prefix}_stage"] = classify_match_stage(candidate.match_type)
            row[f"{prefix}_score"] = f"{candidate.score:.2f}"
            row[f"{prefix}_evidence_source"] = candidate.evidence_source
        else:
            row[f"{prefix}_conf_id"] = ""
            row[f"{prefix}_title"] = ""
            row[f"{prefix}_match_type"] = ""
            row[f"{prefix}_stage"] = ""
            row[f"{prefix}_score"] = ""
            row[f"{prefix}_evidence_source"] = ""
    return row


def write_review_csv(review_rows):
    fieldnames = [
        "booktitle",
        "normalized_strict",
        "normalized_loose",
        "normalized_parent",
        "row_count",
        "status",
        "recommended_action",
        "match_stage",
        "match_type",
        "best_score",
        "score_gap",
        "top_candidate_margin",
        "candidate_count_after_constraints",
        "series_key",
        "canonical_venue_id",
        "evidence_source",
        "blocked_reason",
        "bucket",
        "evidence_summary",
        "approval_ready",
    ]
    for index in range(TOP_REVIEW_CANDIDATES):
        prefix = f"candidate_{index + 1}"
        fieldnames.extend(
            [
                f"{prefix}_conf_id",
                f"{prefix}_title",
                f"{prefix}_match_type",
                f"{prefix}_stage",
                f"{prefix}_score",
                f"{prefix}_evidence_source",
            ]
        )

    with open(OUT_REVIEW, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in review_rows:
            writer.writerow(row)


def write_proposal_csv(review_rows):
    fieldnames = [
        "booktitle",
        "action",
        "conf_id",
        "conf_title",
        "conf_match_type",
        "match_stage",
        "score",
        "score_gap",
        "row_count",
        "series_key",
        "canonical_venue_id",
        "evidence_source",
        "blocked_reason",
        "bucket",
        "recommended_action",
    ]
    with open(OUT_PROPOSALS, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in review_rows:
            action = row["recommended_action"]
            writer.writerow(
                {
                    "booktitle": row["booktitle"],
                    "action": action,
                    "conf_id": row["candidate_1_conf_id"],
                    "conf_title": row["candidate_1_title"],
                    "conf_match_type": row["candidate_1_match_type"],
                    "match_stage": row["candidate_1_stage"] or row["match_stage"],
                    "score": row["candidate_1_score"] or row["best_score"],
                    "score_gap": row["score_gap"],
                    "row_count": row["row_count"],
                    "series_key": row["series_key"],
                    "canonical_venue_id": row["canonical_venue_id"],
                    "evidence_source": row["candidate_1_evidence_source"] or row["evidence_source"],
                    "blocked_reason": row["blocked_reason"],
                    "bucket": row["bucket"],
                    "recommended_action": action,
                }
            )


def _write_split_review_csv(path, fieldnames, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_split_proposal_csvs(review_rows):
    split_rows = split_conference_review_rows_by_action(review_rows)
    fieldnames = [
        "booktitle",
        "row_count",
        "bucket",
        "approval_ready",
        "series_key",
        "canonical_venue_id",
        "candidate_1_conf_id",
        "candidate_1_title",
        "candidate_1_match_type",
        "candidate_1_stage",
        "candidate_1_score",
        "top_candidate_margin",
        "candidate_count_after_constraints",
        "evidence_source",
        "evidence_summary",
        "blocked_reason",
    ]
    _write_split_review_csv(OUT_APPROVE_CANDIDATES, fieldnames, split_rows["approve"])
    _write_split_review_csv(OUT_LOOKUP_ENRICHMENT, fieldnames, split_rows["lookup_enrichment"])
    _write_split_review_csv(
        OUT_COLLISION_REVIEW_ONLY,
        fieldnames,
        split_rows["collision_review_only"],
    )


def write_generated_registry(series_registry, series_to_conf_id, records_by_id):
    fieldnames = [
        "canonical_venue_id",
        "series_key",
        "row_count",
        "distinct_booktitles",
        "dominant_booktitle",
        "representative_title",
        "acronyms",
        "top_booktitles",
        "dblp_keys",
        "current_conf_id",
        "current_conf_title",
        "recommended_action",
    ]
    with open(OUT_GENERATED_REGISTRY, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for series_key, payload in sorted(
            series_registry.items(),
            key=lambda item: (-item[1]["row_count"], item[0]),
        ):
            if payload["row_count"] < MIN_GENERATED_SERIES_ROWS:
                continue
            current_conf_id = series_to_conf_id.get(series_key)
            current_conf_title = (
                records_by_id[current_conf_id].title if current_conf_id in records_by_id else ""
            )
            if current_conf_id:
                continue
            writer.writerow(
                {
                    "canonical_venue_id": payload["canonical_venue_id"],
                    "series_key": series_key,
                    "row_count": payload["row_count"],
                    "distinct_booktitles": payload["distinct_booktitles"],
                    "dominant_booktitle": payload["dominant_booktitle"],
                    "representative_title": payload.get("representative_title", ""),
                    "acronyms": " | ".join(payload.get("acronyms", [])),
                    "top_booktitles": " | ".join(payload["top_booktitles"]),
                    "dblp_keys": " | ".join(payload.get("dblp_keys", [])),
                    "current_conf_id": "",
                    "current_conf_title": current_conf_title,
                    "recommended_action": "review",
                }
            )


def build_alias_review_records(review_rows):
    records = []
    for row in review_rows:
        evidence_payload = {
            "row_count": int(row.get("row_count") or 0),
            "match_stage": row.get("match_stage", ""),
            "match_type": row.get("match_type", ""),
            "blocked_reason": row.get("blocked_reason", ""),
            "bucket": row.get("bucket", ""),
            "series_key": row.get("series_key", ""),
            "canonical_venue_id": row.get("canonical_venue_id", ""),
            "candidates": [
                {
                    "conf_id": row.get(f"candidate_{index}_conf_id", ""),
                    "title": row.get(f"candidate_{index}_title", ""),
                    "match_type": row.get(f"candidate_{index}_match_type", ""),
                    "stage": row.get(f"candidate_{index}_stage", ""),
                    "score": row.get(f"candidate_{index}_score", ""),
                }
                for index in range(1, TOP_REVIEW_CANDIDATES + 1)
                if row.get(f"candidate_{index}_conf_id") or row.get(f"candidate_{index}_title")
            ],
        }
        records.append(
            {
                "venue_type": "conference",
                "raw_name": row.get("booktitle", ""),
                "normalized_name": row.get("normalized_strict", ""),
                "proposed_canonical_id": row.get("candidate_1_conf_id", ""),
                "proposed_canonical_title": row.get("candidate_1_title", ""),
                "score": row.get("best_score", ""),
                "match_type": row.get("match_type", ""),
                "evidence_json": json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True),
                "status": "pending",
                "reviewed_by": "",
                "reviewed_at": "",
            }
        )
    return records


def persist_series_backfills(backfill_updates):
    if not backfill_updates:
        return 0

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    updated_rows = 0
    try:
        for series_key, conf_id in sorted(backfill_updates.items(), key=lambda item: item[0]):
            cursor.execute(
                """
                UPDATE conferences
                SET dblp_key = %s
                WHERE conf_id = %s AND (dblp_key IS NULL OR dblp_key = '')
                """,
                (series_key, conf_id),
            )
            updated_rows += cursor.rowcount
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return updated_rows


def persist_series_corrections(series_corrections):
    if not series_corrections:
        return 0

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    corrected_rows = 0
    try:
        for series_key, (old_conf_id, new_conf_id) in sorted(series_corrections.items(), key=lambda item: item[0]):
            cursor.execute(
                "UPDATE conferences SET dblp_key = NULL WHERE conf_id = %s AND dblp_key = %s",
                (old_conf_id, series_key),
            )
            corrected_rows += cursor.rowcount
            cursor.execute(
                """
                UPDATE conferences
                SET dblp_key = %s
                WHERE conf_id = %s AND (dblp_key IS NULL OR dblp_key = '')
                """,
                (series_key, new_conf_id),
            )
            corrected_rows += cursor.rowcount
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return corrected_rows


def write_unresolved_reports(unresolved_titles, booktitle_counts):
    unresolved_rows = sum(booktitle_counts[title] for title in unresolved_titles)

    with open(OUT_UNMATCHED, "w", encoding="utf-8") as handle:
        handle.write(f"# {len(unresolved_titles)} unresolved conference booktitles\n")
        handle.write(f"# {unresolved_rows} unresolved conference rows\n")
        handle.write("# Review the companion CSV for ranked candidate suggestions.\n\n")
        for title in unresolved_titles:
            handle.write(f"{title}\n")

    with open(OUT_UNMATCHED_BY_ROWS, "w", encoding="utf-8") as handle:
        handle.write(f"# {len(unresolved_titles)} unresolved booktitles sorted by row frequency\n")
        handle.write(f"# {unresolved_rows} unresolved conference rows\n")
        handle.write("# rows\tbooktitle\n\n")
        for title, rows in sorted(
            ((title, booktitle_counts[title]) for title in unresolved_titles),
            key=lambda item: (-item[1], item[0].casefold()),
        ):
            handle.write(f"{rows}\t{title}\n")

    return unresolved_rows


def match_with_local_dblp_registry(result, booktitle, series_key, series_to_conf_id, records_by_id):
    if result.status == "matched":
        return result
    if not series_key:
        return result

    conf_id = series_to_conf_id.get(series_key)
    if conf_id is None:
        return result

    record = records_by_id[conf_id]
    candidate = MatchCandidate(
        conf_id,
        record.title,
        "dblp-local-csv-registry",
        0.96,
        "dblp_local_csv",
    )
    return MatchResult(
        "matched",
        conf_id,
        "dblp-local-csv-registry",
        0.96,
        "high",
        "dblp_local_csv",
        (candidate,),
    )


def reconcile_series_mappings(matcher, series_to_conf_id, series_registry):
    reconciled = dict(series_to_conf_id)
    corrected = {}
    for series_key, conf_id in series_to_conf_id.items():
        payload = series_registry.get(series_key)
        query = (payload.get("representative_title") or payload.get("dominant_booktitle")) if payload else ""
        if not query:
            continue
        local_result = matcher.match(query)
        if (
            local_result.status == "matched"
            and local_result.venue_id is not None
            and local_result.venue_id != conf_id
            and local_result.match_type in {"manual-match", "exact-title", "exact-acronym", "segment-acronym"}
            and local_result.score >= 0.98
        ):
            reconciled[series_key] = local_result.venue_id
            corrected[series_key] = (conf_id, local_result.venue_id)
    return reconciled, corrected


def infer_series_mappings_from_registry(matcher, series_to_conf_id, series_registry):
    inferred = {}
    for series_key, payload in series_registry.items():
        if series_key in series_to_conf_id:
            continue
        if int(payload.get("row_count", 0) or 0) < 5:
            continue
        queries = [
            payload.get("representative_title", ""),
            payload.get("dominant_booktitle", ""),
            *(payload.get("top_booktitles", []) or [])[:3],
        ]
        for query in queries:
            if not query:
                continue
            local_result = matcher.match(query)
            if accepted_series_bridge(local_result):
                inferred[series_key] = local_result.venue_id
                break
    return inferred


def main():
    local_dblp_index = load_local_dblp_csv_index()
    seed_review_metadata = load_seed_review_metadata()

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    inserted_seed_rows = ensure_seed_venues(cursor)
    if inserted_seed_rows:
        conn.commit()

    conference_rows = fetch_conference_rows(cursor)
    cursor.close()
    conn.close()

    records = [
        VenueRecord(
            conf_id,
            title or "",
            acronym or "",
            build_conference_metadata(dblp_key or "", local_dblp_index),
        )
        for conf_id, acronym, title, dblp_key in conference_rows
    ]
    records_by_id = {record.venue_id: record for record in records}
    valid_conf_ids = set(records_by_id)
    by_acronym = {
        compact_key(record.acronym): record.venue_id
        for record in records
        if compact_key(record.acronym)
    }
    by_title = {
        clean_for_match(record.title): record.venue_id
        for record in records
        if clean_for_match(record.title)
    }
    series_to_conf_id = {
        str(record.metadata.get("dblp_key")): record.venue_id
        for record in records
        if record.metadata.get("dblp_key")
    }

    persistent_aliases = load_persistent_venue_aliases(OUT_ALIAS_MEMORY, "conference")
    manual_overrides = {
        **persistent_aliases,
        **load_manual_overrides(valid_conf_ids, by_acronym, by_title),
    }
    conflict_acronyms = load_conflict_acronyms()
    matcher = VenueMatcher(
        venue_type="conference",
        records=records,
        manual_overrides=manual_overrides,
        conflict_acronyms=conflict_acronyms,
    )
    normalizer = VenueNormalizer("conference")

    booktitle_counts, cleaned_series_by_booktitle = load_booktitle_context()
    series_registry = dict(local_dblp_index.get("conference_series_registry", {}))
    series_by_booktitle = dict(cleaned_series_by_booktitle)
    series_by_booktitle.update(local_dblp_index.get("conference_series_by_booktitle", {}))

    series_to_conf_id, corrected_series_to_conf_id = reconcile_series_mappings(
        matcher,
        series_to_conf_id,
        series_registry,
    )
    inferred_series_to_conf_id = infer_series_mappings_from_registry(
        matcher,
        series_to_conf_id,
        series_registry,
    )
    series_to_conf_id.update(inferred_series_to_conf_id)
    booktitles = sorted(booktitle_counts, key=lambda title: (-booktitle_counts[title], title.casefold()))
    total_rows = sum(booktitle_counts.values())

    print(f"Distinct booktitles to match: {len(booktitles):,}")
    print(f"Conference paper rows to classify: {total_rows:,}")
    print(f"Manual conference overrides loaded: {len(manual_overrides):,}")
    print(f"Seed conference rows inserted this run: {inserted_seed_rows:,}")
    print(
        f"Local DBLP conference CSV: {local_dblp_index.get('inputs', {}).get('inproceedings', {}).get('path', '')}"
    )
    print(
        "Local DBLP conference columns: "
        + ", ".join(local_dblp_index.get("inputs", {}).get("inproceedings", {}).get("columns", []))
    )
    print(f"DBLP series mappings available after registry load: {len(series_to_conf_id):,}")
    print(f"DBLP series mappings corrected from local evidence: {len(corrected_series_to_conf_id):,}")
    print(f"DBLP series mappings inferred from local CSV registry: {len(inferred_series_to_conf_id):,}")

    matched_distinct = 0
    matched_rows = 0
    unresolved_titles = []
    skipped_titles = []
    review_rows = []
    resolved_review_rows = []
    audit_rows = []
    match_counts = defaultdict(int)
    match_rows = defaultdict(int)
    stage_counts = defaultdict(int)
    stage_rows = defaultdict(int)
    backfill_updates = dict(inferred_series_to_conf_id)
    run_date = date.today().isoformat()

    with open(OUT_MAPPING, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "booktitle",
                "conf_id",
                "canonical_venue_id",
                "series_key",
                "match_stage",
                "match_type",
                "evidence_source",
                "confidence",
                "score",
            ]
        )

        for booktitle in booktitles:
            row_count = booktitle_counts[booktitle]
            series_key = series_by_booktitle.get(booktitle, "")
            if not series_key:
                series_key = resolve_series_from_local_dblp(
                    booktitle,
                    local_dblp_index,
                    normalizer,
                    conflict_acronyms,
                )
            result = matcher.match(booktitle)
            result = match_with_local_dblp_registry(
                result,
                booktitle,
                series_key,
                series_to_conf_id,
                records_by_id,
            )
            match_stage = classify_match_stage(result.match_type)
            canonical_venue_id = stable_canonical_venue_id(series_key) if series_key else ""
            evidence_payload = {
                "status": result.status,
                "confidence": result.confidence,
                "series_key": series_key,
                "canonical_venue_id": canonical_venue_id,
                "evidence_source": result.evidence_source,
                "candidates": [
                    {
                        "conf_id": candidate.venue_id,
                        "title": candidate.title,
                        "match_type": candidate.match_type,
                        "score": round(candidate.score, 2),
                        "evidence_source": candidate.evidence_source,
                    }
                    for candidate in result.candidates[:TOP_REVIEW_CANDIDATES]
                ],
            }
            audit_rows.append(
                {
                    "venue_type": "conference",
                    "raw_name": booktitle,
                    "normalized_name": normalizer.normalize(booktitle).strict,
                    "canonical_id": result.venue_id or "",
                    "stage": match_stage,
                    "match_type": result.match_type if result.match_type else result.status,
                    "score": f"{result.score:.2f}",
                    "run_date": run_date,
                    "evidence_json": json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True),
                }
            )

            if result.status == "matched" and result.venue_id is not None:
                record = records_by_id.get(result.venue_id)
                resolved_review_rows.append(
                    {
                        "venue_type": "conference",
                        "raw_name": booktitle,
                        "normalized_name": normalizer.normalize(booktitle).strict,
                        "proposed_canonical_id": result.venue_id,
                        "proposed_canonical_title": record.title if record is not None else "",
                        "reviewed_by": seed_review_metadata.get(series_key, {}).get("reviewed_by", ""),
                        "reviewed_at": seed_review_metadata.get(series_key, {}).get("reviewed_at", ""),
                    }
                )
                writer.writerow(
                    [
                        booktitle,
                        result.venue_id,
                        canonical_venue_id,
                        series_key,
                        match_stage,
                        result.match_type,
                        result.evidence_source,
                        result.confidence,
                        f"{result.score:.2f}",
                    ]
                )
                matched_distinct += 1
                matched_rows += row_count
                match_counts[result.match_type] += 1
                match_rows[result.match_type] += row_count
                stage_counts[match_stage] += 1
                stage_rows[match_stage] += row_count
                if should_backfill_series_key(result, series_key, records_by_id):
                    backfill_updates[series_key] = result.venue_id
                    series_to_conf_id[series_key] = result.venue_id
                    record = records_by_id[result.venue_id]
                    record.metadata["dblp_key"] = series_key
                continue

            match_type = result.match_type if result.match_type else result.status
            writer.writerow(
                [
                    booktitle,
                    "",
                    canonical_venue_id,
                    series_key,
                    match_stage,
                    match_type,
                    result.evidence_source,
                    result.confidence,
                    f"{result.score:.2f}",
                ]
            )

            if result.status in {"non_venue", "skipped"}:
                skipped_titles.append(booktitle)
                match_counts[match_type] += 1
                match_rows[match_type] += row_count
                stage_counts[match_stage] += 1
                stage_rows[match_stage] += row_count
                continue

            unresolved_titles.append(booktitle)
            review_rows.append(build_review_row(booktitle, row_count, series_key, result, normalizer))
            match_counts[result.status] += 1
            match_rows[result.status] += row_count

    unresolved_rows = write_unresolved_reports(unresolved_titles, booktitle_counts)
    write_review_csv(review_rows)
    write_proposal_csv(review_rows)
    write_split_proposal_csvs(review_rows)
    write_generated_registry(series_registry, series_to_conf_id, records_by_id)
    upsert_alias_review_records(OUT_ALIAS_REVIEW, build_alias_review_records(review_rows))
    synced_approved_rows = sync_alias_review_statuses(
        OUT_ALIAS_REVIEW,
        OUT_ALIAS_MEMORY,
        reviewed_at=run_date,
    )
    synced_resolved_rows = sync_review_statuses_from_resolved_rows(
        OUT_ALIAS_REVIEW,
        resolved_review_rows,
        reviewed_at=run_date,
    )
    write_match_audit_rows(OUT_MATCH_AUDIT, audit_rows)
    updated_series_rows = persist_series_backfills(backfill_updates)
    corrected_series_rows = persist_series_corrections(corrected_series_to_conf_id)

    pct_distinct = (matched_distinct / len(booktitles) * 100) if booktitles else 0.0
    pct_rows = (matched_rows / total_rows * 100) if total_rows else 0.0

    print(
        f"Matched distinct booktitles: {matched_distinct:,} / {len(booktitles):,} ({pct_distinct:.1f}%)"
    )
    print(
        f"Matched conference rows: {matched_rows:,} / {total_rows:,} ({pct_rows:.1f}%)"
    )
    print(f"Unresolved distinct booktitles: {len(unresolved_titles):,}")
    print(f"Unresolved conference rows: {unresolved_rows:,}")
    print(f"Skipped/non-venue booktitles: {len(skipped_titles):,}")
    print(f"Conference rows backfilled with DBLP series keys: {updated_series_rows:,}")
    print(f"Conference rows corrected for stale DBLP series keys: {corrected_series_rows:,}")
    print("Breakdown by match outcome:")
    for key in sorted(match_counts):
        print(f"  {key}: {match_counts[key]:,} distinct / {match_rows[key]:,} rows")
    print("Breakdown by match stage:")
    for key in sorted(stage_counts):
        print(f"  {key}: {stage_counts[key]:,} distinct / {stage_rows[key]:,} rows")

    print("Top unresolved booktitles by row count:")
    for title, rows in sorted(
        ((title, booktitle_counts[title]) for title in unresolved_titles),
        key=lambda item: (-item[1], item[0].casefold()),
    )[:20]:
        print(console_safe(f"  {rows:,}  {title}"))

    print(f"Mapping:            {OUT_MAPPING}")
    print(f"Unresolved:         {OUT_UNMATCHED}")
    print(f"Unresolved by rows: {OUT_UNMATCHED_BY_ROWS}")
    print(f"Review CSV:         {OUT_REVIEW}")
    print(f"Proposal CSV:       {OUT_PROPOSALS}")
    print(f"Approve CSV:        {OUT_APPROVE_CANDIDATES}")
    print(f"Enrichment CSV:     {OUT_LOOKUP_ENRICHMENT}")
    print(f"Collision CSV:      {OUT_COLLISION_REVIEW_ONLY}")
    print(f"Generated registry: {OUT_GENERATED_REGISTRY}")
    print(f"Review memory:      {OUT_ALIAS_REVIEW}")
    print(f"Alias memory:       {OUT_ALIAS_MEMORY}")
    print(f"Approval sync rows: {synced_approved_rows:,}")
    print(f"Resolved sync rows: {synced_resolved_rows:,}")
    print(f"Match audit:        {OUT_MATCH_AUDIT}")
    print(f"Manual aliases:     {MANUAL_ALIAS_CSV}")


if __name__ == "__main__":
    print("Matching DBLP booktitles to conferences table...")
    main()
