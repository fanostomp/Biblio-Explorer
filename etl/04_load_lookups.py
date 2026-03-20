"""
04_load_lookups.py
------------------
Loads lookup tables into biblio_db:
  1. primary_for       (from icoreCategories.xlsx)
  2. best_subject_area (from bestSubjectArea.csv)
  3. conferences       (from iCore26_KilledColumnsForLoading.csv)
  4. journals          (from journal_ranking_data_raw.csv)
  5. authors           (from cleaned author files)

Conference loading supports:
  - diff-only sync against the current conferences table
  - dry-run mode
  - conference-only execution
  - duplicate/ambiguity diagnostics written to
    data/matched/conference_source_conflicts.csv

Examples:
  python etl/04_load_lookups.py
  python etl/04_load_lookups.py --only conferences
  python etl/04_load_lookups.py --only conferences --dry-run
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

import mysql.connector
import openpyxl

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG, ICORE_CSV, ICORE_CATS_XLSX, JOURNAL_RANK_CSV, BEST_AREA_CSV

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CLEANED_DIR = os.path.join(DATA_DIR, "cleaned")
MATCHED_DIR = os.path.join(DATA_DIR, "matched")
CONFERENCE_CONFLICTS_CSV = os.path.join(MATCHED_DIR, "conference_source_conflicts.csv")

VALID_CONFERENCE_RANKS = {"A*", "A", "B", "C"}
RANK_PRIORITY = {"A*": 4, "A": 3, "B": 2, "C": 1, None: 0}
TITLE_ABBREVIATION_RULES = (
    (re.compile(r"\bint'l\b", re.IGNORECASE), "international"),
    (re.compile(r"\bintl\.?\b", re.IGNORECASE), "international"),
    (re.compile(r"\bint\.?\b", re.IGNORECASE), "international"),
    (re.compile(r"\bconfs\.?\b", re.IGNORECASE), "conferences"),
    (re.compile(r"\bconf\.?\b", re.IGNORECASE), "conference"),
    (re.compile(r"\bsymp\.?\b", re.IGNORECASE), "symposium"),
    (re.compile(r"\bproc\.?\b", re.IGNORECASE), "proceedings"),
)
NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
WHITESPACE_RE = re.compile(r"\s+")
_ALLOWED_TABLES = frozenset(
    {"primary_for", "best_subject_area", "conferences", "journals", "authors", "papers", "paper_authors"}
)


def get_conn():
    return mysql.connector.connect(**DB_CONFIG)


def get_table_row_count(cursor, table_name):
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"get_table_row_count: disallowed table name: {table_name!r}")
    cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
    return cursor.fetchone()[0]


def parse_args():
    parser = argparse.ArgumentParser(description="Load lookup tables into biblio_db.")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["primary_for", "best_subject_area", "conferences", "journals", "authors"],
        help="Run only the specified stages.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute the selected stages but roll back DB changes before exit.",
    )
    return parser.parse_args()


def normalize_area_name(name):
    return name.strip().strip('"').strip()


def normalize_conference_title_fingerprint(title):
    """
    Normalize lightweight title spelling variants so duplicates like
    'Intl. Geoscience...' and 'International Geoscience...' collapse
    to the same fingerprint.
    """
    text = title.casefold().replace("&", " and ")
    for pattern, replacement in TITLE_ABBREVIATION_RULES:
        text = pattern.sub(replacement, text)
    text = NON_ALNUM_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def title_value(row):
    return (row.get(" Title") or row.get("Title") or "").strip()


def clean_conference_rank(rank):
    rank = (rank or "").strip()
    return rank if rank in VALID_CONFERENCE_RANKS else None


def clean_primary_for(for_code, valid_for_codes):
    value = (for_code or "").strip()
    if not value:
        return None
    return value if value in valid_for_codes else None


def conference_winner_key(row):
    """Sort key for canonical conference row selection within one acronym group."""
    return (
        RANK_PRIORITY.get(row["rank"], 0),
        1 if row["primary_for"] else 0,
        len(row["title"]),
        -row["line_no"],  # Negate so max() prefers the last source row on a full tie.
    )


def exact_conference_source_key(row):
    return (row["title"], row["rank"], row["primary_for"])


def classify_conference_acronym_group(rows):
    deduped = {}
    exact_duplicate_rows = 0
    for row in rows:
        exact_key = exact_conference_source_key(row)
        if exact_key in deduped:
            exact_duplicate_rows += 1
            continue
        deduped[exact_key] = row

    unique_rows = sorted(deduped.values(), key=lambda item: item["line_no"])
    fingerprints = {row["title_key"] for row in unique_rows}

    if len(rows) == 1:
        classification = "single-row-acronym"
    elif len(fingerprints) > 1:
        classification = "ambiguous-acronym-collision"
    elif len(unique_rows) > 1:
        classification = "same-normalized-title-variant"
    else:
        classification = "exact-duplicate-only"

    return {
        "classification": classification,
        "unique_rows": unique_rows,
        "exact_duplicate_rows": exact_duplicate_rows,
        "exact_duplicate_group": len(unique_rows) < len(rows),
        # The current pipeline keeps acronym as the conference identity key.
        # Only same-title variants are eligible for canonicalization; distinct
        # normalized titles under one acronym are treated as ambiguous and skipped.
        "canonical_row": None if len(fingerprints) > 1 else max(unique_rows, key=conference_winner_key),
    }


def write_conference_conflict_report(conflicts):
    os.makedirs(MATCHED_DIR, exist_ok=True)
    with open(CONFERENCE_CONFLICTS_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "status",
                "acronym",
                "line_no",
                "title",
                "normalized_title",
                "rank",
                "primary_for",
            ]
        )
        for acronym, rows in sorted(conflicts.items()):
            for row in rows:
                writer.writerow(
                    [
                        "ambiguous-acronym-collision",
                        acronym,
                        row["line_no"],
                        row["title"],
                        row["title_key"],
                        row["rank"] or "",
                        row["primary_for"] or "",
                    ]
                )


def load_primary_for(cursor):
    print("Loading primary_for...")
    before_count = get_table_row_count(cursor, "primary_for")
    wb = openpyxl.load_workbook(ICORE_CATS_XLSX, read_only=True, data_only=True)
    ws = wb.active
    rows = iter(ws.rows)
    next(rows)

    inserted = 0
    valid_rows = 0
    skipped_rows = 0
    for row in rows:
        code = str(row[0].value).strip() if row[0].value is not None else None
        desc = str(row[1].value).strip() if row[1].value is not None else None
        if not code or not desc:
            skipped_rows += 1
            continue
        valid_rows += 1
        cursor.execute(
            "INSERT IGNORE INTO primary_for (for_code, description) VALUES (%s, %s)",
            (code, desc),
        )
        inserted += cursor.rowcount
    wb.close()
    final_count = get_table_row_count(cursor, "primary_for")
    ignored_rows = valid_rows - inserted
    print(f"  valid source rows: {valid_rows:,}")
    print(f"  skipped malformed rows: {skipped_rows:,}")
    print(f"  inserted rows: {inserted:,}")
    print(f"  ignored as duplicates/already present: {ignored_rows:,}")
    print(f"  final primary_for rows: {final_count:,} (was {before_count:,})")


def load_best_subject_area(cursor):
    print("Loading best_subject_area...")
    before_count = get_table_row_count(cursor, "best_subject_area")
    inserted = 0
    valid_rows = 0
    skipped_rows = 0
    with open(BEST_AREA_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or not row[0].strip():
                skipped_rows += 1
                continue
            valid_rows += 1
            name = row[0].strip().strip('"')
            cursor.execute(
                "INSERT IGNORE INTO best_subject_area (area_name) VALUES (%s)",
                (name,),
            )
            inserted += cursor.rowcount
    final_count = get_table_row_count(cursor, "best_subject_area")
    ignored_rows = valid_rows - inserted
    print(f"  valid source rows: {valid_rows:,}")
    print(f"  skipped malformed rows: {skipped_rows:,}")
    print(f"  inserted rows: {inserted:,}")
    print(f"  ignored as duplicates/already present: {ignored_rows:,}")
    print(f"  final best_subject_area rows: {final_count:,} (was {before_count:,})")


def load_conferences(cursor, dry_run=False):
    print("Syncing conferences...")
    before_count = get_table_row_count(cursor, "conferences")

    cursor.execute("SELECT for_code FROM primary_for")
    valid_for_codes = {row[0] for row in cursor.fetchall()}

    source_rows = []
    source_rows_skipped = 0
    invalid_primary_for_rows = 0
    with open(ICORE_CSV, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, raw_row in enumerate(reader, start=2):
            acronym = (raw_row.get("Acronym") or "").strip()
            title = title_value(raw_row)
            if not acronym or not title:
                source_rows_skipped += 1
                continue

            raw_primary_for = (raw_row.get("PrimaryFoR") or "").strip()
            primary_for = clean_primary_for(raw_primary_for, valid_for_codes)
            if raw_primary_for and primary_for is None:
                invalid_primary_for_rows += 1

            source_rows.append(
                {
                    "line_no": line_no,
                    "acronym": acronym,
                    "title": title,
                    "title_key": normalize_conference_title_fingerprint(title),
                    "rank": clean_conference_rank(raw_row.get("Rank", "")),
                    "primary_for": primary_for,
                }
            )

    by_acronym = defaultdict(list)
    for row in source_rows:
        by_acronym[row["acronym"]].append(row)

    single_row_acronyms = 0
    repeated_acronym_groups = 0
    exact_duplicate_rows = 0
    exact_duplicate_groups = 0
    same_normalized_title_variant_groups = 0
    canonical_rows = []
    conflicts = {}

    for acronym, rows in by_acronym.items():
        if len(rows) == 1:
            single_row_acronyms += 1
        else:
            repeated_acronym_groups += 1

        group = classify_conference_acronym_group(rows)
        exact_duplicate_rows += group["exact_duplicate_rows"]
        if group["exact_duplicate_group"]:
            exact_duplicate_groups += 1

        if group["classification"] == "ambiguous-acronym-collision":
            conflicts[acronym] = group["unique_rows"]
            continue

        if group["classification"] == "same-normalized-title-variant":
            same_normalized_title_variant_groups += 1

        canonical_rows.append(group["canonical_row"])

    write_conference_conflict_report(conflicts)

    source_acronyms = set(by_acronym)
    insertable_acronyms = {row["acronym"] for row in canonical_rows}
    ambiguous_acronyms = set(conflicts)

    cursor.execute("SELECT conf_id, acronym FROM conferences")
    existing_rows = cursor.fetchall()
    existing_acronyms = {acronym for _conf_id, acronym in existing_rows if acronym}

    rows_to_insert = [
        row
        for row in sorted(canonical_rows, key=lambda item: item["acronym"].casefold())
        if row["acronym"] not in existing_acronyms
    ]

    inserted_rows = 0
    if rows_to_insert:
        cursor.executemany(
            """
            INSERT INTO conferences (title, acronym, rank, primary_for)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (row["title"], row["acronym"], row["rank"], row["primary_for"])
                for row in rows_to_insert
            ],
        )
        inserted_rows = cursor.rowcount

    final_db_acronyms = set(existing_acronyms)
    final_db_acronyms.update(row["acronym"] for row in rows_to_insert)
    final_count = get_table_row_count(cursor, "conferences")

    missing_after_sync = sorted(source_acronyms - final_db_acronyms)
    missing_unambiguous = sorted(insertable_acronyms - final_db_acronyms)

    verb = "would insert" if dry_run else "inserted"
    print(f"  source rows: {len(source_rows):,} (skipped blanks: {source_rows_skipped:,})")
    print(f"  distinct source acronyms: {len(source_acronyms):,}")
    print(f"  single-row acronym groups: {single_row_acronyms:,}")
    print(f"  repeated acronym groups: {repeated_acronym_groups:,}")
    print(f"  exact duplicate rows collapsed: {exact_duplicate_rows:,}")
    print(f"  exact duplicate groups: {exact_duplicate_groups:,}")
    print(f"  same-normalized-title variant groups collapsed: {same_normalized_title_variant_groups:,}")
    print(f"  ambiguous acronym groups skipped intentionally: {len(ambiguous_acronyms):,}")
    print(f"  rows with invalid PrimaryFoR cleared to NULL: {invalid_primary_for_rows:,}")
    print(f"  existing conference acronyms before sync: {len(existing_acronyms):,}")
    print(f"  {verb}: {inserted_rows:,}")
    print(f"  final conference rows after sync: {final_count:,} (was {before_count:,})")
    print(f"  source acronyms still missing after sync: {len(missing_after_sync):,}")
    print(f"  unambiguous source acronyms still missing: {len(missing_unambiguous):,}")
    print(
        "  conflict report: "
        f"{CONFERENCE_CONFLICTS_CSV} (ambiguous acronym collisions intentionally not auto-merged)"
    )

    if missing_unambiguous:
        sample = ", ".join(missing_unambiguous[:10])
        print(f"  WARNING: unambiguous source acronyms still missing: {sample}")


