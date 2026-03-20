"""
Repeatable benchmark for DB-07 search/profile index verification.

Run from the repo root:
    python scripts/benchmark_search_profile.py
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mysql.connector


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "etl"))
from config import DB_CONFIG  # noqa: E402


RUNS = 5
PAGE_SIZE = 50
SEARCH_LIMIT = 15


@dataclass(frozen=True)
class VenueFixture:
    venue_id: int
    label: str
    min_year: int
    max_year: int
    start_year: int
    end_year: int
    total_papers: int


@dataclass(frozen=True)
class AuthorFixture:
    author_id: int
    name: str
    total_papers: int
    search_term: str
    boolean_expr: str


@dataclass(frozen=True)
class SearchFixture:
    term: str
    boolean_expr: str


@dataclass(frozen=True)
class QueryCase:
    name: str
    sql: str
    params: tuple[Any, ...]
    explain_sql: str | None = None
    explain_params: tuple[Any, ...] | None = None


@dataclass
class QueryRun:
    name: str
    ok: bool
    timings_ms: list[float]
    median_ms: float | None
    row_count: int | None
    fingerprint: str
    error: str | None
    explain_rows: list[dict[str, Any]]


def connect():
    return mysql.connector.connect(**DB_CONFIG)


def fetch_one(cursor, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    cursor.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"Query returned no rows: {query}")
    return row


def fetch_all(cursor, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    cursor.execute(query, params)
    return list(cursor.fetchall())


def to_fingerprint(value: Any) -> str:
    payload = json.dumps(value, default=str, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def benchmark_query(
    conn,
    case: QueryCase,
    runs: int = RUNS,
) -> QueryRun:
    timings_ms: list[float] = []
    last_rows: list[tuple[Any, ...]] | None = None
    error: str | None = None
    ok = True

    for _ in range(runs):
        cursor = conn.cursor()
        started = time.perf_counter()
        try:
            cursor.execute(case.sql, case.params)
            rows = list(cursor.fetchall())
        except Exception as exc:  # pragma: no cover - benchmark error path
            ok = False
            error = str(exc)
            rows = []
        finally:
            timings_ms.append(round((time.perf_counter() - started) * 1000, 2))
            cursor.close()

        last_rows = rows
        if not ok:
            break

    explain_rows: list[dict[str, Any]] = []
    if case.explain_sql is not None and ok:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(case.explain_sql, case.explain_params or case.params)
            explain_rows = list(cursor.fetchall())
        finally:
            cursor.close()

    row_count = len(last_rows or []) if ok else None
    fingerprint_source: Any = last_rows if ok else {"error": error}

    return QueryRun(
        name=case.name,
        ok=ok,
        timings_ms=timings_ms,
        median_ms=round(statistics.median(timings_ms), 2) if ok else None,
        row_count=row_count,
        fingerprint=to_fingerprint(fingerprint_source),
        error=error,
        explain_rows=explain_rows,
    )


def pick_conference_fixture(cursor) -> VenueFixture:
    venue_id, label, min_year, max_year, total_papers = fetch_one(
        cursor,
        """
        SELECT
            p.conf_id,
            c.acronym,
            MIN(p.year) AS min_year,
            MAX(p.year) AS max_year,
            COUNT(*) AS total_papers
        FROM papers p
        JOIN conferences c ON c.conf_id = p.conf_id
        WHERE p.conf_id IS NOT NULL
        GROUP BY p.conf_id, c.acronym
        ORDER BY total_papers DESC, p.conf_id ASC
        LIMIT 1
        """,
    )
    start_year = max(int(min_year), int(max_year) - 5)
    end_year = int(max_year)
    return VenueFixture(
        venue_id=int(venue_id),
        label=str(label),
        min_year=int(min_year),
        max_year=int(max_year),
        start_year=start_year,
        end_year=end_year,
        total_papers=int(total_papers),
    )


def pick_journal_fixture(cursor) -> VenueFixture:
    venue_id, label, min_year, max_year, total_papers = fetch_one(
        cursor,
        """
        SELECT
            p.journal_id,
            j.title,
            MIN(p.year) AS min_year,
            MAX(p.year) AS max_year,
            COUNT(*) AS total_papers
        FROM papers p
        JOIN journals j ON j.journal_id = p.journal_id
        WHERE p.journal_id IS NOT NULL
        GROUP BY p.journal_id, j.title
        ORDER BY total_papers DESC, p.journal_id ASC
        LIMIT 1
        """,
    )
    start_year = max(int(min_year), int(max_year) - 5)
    end_year = int(max_year)
    return VenueFixture(
        venue_id=int(venue_id),
        label=str(label),
        min_year=int(min_year),
        max_year=int(max_year),
        start_year=start_year,
        end_year=end_year,
        total_papers=int(total_papers),
    )


def pick_author_fixture(cursor) -> AuthorFixture:
    author_id, name, total_papers = fetch_one(
        cursor,
        """
        SELECT
            pa.author_id,
            a.name,
            COUNT(*) AS total_papers
        FROM paper_authors pa
        JOIN authors a ON a.author_id = pa.author_id
        GROUP BY pa.author_id, a.name
        ORDER BY total_papers DESC, pa.author_id ASC
        LIMIT 1
        """,
    )

    tokens = re.findall(r"\w+", str(name), flags=re.UNICODE)
    term = next((token.lower() for token in tokens if len(token) > 3), str(name).lower())
    return AuthorFixture(
        author_id=int(author_id),
        name=str(name),
        total_papers=int(total_papers),
        search_term=term,
        boolean_expr=f"+{term}*",
    )


def pick_search_term(
    cursor,
    table_name: str,
    preferred_terms: list[str],
    like_columns: list[str],
    fallback_query: str,
) -> SearchFixture:
    for term in preferred_terms:
        where_clause = " OR ".join(f"{column} LIKE %s" for column in like_columns)
        row = fetch_one(
            cursor,
            f"SELECT COUNT(*) FROM {table_name} WHERE {where_clause}",
            tuple(f"%{term}%" for _ in like_columns),
        )
        if int(row[0]) > 0:
            return SearchFixture(term=term, boolean_expr=f"+{term}*")

    fallback_row = fetch_one(cursor, fallback_query)
    text = " ".join(str(value) for value in fallback_row if value)
    tokens = [token.lower() for token in re.findall(r"\w+", text, flags=re.UNICODE)]
    term = next((token for token in tokens if len(token) > 3), tokens[0])
    return SearchFixture(term=term, boolean_expr=f"+{term}*")


def load_index_map(cursor) -> dict[tuple[str, str], list[tuple[str, str]]]:
    cursor.execute(
        """
        SELECT
            table_name,
            index_name,
            index_type,
            seq_in_index,
            column_name
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name IN ('authors', 'journals', 'conferences', 'papers')
        ORDER BY table_name, index_name, seq_in_index
        """
    )
    rows = cursor.fetchall()
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for table_name, index_name, index_type, _seq_in_index, column_name in rows:
        grouped.setdefault((str(table_name), str(index_name)), []).append(
            (str(index_type), str(column_name))
        )
    return grouped


def find_equivalent_indexes(
    index_map: dict[tuple[str, str], list[tuple[str, str]]],
    table_name: str,
    index_type: str,
    columns: tuple[str, ...],
) -> list[str]:
    matches: list[str] = []
    for (candidate_table, index_name), parts in index_map.items():
        if candidate_table != table_name:
            continue
        if not parts:
            continue
        candidate_type = parts[0][0]
        candidate_columns = tuple(column_name for _type, column_name in parts)
        if candidate_type == index_type and candidate_columns == columns:
            matches.append(index_name)
    return matches


def print_index_snapshot(index_map: dict[tuple[str, str], list[tuple[str, str]]]) -> None:
    print("Required index coverage")
    requirements = [
        ("authors", "FULLTEXT", ("name",), "author search"),
        ("journals", "FULLTEXT", ("title",), "journal search"),
        ("conferences", "FULLTEXT", ("title", "acronym"), "conference search"),
        ("papers", "BTREE", ("conf_id", "year"), "conference profile filters"),
        ("papers", "BTREE", ("journal_id", "year"), "journal profile filters"),
    ]
    for table_name, index_type, columns, label in requirements:
        matches = find_equivalent_indexes(index_map, table_name, index_type, columns)
        status = "present" if matches else "missing"
        names = ", ".join(matches) if matches else "-"
        print(
            f"  {label:28} {status:7} {table_name}({', '.join(columns)}) [{index_type}] names={names}"
        )
    print()


def print_environment(cursor) -> None:
    version, version_comment = fetch_one(
        cursor,
        "SELECT VERSION(), @@version_comment",
    )
    print("Database engine")
    print(f"  version          {version}")
    print(f"  version_comment  {version_comment}")
    print()


def print_fixtures(
    conference: VenueFixture,
    journal: VenueFixture,
    author: AuthorFixture,
    conf_search: SearchFixture,
    journal_search: SearchFixture,
) -> None:
    print("Benchmark fixtures")
    print(
        "  conference       "
        f"id={conference.venue_id}, label={conference.label}, papers={conference.total_papers:,}, "
        f"range={conference.start_year}-{conference.end_year} (full={conference.min_year}-{conference.max_year})"
    )
    print(
        "  journal          "
        f"id={journal.venue_id}, label={journal.label}, papers={journal.total_papers:,}, "
        f"range={journal.start_year}-{journal.end_year} (full={journal.min_year}-{journal.max_year})"
    )
    print(
        "  author           "
        f"id={author.author_id}, name={author.name}, papers={author.total_papers:,}, search_term={author.search_term}"
    )
    print(f"  conf_search      term={conf_search.term}")
    print(f"  journal_search   term={journal_search.term}")
    print()


def print_query_runs(runs: list[QueryRun]) -> None:
    print("Benchmarks")
    for run in runs:
        if not run.ok:
            print(f"  [FAIL] {run.name}")
            print(f"         error={run.error}")
            print(f"         timings_ms={run.timings_ms}")
            print(f"         fingerprint={run.fingerprint}")
            continue

        timings = ", ".join(f"{timing:.2f}" for timing in run.timings_ms)
        print(f"  [PASS] {run.name}")
        print(f"         timings_ms=[{timings}]")
        print(f"         median_ms={run.median_ms:.2f}")
        print(f"         row_count={run.row_count}")
        print(f"         fingerprint={run.fingerprint}")
        if run.explain_rows:
            for explain in run.explain_rows:
                key = explain.get("key")
                rows = explain.get("rows")
                extra = explain.get("Extra")
                select_type = explain.get("select_type")
                table = explain.get("table")
                print(
                    "         "
                    f"EXPLAIN select_type={select_type} table={table} key={key} rows={rows} extra={extra}"
                )
    print()


def build_query_cases(
    conference: VenueFixture,
    journal: VenueFixture,
    author: AuthorFixture,
    conf_search: SearchFixture,
    journal_search: SearchFixture,
) -> list[QueryCase]:
    return [
        QueryCase(
            name="conference_papers_count",
            sql=(
                "SELECT COUNT(*) AS total "
                "FROM papers WHERE conf_id = %s AND year >= %s AND year <= %s"
            ),
            params=(conference.venue_id, conference.start_year, conference.end_year),
            explain_sql=(
                "EXPLAIN SELECT COUNT(*) AS total "
                "FROM papers WHERE conf_id = %s AND year >= %s AND year <= %s"
            ),
        ),
        QueryCase(
            name="conference_papers_page",
            sql=(
                "SELECT paper_id, title, year, pages, ee, url "
                "FROM papers WHERE conf_id = %s AND year >= %s AND year <= %s "
                "ORDER BY year DESC LIMIT %s OFFSET 0"
            ),
            params=(
                conference.venue_id,
                conference.start_year,
                conference.end_year,
                PAGE_SIZE,
            ),
            explain_sql=(
                "EXPLAIN SELECT paper_id, title, year, pages, ee, url "
                "FROM papers WHERE conf_id = %s AND year >= %s AND year <= %s "
                "ORDER BY year DESC LIMIT %s OFFSET 0"
            ),
        ),
        QueryCase(
            name="conference_yearly_stats",
            sql=(
                "SELECT * FROM vw_conf_yearly_stats "
                "WHERE conf_id = %s ORDER BY year ASC"
            ),
            params=(conference.venue_id,),
            explain_sql=(
                "EXPLAIN SELECT * FROM vw_conf_yearly_stats "
                "WHERE conf_id = %s ORDER BY year ASC"
            ),
        ),
        QueryCase(
            name="journal_papers_count",
            sql=(
                "SELECT COUNT(*) AS total "
                "FROM papers WHERE journal_id = %s AND year >= %s AND year <= %s"
            ),
            params=(journal.venue_id, journal.start_year, journal.end_year),
            explain_sql=(
                "EXPLAIN SELECT COUNT(*) AS total "
                "FROM papers WHERE journal_id = %s AND year >= %s AND year <= %s"
            ),
        ),
        QueryCase(
            name="journal_papers_page",
            sql=(
                "SELECT paper_id, title, year, volume, number, pages, ee, url "
                "FROM papers WHERE journal_id = %s AND year >= %s AND year <= %s "
                "ORDER BY year DESC LIMIT %s OFFSET 0"
            ),
            params=(
                journal.venue_id,
                journal.start_year,
                journal.end_year,
                PAGE_SIZE,
            ),
            explain_sql=(
                "EXPLAIN SELECT paper_id, title, year, volume, number, pages, ee, url "
                "FROM papers WHERE journal_id = %s AND year >= %s AND year <= %s "
                "ORDER BY year DESC LIMIT %s OFFSET 0"
            ),
        ),
        QueryCase(
            name="journal_yearly_stats",
            sql=(
                "SELECT * FROM vw_journal_yearly_stats "
                "WHERE journal_id = %s ORDER BY year ASC"
            ),
            params=(journal.venue_id,),
            explain_sql=(
                "EXPLAIN SELECT * FROM vw_journal_yearly_stats "
                "WHERE journal_id = %s ORDER BY year ASC"
            ),
        ),
        QueryCase(
            name="author_search_match",
            sql=(
                "SELECT author_id, name FROM authors "
                "WHERE MATCH(name) AGAINST(%s IN BOOLEAN MODE) "
                "ORDER BY name LIMIT %s"
            ),
            params=(author.boolean_expr, SEARCH_LIMIT),
        ),
        QueryCase(
            name="conference_search_match",
            sql=(
                "SELECT conf_id, title, acronym FROM conferences "
                "WHERE MATCH(title, acronym) AGAINST(%s IN BOOLEAN MODE) "
                "ORDER BY acronym LIMIT %s"
            ),
            params=(conf_search.boolean_expr, SEARCH_LIMIT),
        ),
        QueryCase(
            name="conference_search_like_sanity",
            sql=(
                "SELECT conf_id, title, acronym FROM conferences "
                "WHERE acronym LIKE %s OR title LIKE %s "
                "ORDER BY acronym LIMIT %s"
            ),
            params=(f"%{conf_search.term}%", f"%{conf_search.term}%", SEARCH_LIMIT),
        ),
        QueryCase(
            name="journal_search_match",
            sql=(
                "SELECT journal_id, title FROM journals "
                "WHERE MATCH(title) AGAINST(%s IN BOOLEAN MODE) "
                "ORDER BY title LIMIT %s"
            ),
            params=(journal_search.boolean_expr, SEARCH_LIMIT),
        ),
        QueryCase(
            name="journal_search_like_sanity",
            sql=(
                "SELECT journal_id, title FROM journals "
                "WHERE title LIKE %s ORDER BY title LIMIT %s"
            ),
            params=(f"%{journal_search.term}%", SEARCH_LIMIT),
        ),
    ]


def main() -> int:
    conn = connect()
    try:
        cursor = conn.cursor()
        print_environment(cursor)

        index_map = load_index_map(cursor)
        print_index_snapshot(index_map)

        conference = pick_conference_fixture(cursor)
        journal = pick_journal_fixture(cursor)
        author = pick_author_fixture(cursor)
        conf_search = pick_search_term(
            cursor,
            table_name="conferences",
            preferred_terms=["science", "data", "systems"],
            like_columns=["title", "acronym"],
            fallback_query="SELECT title, acronym FROM conferences ORDER BY conf_id ASC LIMIT 1",
        )
        journal_search = pick_search_term(
            cursor,
            table_name="journals",
            preferred_terms=["nature", "medicine", "review"],
            like_columns=["title"],
            fallback_query="SELECT title FROM journals ORDER BY journal_id ASC LIMIT 1",
        )
        cursor.close()

        print_fixtures(conference, journal, author, conf_search, journal_search)

        query_cases = build_query_cases(
            conference=conference,
            journal=journal,
            author=author,
            conf_search=conf_search,
            journal_search=journal_search,
        )
        runs = [benchmark_query(conn, case) for case in query_cases]
        print_query_runs(runs)

        failures = [run for run in runs if not run.ok]
        print(
            f"Summary: {len(runs) - len(failures)} passed, {len(failures)} failed benchmark/query checks"
        )
        return 0 if not failures else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
