import csv
from pathlib import Path
from uuid import uuid4

import pytest

from etl.venue_matching_report import (
    build_high_confidence_proposals,
    build_manual_review_batch_summary,
    build_manual_seed_batch_summary,
    build_regression_rows,
    enforce_regression_zero_growth,
)
from etl.venue_matching import compute_mapping_metrics


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_compute_mapping_metrics_uses_same_filters_for_distinct_and_row_counts():
    tmp_dir = Path(".tmp") / f"venue-report-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    conference_input = tmp_dir / "cleaned_inproceedings.csv"
    conference_map = tmp_dir / "booktitle_to_conf_id.csv"
    journal_input = tmp_dir / "cleaned_articles.csv"
    journal_map = tmp_dir / "journal_name_to_id.csv"

    write_csv(
        conference_input,
        ["booktitle"],
        [
            {"booktitle": "Conf A"},
            {"booktitle": "Conf A"},
            {"booktitle": "Conf B"},
            {"booktitle": "Conf C"},
            {"booktitle": "Conf C"},
            {"booktitle": "Conf D"},
        ],
    )
    write_csv(
        conference_map,
        ["booktitle", "conf_id", "match_type", "score"],
        [
            {"booktitle": "Conf A", "conf_id": "10", "match_type": "manual-match", "score": "1.00"},
            {"booktitle": "Conf B", "conf_id": "", "match_type": "unmatched", "score": "0.00"},
            {"booktitle": "Conf C", "conf_id": "", "match_type": "manual-skip", "score": "1.00"},
            {"booktitle": "Conf D", "conf_id": "11", "match_type": "dblp-local-csv-registry", "score": "0.97"},
        ],
    )
    write_csv(
        journal_input,
        ["journal"],
        [
            {"journal": "Jour A"},
            {"journal": "Jour A"},
            {"journal": "Jour B"},
            {"journal": "Jour C"},
        ],
    )
    write_csv(
        journal_map,
        ["dblp_journal_name", "journal_id", "match_type", "confidence"],
        [
            {"dblp_journal_name": "Jour A", "journal_id": "20", "match_type": "manual-match", "confidence": "high"},
            {"dblp_journal_name": "Jour B", "journal_id": "", "match_type": "unmatched", "confidence": "unmatched"},
            {"dblp_journal_name": "Jour C", "journal_id": "", "match_type": "manual-non-venue", "confidence": "non_venue"},
        ],
    )

    metrics = compute_mapping_metrics(
        conference_input_csv=conference_input,
        conference_mapping_csv=conference_map,
        journal_input_csv=journal_input,
        journal_mapping_csv=journal_map,
    )

    assert metrics["conference"]["matched_distinct"] == 2
    assert metrics["conference"]["unmatched_distinct"] == 1
    assert metrics["conference"]["unmatched_rows"] == 1
    assert metrics["conference"]["stage_counts"]["manual aliases"]["distinct"] == 1
    assert metrics["conference"]["stage_counts"]["DBLP local CSV registry"]["rows"] == 1
    assert metrics["conference"]["stage_counts"]["explicit skip / non_venue"]["distinct"] == 1

    assert metrics["journal"]["matched_distinct"] == 1
    assert metrics["journal"]["unmatched_distinct"] == 1
    assert metrics["journal"]["skipped_or_non_venue_rows"] == 1
    assert metrics["journal"]["stage_counts"]["manual aliases"]["rows"] == 2
    assert metrics["journal"]["stage_counts"]["explicit skip / non_venue"]["distinct"] == 1


def test_build_regression_rows_includes_current_review_context():
    baseline_rows = {
        "CARS": {"booktitle": "CARS", "conf_id": "123", "match_type": "manual-match"},
    }
    current_rows = {
        "CARS": {
            "booktitle": "CARS",
            "conf_id": "",
            "match_stage": "exact normalized local match",
            "match_type": "metadata-alias",
            "score": "0.96",
        }
    }
    review_rows = {
        "CARS": {
            "booktitle": "CARS",
            "normalized_strict": "cars",
            "candidate_1_conf_id": "373",
            "candidate_1_title": "European Dependable Computing Conference",
            "match_stage": "exact normalized local match",
            "match_type": "metadata-alias",
            "best_score": "0.96",
            "blocked_reason": "blocked by low margin between top candidates",
        }
    }
    title_lookup = {"123": "Computer Assisted Radiology and Surgery"}

    rows = build_regression_rows(
        baseline_rows=baseline_rows,
        current_rows=current_rows,
        review_rows=review_rows,
        title_lookup=title_lookup,
    )

    assert rows == [
        {
            "booktitle": "CARS",
            "normalized_booktitle": "cars",
            "previous_conf_id": "123",
            "previous_matched_title": "Computer Assisted Radiology and Surgery",
            "current_best_candidate": "European Dependable Computing Conference",
            "current_stage": "exact normalized local match",
            "current_match_type": "metadata-alias",
            "current_score": "0.96",
            "reason_blocked": "blocked by low margin between top candidates",
            "justification": "",
        }
    ]


