"""
Expected ETL validation counts for the canonical cleaned-input snapshot.

These counts reflect the checked project data after DB-01 and DB-02 work.
If the cleaned inputs or curated matching outputs intentionally change,
update this file together with a fresh validation run.
"""

BASELINE_DATE = "2026-05-22"

EXPECTED_COUNTS = {
    "papers_total": 2_525_752,
    "papers_conference": 1_413_090,
    "papers_journal": 1_112_662,
    "authors": 1_403_305,
    "paper_authors": 7_050_510,
    "matched_conference_rows": 1_103_466,
    "unmatched_conference_rows": 309_624,
    "matched_journal_rows": 1_038_304,
    "unmatched_journal_rows": 74_358,
}