def load_journals(cursor, area_name_to_id):
    print("Loading journals...")
    before_count = get_table_row_count(cursor, "journals")
    inserted = 0
    skipped = 0
    valid_rows = 0

    def safe_decimal(val):
        try:
            v = str(val).replace(",", ".").strip()
            return float(v) if v else None
        except Exception:
            return None

    def safe_int(val):
        try:
            v = str(val).strip()
            return int(v) if v else None
        except Exception:
            return None

    with open(JOURNAL_RANK_CSV, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delimiter)

        for row in reader:
            title = row.get("Title", "").strip().strip('"')
            if not title:
                skipped += 1
                continue
            valid_rows += 1

            country = row.get("Country", "").strip() or None
            sjr = safe_decimal(row.get("SJR", "") or row.get("SJR-index", ""))
            cscore = safe_decimal(row.get("CiteScore", ""))
            h_idx = safe_int(row.get("H index", "") or row.get("H-index", ""))
            quartile = row.get("Best Quartile", "").strip() or None
            if quartile and quartile not in ("Q1", "Q2", "Q3", "Q4"):
                quartile = None

            area_name = normalize_area_name(row.get("Best Subject Area", ""))
            area_id = area_name_to_id.get(area_name)

            total_docs = safe_int(row.get("Total Docs.", ""))
            total_refs = safe_int(row.get("Total Refs.", ""))
            total_cites_3y = safe_int(row.get("Total Cites 3y", ""))
            citable_3y = safe_int(row.get("Citable Docs. 3y", ""))
            cpd2y = safe_decimal(row.get("Cites / Doc. 2y", ""))
            rpd = safe_decimal(row.get("Ref. / Doc.", ""))
            publisher = row.get("Publisher", "").strip() or None

            cursor.execute(
                """
                INSERT IGNORE INTO journals
                (title, country, publisher, sjr_index, cite_score, h_index,
                 best_quartile, best_subject_area,
                 total_docs, total_refs, total_cites_3y, citable_docs_3y,
                 cites_per_doc_2y, refs_per_doc)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    title,
                    country,
                    publisher,
                    sjr,
                    cscore,
                    h_idx,
                    quartile,
                    area_id,
                    total_docs,
                    total_refs,
                    total_cites_3y,
                    citable_3y,
                    cpd2y,
                    rpd,
                ),
            )
            inserted += cursor.rowcount

    final_count = get_table_row_count(cursor, "journals")
    ignored_rows = valid_rows - inserted
    print(f"  valid source rows: {valid_rows:,}")
    print(f"  skipped malformed rows: {skipped:,}")
    print(f"  inserted rows: {inserted:,}")
    print(f"  ignored as duplicates/already present: {ignored_rows:,}")
    print(f"  final journal rows: {final_count:,} (was {before_count:,})")


def load_authors(cursor):
    print("Loading authors...")
    before_count = get_table_row_count(cursor, "authors")
    author_files = [
        os.path.join(CLEANED_DIR, "cleaned_inproceedings_authors.csv"),
        os.path.join(CLEANED_DIR, "cleaned_articles_authors.csv"),
    ]

    unique_names = set()
    rows_scanned = 0
    blank_name_rows = 0
    for path in author_files:
        if not os.path.exists(path):
            print(f"  WARNING: {path} not found; run 02 and 03 first")
            continue
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows_scanned += 1
                name = row.get("author_name", "").strip()
                if not name:
                    blank_name_rows += 1
                    continue
                unique_names.add(name)

    batch = [(name, name) for name in unique_names]
    chunk = 1_000
    inserted_rows = 0
    insert_error_rows = 0
    for i in range(0, len(batch), chunk):
        rows = batch[i:i + chunk]
        try:
            cursor.executemany(
                "INSERT IGNORE INTO authors (name, name_exact) VALUES (%s, %s)",
                rows,
            )
            inserted_rows += cursor.rowcount
        except mysql.connector.Error as batch_exc:
            print(f"  WARNING: batch insert failed ({batch_exc}), retrying row-by-row...")
            for row in rows:
                try:
                    cursor.execute(
                        "INSERT IGNORE INTO authors (name, name_exact) VALUES (%s, %s)",
                        row,
                    )
                    inserted_rows += cursor.rowcount
                except mysql.connector.Error as exc:
                    insert_error_rows += 1
                    print(f"  WARNING: skipped author {row[0]!r} ({exc})")
        if i % 100_000 == 0 and i > 0:
            print(f"  authors: {i:,} distinct names processed so far...", flush=True)

    final_count = get_table_row_count(cursor, "authors")
    duplicate_suppressed = len(unique_names) - inserted_rows - insert_error_rows
    print(f"  source author rows scanned: {rows_scanned:,}")
    print(f"  skipped blank/malformed author rows: {blank_name_rows:,}")
    print(f"  distinct source names prepared: {len(unique_names):,}")
    print(f"  duplicate source rows collapsed before load: {rows_scanned - blank_name_rows - len(unique_names):,}")
    print(f"  inserted rows: {inserted_rows:,}")
    print(f"  ignored as duplicates/already present: {duplicate_suppressed:,}")
    if insert_error_rows:
        print(f"  skipped insert-error rows: {insert_error_rows:,}")
    print(f"  final authors rows: {final_count:,} (was {before_count:,})")


def main():
    args = parse_args()
    selected = set(args.only or ["primary_for", "best_subject_area", "conferences", "journals", "authors"])

    conn = get_conn()
    conn.autocommit = False
    cursor = conn.cursor()
    cursor.execute("USE biblio_db")

    if args.dry_run:
        print("DRY RUN: database changes will be rolled back before exit.")

    try:
        if "primary_for" in selected:
            load_primary_for(cursor)
            if not args.dry_run:
                conn.commit()

        if "best_subject_area" in selected:
            load_best_subject_area(cursor)
            if not args.dry_run:
                conn.commit()

        if "conferences" in selected:
            load_conferences(cursor, dry_run=args.dry_run)
            if not args.dry_run:
                conn.commit()

        if "journals" in selected:
            cursor.execute("SELECT area_id, area_name FROM best_subject_area")
            area_name_to_id = {name: aid for aid, name in cursor.fetchall()}
            load_journals(cursor, area_name_to_id)
            if not args.dry_run:
                conn.commit()

        if "authors" in selected:
            load_authors(cursor)
            if not args.dry_run:
                conn.commit()

        if args.dry_run:
            conn.rollback()
            print("\nDry-run complete. Rolled back DB changes.")
        else:
            print("\nSelected lookup stages loaded successfully.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