def test_build_high_confidence_proposals_combines_conference_and_journal_reviews():
    conference_rows = [
        {
            "booktitle": "IEEE SCC",
            "normalized_strict": "ieee scc",
            "row_count": "928",
            "match_stage": "exact normalized local match",
            "match_type": "metadata-alias",
            "best_score": "0.96",
            "blocked_reason": "blocked by low margin between top candidates",
            "candidate_1_conf_id": "1148",
            "candidate_1_title": "IEEE Conference on Computational Complexity",
        },
        {
            "booktitle": "Bildverarbeitung für die Medizin",
            "normalized_strict": "bildverarbeitung fur die medizin",
            "row_count": "1490",
            "match_stage": "conservative fuzzy fallback",
            "match_type": "unmatched",
            "best_score": "0.00",
            "blocked_reason": "no conservative candidate found",
            "candidate_1_conf_id": "",
            "candidate_1_title": "",
        },
    ]
    journal_rows = [
        {
            "dblp_journal_name": "JCM",
            "normalized_strict": "jcm",
            "row_count": "516",
            "match_stage": "exact normalized local match",
            "match_type": "derived-acronym",
            "best_score": "0.90",
            "blocked_reason": "blocked by low margin between top candidates",
            "candidate_1_journal_id": "9178",
            "candidate_1_title": "Journal of Cardiovascular Medicine",
        }
    ]

    proposals = build_high_confidence_proposals(conference_rows, journal_rows)

    assert proposals == [
        {
            "venue_type": "conference",
            "raw_name": "IEEE SCC",
            "normalized_name": "ieee scc",
            "row_count": "928",
            "proposed_canonical_id": "1148",
            "proposed_canonical_title": "IEEE Conference on Computational Complexity",
            "score": "0.96",
            "match_type": "metadata-alias",
            "evidence_stage": "exact normalized local match",
            "blocked_reason": "blocked by low margin between top candidates",
        },
        {
            "venue_type": "journal",
            "raw_name": "JCM",
            "normalized_name": "jcm",
            "row_count": "516",
            "proposed_canonical_id": "9178",
            "proposed_canonical_title": "Journal of Cardiovascular Medicine",
            "score": "0.90",
            "match_type": "derived-acronym",
            "evidence_stage": "exact normalized local match",
            "blocked_reason": "blocked by low margin between top candidates",
        },
    ]


def test_enforce_regression_zero_growth_raises_when_unexplained_regressions_increase():
    previous_rows = [
        {"booktitle": "Legacy Conf", "justification": ""},
    ]
    current_rows = [
        {"booktitle": "Legacy Conf", "justification": ""},
        {"booktitle": "New Conf", "justification": ""},
    ]

    with pytest.raises(ValueError, match="unexplained conference regressions increased"):
        enforce_regression_zero_growth(previous_rows, current_rows)


def test_enforce_regression_zero_growth_allows_justified_new_regressions():
    previous_rows = [
        {"booktitle": "Legacy Conf", "justification": ""},
    ]
    current_rows = [
        {"booktitle": "Legacy Conf", "justification": ""},
        {"booktitle": "New Conf", "justification": "Lookup row intentionally removed; replacement pending"},
    ]

    enforce_regression_zero_growth(previous_rows, current_rows)


