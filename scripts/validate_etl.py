"""
Repeatable post-load validation for the bibliographic ETL.

Run from the repo root:
    python scripts/validate_etl.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import mysql.connector


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "etl"))
from config import DB_CONFIG  # noqa: E402
from validation_baseline import BASELINE_DATE, EXPECTED_COUNTS  # noqa: E402


@dataclass
class DbSnapshot:
    papers_total: int
    papers_conference: int
    papers_journal: int
    authors_total: int
    paper_authors_total: int
    unmatched_conference_rows: int
    matched_conference_rows: int
    unmatched_journal_rows: int
    matched_journal_rows: int
    has_unique_type_raw_id_guard: bool
    duplicate_paper_rows: int
    has_fk_pa_paper: bool
    has_fk_pa_author: bool


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def fetch_scalar(cursor, query: str) -> int:
    cursor.execute(query)
    return int(cursor.fetchone()[0] or 0)


def fetch_db_snapshot() -> DbSnapshot:
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    papers_total = fetch_scalar(cursor, "SELECT COUNT(*) FROM papers")
    papers_conference = fetch_scalar(
        cursor,
        "SELECT COUNT(*) FROM papers WHERE type = 'conference'",
    )
    papers_journal = fetch_scalar(
        cursor,
        "SELECT COUNT(*) FROM papers WHERE type = 'journal'",
    )
    authors_total = fetch_scalar(cursor, "SELECT COUNT(*) FROM authors")
    paper_authors_total = fetch_scalar(cursor, "SELECT COUNT(*) FROM paper_authors")
    unmatched_conference_rows = fetch_scalar(
        cursor,
        "SELECT COUNT(*) FROM papers WHERE type = 'conference' AND conf_id IS NULL",
    )
    matched_conference_rows = fetch_scalar(
        cursor,
        "SELECT COUNT(*) FROM papers WHERE type = 'conference' AND conf_id IS NOT NULL",
    )
    unmatched_journal_rows = fetch_scalar(
        cursor,
        "SELECT COUNT(*) FROM papers WHERE type = 'journal' AND journal_id IS NULL",
    )
    matched_journal_rows = fetch_scalar(
        cursor,
        "SELECT COUNT(*) FROM papers WHERE type = 'journal' AND journal_id IS NOT NULL",
    )
    cursor.execute(
        """
        SELECT index_name, seq_in_index, column_name
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'papers'
          AND non_unique = 0
        ORDER BY index_name, seq_in_index
        """
    )
    unique_indexes: dict[str, list[str]] = {}
    for index_name, _seq_in_index, column_name in cursor.fetchall():
        unique_indexes.setdefault(index_name, []).append(column_name)

    has_unique_type_raw_id_guard = any(
        columns == ["type", "raw_id"]
        for columns in unique_indexes.values()
    )
    duplicate_paper_rows = 0
    if has_unique_type_raw_id_guard:
        cursor.execute(
            """
            SELECT COALESCE(SUM(cnt - 1), 0)
            FROM (
                SELECT COUNT(*) AS cnt
                FROM papers
                WHERE raw_id IS NOT NULL
                GROUP BY type, raw_id
                HAVING COUNT(*) > 1
            ) AS dup_groups
            """
        )
        duplicate_paper_rows = int(cursor.fetchone()[0] or 0)

    cursor.execute(
        """
        SELECT column_name, referenced_table_name, referenced_column_name
        FROM information_schema.key_column_usage
        WHERE table_schema = DATABASE()
          AND table_name = 'paper_authors'
          AND referenced_table_name IS NOT NULL
        """
    )
    fk_rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return DbSnapshot(
        papers_total=papers_total,
        papers_conference=papers_conference,
        papers_journal=papers_journal,
        authors_total=authors_total,
        paper_authors_total=paper_authors_total,
        unmatched_conference_rows=unmatched_conference_rows,
        matched_conference_rows=matched_conference_rows,
        unmatched_journal_rows=unmatched_journal_rows,
        matched_journal_rows=matched_journal_rows,
        has_unique_type_raw_id_guard=has_unique_type_raw_id_guard,
        duplicate_paper_rows=duplicate_paper_rows,
        has_fk_pa_paper=any(
            column_name == "paper_id"
            and referenced_table_name == "papers"
            and referenced_column_name == "paper_id"
            for column_name, referenced_table_name, referenced_column_name in fk_rows
        ),
        has_fk_pa_author=any(
            column_name == "author_id"
            and referenced_table_name == "authors"
            and referenced_column_name == "author_id"
            for column_name, referenced_table_name, referenced_column_name in fk_rows
        ),
    )


def build_check_results(db: DbSnapshot) -> list[CheckResult]:
    checks = [
        CheckResult(
            name="papers UNIQUE(type, raw_id) guard exists",
            ok=db.has_unique_type_raw_id_guard,
            detail="present" if db.has_unique_type_raw_id_guard else "missing",
        ),
        CheckResult(
            name="duplicate papers(type, raw_id)",
            ok=db.has_unique_type_raw_id_guard and db.duplicate_paper_rows == 0,
            detail=(
                "0 duplicate rows (schema guard + data verified)"
                if db.has_unique_type_raw_id_guard and db.duplicate_paper_rows == 0
                else f"{db.duplicate_paper_rows} duplicate rows found"
                if db.has_unique_type_raw_id_guard
                else "cannot be trusted: UNIQUE(type, raw_id) guard is missing"
            ),
        ),
        CheckResult(
            name="orphan paper_authors.paper_id links",
            ok=db.has_fk_pa_paper,
            detail=(
                "FK paper_authors.paper_id -> papers.paper_id present"
                if db.has_fk_pa_paper else
                "foreign key missing"
            ),
        ),
        CheckResult(
            name="orphan paper_authors.author_id links",
            ok=db.has_fk_pa_author,
            detail=(
                "FK paper_authors.author_id -> authors.author_id present"
                if db.has_fk_pa_author else
                "foreign key missing"
            ),
        ),
    ]

    metric_map = {
        "papers_total": db.papers_total,
        "papers_conference": db.papers_conference,
        "papers_journal": db.papers_journal,
        "authors": db.authors_total,
        "paper_authors": db.paper_authors_total,
        "matched_conference_rows": db.matched_conference_rows,
        "unmatched_conference_rows": db.unmatched_conference_rows,
        "matched_journal_rows": db.matched_journal_rows,
        "unmatched_journal_rows": db.unmatched_journal_rows,
    }

    labels = {
        "papers_total": "total paper row count",
        "papers_conference": "conference paper row count",
        "papers_journal": "journal paper row count",
        "authors": "author count vs cleaned distinct authors",
        "paper_authors": "paper_authors count vs cleaned unique pairs",
        "matched_conference_rows": "matched conference venue rows",
        "unmatched_conference_rows": "unmatched conference venue rows",
        "matched_journal_rows": "matched journal venue rows",
        "unmatched_journal_rows": "unmatched journal venue rows",
    }

    for key, expected in EXPECTED_COUNTS.items():
        actual = metric_map[key]
        checks.append(
            CheckResult(
                name=labels[key],
                ok=actual == expected,
                detail=f"db={actual:,}, expected={expected:,}",
            )
        )

    return checks


def print_baseline() -> None:
    print(f"Validation baseline date: {BASELINE_DATE}")
    print("Expected counts")
    for key, value in EXPECTED_COUNTS.items():
        print(f"  {key:27} {value:,}")
    print()


def print_db_snapshot(db: DbSnapshot) -> None:
    print("Database snapshot")
    print(f"  papers_total                 {db.papers_total:,}")
    print(f"  papers_conference            {db.papers_conference:,}")
    print(f"  papers_journal               {db.papers_journal:,}")
    print(f"  authors                      {db.authors_total:,}")
    print(f"  paper_authors                {db.paper_authors_total:,}")
    print(f"  matched_conference_rows      {db.matched_conference_rows:,}")
    print(f"  unmatched_conference_rows    {db.unmatched_conference_rows:,}")
    print(f"  matched_journal_rows         {db.matched_journal_rows:,}")
    print(f"  unmatched_journal_rows       {db.unmatched_journal_rows:,}")
    print(
        "  unique(type, raw_id) guard   "
        + ("present" if db.has_unique_type_raw_id_guard else "missing")
    )
    print(
        "  fk paper_authors.paper_id    "
        + ("present" if db.has_fk_pa_paper else "missing")
    )
    print(
        "  fk paper_authors.author_id   "
        + ("present" if db.has_fk_pa_author else "missing")
    )
    print()


def print_check_results(results: list[CheckResult]) -> None:
    print("Checks")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"  [{status}] {result.name}: {result.detail}")
    print()


def main() -> int:
    print_baseline()
    print("Collecting live database snapshot...")
    db = fetch_db_snapshot()
    results = build_check_results(db)
    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed

    print()
    print_db_snapshot(db)
    print_check_results(results)
    print(f"Validation summary: {passed} passed, {failed} failed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