def test_build_manual_seed_batch_summary_counts_seed_rows_and_review_memory_approvals():
    tmp_dir = Path(".tmp") / f"venue-report-batch-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    seed_path = tmp_dir / "conference_seed_venues.csv"
    review_path = tmp_dir / "venue_alias_review.csv"

    write_csv(
        seed_path,
        ["title", "acronym", "dblp_key", "notes", "date_added", "added_by"],
        [
            {
                "title": "ICNSC",
                "acronym": "ICNSC",
                "dblp_key": "conf/icnsc",
                "notes": "seeded",
                "date_added": "2026-03-31",
                "added_by": "manual-reviewed-seed-batch-2",
            },
            {
                "title": "ICUMT",
                "acronym": "ICUMT",
                "dblp_key": "conf/icumt",
                "notes": "seeded",
                "date_added": "2026-03-31",
                "added_by": "manual-reviewed-seed-batch-2",
            },
        ],
    )
    write_csv(
        review_path,
        [
            "id",
            "venue_type",
            "raw_name",
            "normalized_name",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_json",
            "status",
            "reviewed_by",
            "reviewed_at",
        ],
        [
            {
                "id": "1",
                "venue_type": "conference",
                "raw_name": "ICNSC",
                "normalized_name": "icnsc",
                "proposed_canonical_id": "2001",
                "proposed_canonical_title": "ICNSC",
                "score": "0.00",
                "match_type": "unmatched",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-seed-batch-2",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "2",
                "venue_type": "conference",
                "raw_name": "ICUMT Workshops",
                "normalized_name": "icumt workshops",
                "proposed_canonical_id": "2002",
                "proposed_canonical_title": "ICUMT",
                "score": "0.00",
                "match_type": "unmatched",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-seed-batch-2",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "3",
                "venue_type": "journal",
                "raw_name": "JCP",
                "normalized_name": "jcp",
                "proposed_canonical_id": "7928",
                "proposed_canonical_title": "Journal of Cancer Policy",
                "score": "0.90",
                "match_type": "derived-acronym",
                "evidence_json": "{}",
                "status": "pending",
                "reviewed_by": "",
                "reviewed_at": "",
            },
        ],
    )

    summary = build_manual_seed_batch_summary(
        seed_path=seed_path,
        review_path=review_path,
        reviewed_by="manual-reviewed-seed-batch-2",
        reviewed_at="2026-03-31",
    )

    assert summary["seed_rows_added"] == 2
    assert summary["approved_review_rows"] == 2
    assert summary["seed_titles"] == ["ICNSC", "ICUMT"]
    assert summary["approved_raw_names"] == ["ICNSC", "ICUMT Workshops"]


def test_build_manual_seed_batch_summary_prefers_latest_manual_review_batch():
    tmp_dir = Path(".tmp") / f"venue-report-latest-batch-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    seed_path = tmp_dir / "conference_seed_venues.csv"
    review_path = tmp_dir / "venue_alias_review.csv"

    write_csv(
        seed_path,
        ["title", "acronym", "dblp_key", "notes", "date_added", "added_by"],
        [
            {
                "title": "ICNSC",
                "acronym": "ICNSC",
                "dblp_key": "conf/icnsc",
                "notes": "seeded",
                "date_added": "2026-03-31",
                "added_by": "manual-reviewed-seed-batch-2",
            },
            {
                "title": "EUROMICRO",
                "acronym": "EUROMICRO",
                "dblp_key": "conf/euromicro",
                "notes": "seeded",
                "date_added": "2026-03-31",
                "added_by": "manual-reviewed-seed-batch-3",
            },
        ],
    )
    write_csv(
        review_path,
        [
            "id",
            "venue_type",
            "raw_name",
            "normalized_name",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_json",
            "status",
            "reviewed_by",
            "reviewed_at",
        ],
        [
            {
                "id": "1",
                "venue_type": "conference",
                "raw_name": "ICNSC",
                "normalized_name": "icnsc",
                "proposed_canonical_id": "2001",
                "proposed_canonical_title": "ICNSC",
                "score": "0.00",
                "match_type": "unmatched",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-seed-batch-2",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "2",
                "venue_type": "conference",
                "raw_name": "EUROMICRO",
                "normalized_name": "euromicro",
                "proposed_canonical_id": "3001",
                "proposed_canonical_title": "EUROMICRO",
                "score": "0.00",
                "match_type": "unmatched",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-seed-batch-3",
                "reviewed_at": "2026-03-31",
            },
        ],
    )

    summary = build_manual_seed_batch_summary(
        seed_path=seed_path,
        review_path=review_path,
    )

    assert summary["reviewed_by"] == "manual-reviewed-seed-batch-3"
    assert summary["reviewed_at"] == "2026-03-31"
    assert summary["seed_rows_added"] == 1
    assert summary["approved_review_rows"] == 1
    assert summary["seed_titles"] == ["EUROMICRO"]
    assert summary["approved_raw_names"] == ["EUROMICRO"]


def test_build_manual_review_batch_summary_counts_latest_journal_batch():
    tmp_dir = Path(".tmp") / f"venue-report-journal-batch-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    write_csv(
        review_path,
        [
            "id",
            "venue_type",
            "raw_name",
            "normalized_name",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_json",
            "status",
            "reviewed_by",
            "reviewed_at",
        ],
        [
            {
                "id": "1",
                "venue_type": "journal",
                "raw_name": "Software - Concepts and Tools",
                "normalized_name": "software concepts and tools",
                "proposed_canonical_id": "10723",
                "proposed_canonical_title": "IET Software",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-1",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "2",
                "venue_type": "journal",
                "raw_name": "iJET",
                "normalized_name": "ijet",
                "proposed_canonical_id": "8537",
                "proposed_canonical_title": "International Journal of Emerging Technologies in Learning",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-1",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "3",
                "venue_type": "journal",
                "raw_name": "JCP",
                "normalized_name": "jcp",
                "proposed_canonical_id": "7928",
                "proposed_canonical_title": "Journal of Cancer Policy",
                "score": "0.90",
                "match_type": "derived-acronym",
                "evidence_json": "{}",
                "status": "pending",
                "reviewed_by": "",
                "reviewed_at": "",
            },
            {
                "id": "4",
                "venue_type": "conference",
                "raw_name": "EUROMICRO",
                "normalized_name": "euromicro",
                "proposed_canonical_id": "3001",
                "proposed_canonical_title": "EUROMICRO",
                "score": "0.00",
                "match_type": "unmatched",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-seed-batch-3",
                "reviewed_at": "2026-03-31",
            },
        ],
    )

    summary = build_manual_review_batch_summary(
        review_path=review_path,
        venue_type="journal",
    )

    assert summary["reviewed_by"] == "manual-reviewed-journal-batch-1"
    assert summary["reviewed_at"] == "2026-03-31"
    assert summary["approved_review_rows"] == 2
    assert summary["approved_raw_names"] == ["Software - Concepts and Tools", "iJET"]


def test_build_manual_review_batch_summary_prefers_latest_journal_batch_number():
    tmp_dir = Path(".tmp") / f"venue-report-journal-batch-latest-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    write_csv(
        review_path,
        [
            "id",
            "venue_type",
            "raw_name",
            "normalized_name",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_json",
            "status",
            "reviewed_by",
            "reviewed_at",
        ],
        [
            {
                "id": "1",
                "venue_type": "journal",
                "raw_name": "iJET",
                "normalized_name": "ijet",
                "proposed_canonical_id": "8537",
                "proposed_canonical_title": "International Journal of Emerging Technologies in Learning",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-1",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "2",
                "venue_type": "journal",
                "raw_name": "IJDMB",
                "normalized_name": "ijdmb",
                "proposed_canonical_id": "16531",
                "proposed_canonical_title": "International Journal of Data Mining and Bioinformatics",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-2",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "3",
                "venue_type": "journal",
                "raw_name": "IJEBR",
                "normalized_name": "ijebr",
                "proposed_canonical_id": "14304",
                "proposed_canonical_title": "International Journal of e-Business Research",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-2",
                "reviewed_at": "2026-03-31",
            },
        ],
    )

    summary = build_manual_review_batch_summary(
        review_path=review_path,
        venue_type="journal",
    )

    assert summary["reviewed_by"] == "manual-reviewed-journal-batch-2"
    assert summary["reviewed_at"] == "2026-03-31"
    assert summary["approved_review_rows"] == 2
    assert summary["approved_raw_names"] == ["IJDMB", "IJEBR"]


def test_build_manual_review_batch_summary_prefers_batch_3_journal_review_rows():
    tmp_dir = Path(".tmp") / f"venue-report-journal-batch-3-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    write_csv(
        review_path,
        [
            "id",
            "venue_type",
            "raw_name",
            "normalized_name",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_json",
            "status",
            "reviewed_by",
            "reviewed_at",
        ],
        [
            {
                "id": "1",
                "venue_type": "journal",
                "raw_name": "IJDMB",
                "normalized_name": "ijdmb",
                "proposed_canonical_id": "16531",
                "proposed_canonical_title": "International Journal of Data Mining and Bioinformatics",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-2",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "2",
                "venue_type": "journal",
                "raw_name": "JTAER",
                "normalized_name": "jtaer",
                "proposed_canonical_id": "5637",
                "proposed_canonical_title": "Journal of Theoretical and Applied Electronic Commerce Research",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-3",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "3",
                "venue_type": "journal",
                "raw_name": "ITC",
                "normalized_name": "itc",
                "proposed_canonical_id": "12080",
                "proposed_canonical_title": "Information Technology and Control",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-3",
                "reviewed_at": "2026-03-31",
            },
        ],
    )

    summary = build_manual_review_batch_summary(
        review_path=review_path,
        venue_type="journal",
    )

    assert summary["reviewed_by"] == "manual-reviewed-journal-batch-3"
    assert summary["reviewed_at"] == "2026-03-31"
    assert summary["approved_review_rows"] == 2
    assert summary["approved_raw_names"] == ["ITC", "JTAER"]


def test_build_manual_review_batch_summary_supports_journal_catalog_batches():
    tmp_dir = Path(".tmp") / f"venue-report-journal-catalog-batch-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    write_csv(
        review_path,
        [
            "id",
            "venue_type",
            "raw_name",
            "normalized_name",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_json",
            "status",
            "reviewed_by",
            "reviewed_at",
        ],
        [
            {
                "id": "1",
                "venue_type": "journal",
                "raw_name": "Intelligent Tutoring Media",
                "normalized_name": "intelligent tutoring media",
                "proposed_canonical_id": "13242",
                "proposed_canonical_title": "Digital Creativity",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-catalog-batch-1",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "2",
                "venue_type": "journal",
                "raw_name": "Transactions of the SDPS",
                "normalized_name": "transactions of the sdps",
                "proposed_canonical_id": "15484",
                "proposed_canonical_title": "Journal of Integrated Design and Process Science",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-catalog-batch-1",
                "reviewed_at": "2026-03-31",
            },
        ],
    )

    summary = build_manual_review_batch_summary(
        review_path=review_path,
        venue_type="journal",
    )

    assert summary["reviewed_by"] == "manual-reviewed-journal-catalog-batch-1"
    assert summary["reviewed_at"] == "2026-03-31"
    assert summary["approved_review_rows"] == 2
    assert summary["approved_raw_names"] == [
        "Intelligent Tutoring Media",
        "Transactions of the SDPS",
    ]


def test_build_manual_review_batch_summary_prefers_catalog_batch_on_same_date():
    tmp_dir = Path(".tmp") / f"venue-report-journal-catalog-priority-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    write_csv(
        review_path,
        [
            "id",
            "venue_type",
            "raw_name",
            "normalized_name",
            "proposed_canonical_id",
            "proposed_canonical_title",
            "score",
            "match_type",
            "evidence_json",
            "status",
            "reviewed_by",
            "reviewed_at",
        ],
        [
            {
                "id": "1",
                "venue_type": "journal",
                "raw_name": "JTAER",
                "normalized_name": "jtaer",
                "proposed_canonical_id": "5637",
                "proposed_canonical_title": "Journal of Theoretical and Applied Electronic Commerce Research",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-batch-3",
                "reviewed_at": "2026-03-31",
            },
            {
                "id": "2",
                "venue_type": "journal",
                "raw_name": "Intelligent Tutoring Media",
                "normalized_name": "intelligent tutoring media",
                "proposed_canonical_id": "13242",
                "proposed_canonical_title": "Digital Creativity",
                "score": "1.00",
                "match_type": "manual-match",
                "evidence_json": "{}",
                "status": "approved",
                "reviewed_by": "manual-reviewed-journal-catalog-batch-1",
                "reviewed_at": "2026-03-31",
            },
        ],
    )

    summary = build_manual_review_batch_summary(
        review_path=review_path,
        venue_type="journal",
    )

    assert summary["reviewed_by"] == "manual-reviewed-journal-catalog-batch-1"
    assert summary["reviewed_at"] == "2026-03-31"
    assert summary["approved_review_rows"] == 1
    assert summary["approved_raw_names"] == ["Intelligent Tutoring Media"]
