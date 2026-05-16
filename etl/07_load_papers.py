"""
07_load_papers.py
Final ETL step: loads papers and paper_authors into the database
using the cleaned CSVs and the venue mapping files produced by
scripts 05 and 06.

Unmatched venue policy:
- papers still load into the final papers table when venue matching fails
- unmatched conference rows keep conf_id = NULL
- unmatched journal rows keep journal_id = NULL
- venue/profile analytics therefore cover only linked venue rows,
  while author/year analytics still see every loaded paper

Rerun workflow:
- papers are keyed by (type, raw_id)
- rerunning this script updates/reuses the same paper rows
- if the schema guard is missing or duplicates already exist,
  the loader aborts instead of silently importing duplicate facts

Run AFTER: 02, 03, 04, 05, 06
Run: python 07_load_papers.py
"""

import logging
import csv
import os
import sys

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG

logger = logging.getLogger(__name__)

CLEANED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")
MATCHED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "matched")

INPROC_CSV = os.path.join(CLEANED_DIR, "cleaned_inproceedings.csv")
INPROC_AUTH = os.path.join(CLEANED_DIR, "cleaned_inproceedings_authors.csv")
ARTICLES_CSV = os.path.join(CLEANED_DIR, "cleaned_articles.csv")
ARTICLES_AUTH = os.path.join(CLEANED_DIR, "cleaned_articles_authors.csv")

CONF_MAP_CSV = os.path.join(MATCHED_DIR, "booktitle_to_conf_id.csv")
JOUR_MAP_CSV = os.path.join(MATCHED_DIR, "journal_name_to_id.csv")


def load_mapping(path, key_col, val_col):
    """Load a CSV mapping into a dict. val is int or None."""
    mapping = {}
    if not os.path.exists(path):
        logger.warning(f"  WARNING: mapping file not found: {path}")
        return mapping
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get(key_col, "").strip()
            val = row.get(val_col, "").strip()
            mapping[key] = int(val) if val else None
    return mapping


def load_author_map(conn):
    """Fetch author name -> author_id from DB."""
    cur = conn.cursor()
    cur.execute("SELECT author_id, name_exact FROM authors")
    mapping = {name_exact: aid for aid, name_exact in cur.fetchall()}
    cur.close()
    return mapping


def validate_paper_identity_guard(conn):
    """Refuse to load papers unless reruns are protected by schema + clean data."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT index_name, seq_in_index, column_name
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'papers'
          AND non_unique = 0
        ORDER BY index_name, seq_in_index
        """
    )

    unique_indexes = {}
    for index_name, _seq_in_index, column_name in cur.fetchall():
        unique_indexes.setdefault(index_name, []).append(column_name)

    has_type_raw_id_guard = any(
        columns == ["type", "raw_id"]
        for columns in unique_indexes.values()
    )

    if not has_type_raw_id_guard:
        cur.close()
        raise RuntimeError(
            "papers is missing a UNIQUE(type, raw_id) constraint. "
            "Rebuild or migrate the schema with etl/01_create_schema.sql before rerunning 07_load_papers.py."
        )

    cur.execute(
        """
        SELECT type, raw_id, COUNT(*) AS dup_count
        FROM papers
        WHERE raw_id IS NOT NULL
        GROUP BY type, raw_id
        HAVING COUNT(*) > 1
        ORDER BY dup_count DESC, type, raw_id
        LIMIT 5
        """
    )
    duplicates = cur.fetchall()
    cur.close()

    if duplicates:
        sample = ", ".join(
            f"{paper_type}:{raw_id} x{dup_count}"
            for paper_type, raw_id, dup_count in duplicates
        )
        raise RuntimeError(
            "Existing duplicate paper identities detected before load "
            f"({sample}). Clean the table before rerunning 07_load_papers.py."
        )


def upsert_papers_batch(cursor, rows):
    cursor.executemany(
        """
        INSERT INTO papers
            (title, year, pages, type, conf_id, journal_id,
             volume, number, dblp_key, ee, url, raw_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            title = VALUES(title),
            year = VALUES(year),
            pages = VALUES(pages),
            conf_id = VALUES(conf_id),
            journal_id = VALUES(journal_id),
            volume = VALUES(volume),
            number = VALUES(number),
            dblp_key = VALUES(dblp_key),
            ee = VALUES(ee),
            url = VALUES(url)
        """,
        rows,
    )


def insert_authors_batch(cursor, rows):
    cursor.executemany(
        """
        INSERT IGNORE INTO paper_authors (paper_id, author_id, author_order)
        VALUES (%s, %s, %s)
        """,
        rows,
    )


def process_papers(
    conn,
    paper_csv,
    author_csv,
    paper_type,
    venue_map,
    venue_key_col,
    author_name_to_id,
    BATCH=1000,
):
    """
    Generic loader for both conference papers and journal articles.
    paper_type: 'conference' | 'journal'
    venue_key_col: column in paper_csv that holds the venue name (booktitle or journal)
    Unmatched venues are allowed: keep the paper fact and leave the
    type-specific venue FK NULL.
    """
    cur = conn.cursor()

    # Build a temp map: raw paper id from CSV -> db paper_id
    raw_to_db = {}

    paper_rows = []
    skipped = 0
    processed = 0
    matched_venue_rows = 0
    unmatched_venue_rows = 0

    with open(paper_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_id = row.get("id", "").strip()
            title = row.get("title", "").strip()
            year_s = row.get("year", "").strip()
            venue = row.get(venue_key_col, "").strip()

            if not raw_id or not title or not year_s:
                skipped += 1
                continue
            try:
                year = int(year_s)
            except ValueError:
                skipped += 1
                continue

            venue_id = venue_map.get(venue)  # None means the paper stays unmatched in final papers

            if paper_type == "conference":
                conf_id = venue_id
                journal_id = None
                volume = number = None
            else:
                conf_id = None
                journal_id = venue_id
                volume = row.get("volume", "").strip() or None
                number = row.get("number", "").strip() or None

            if venue_id is None:
                unmatched_venue_rows += 1
            else:
                matched_venue_rows += 1

            paper_rows.append(
                (
                    title,
                    year,
                    row.get("pages", "").strip() or None,
                    paper_type,
                    conf_id,
                    journal_id,
                    volume if paper_type == "journal" else None,
                    number if paper_type == "journal" else None,
                    row.get("dblp_key", "").strip() or None,
                    row.get("ee", "").strip() or None,
                    row.get("url", "").strip() or None,
                    raw_id,
                )
            )

            if len(paper_rows) >= BATCH:
                _flush_papers(cur, conn, paper_rows)
                processed += len(paper_rows)
                logger.info(f"  [{paper_type}] {processed:,} processed...")
                paper_rows = []

    if paper_rows:
        _flush_papers(cur, conn, paper_rows)
        processed += len(paper_rows)

    cur.execute("SELECT COUNT(*) FROM papers WHERE type = %s", (paper_type,))
    current_total = cur.fetchone()[0]
    logger.info(
        f"  [{paper_type}] papers done: {processed:,} processed, "
        f"{skipped:,} skipped, {current_total:,} rows now present, "
        f"{matched_venue_rows:,} venue-matched, {unmatched_venue_rows:,} venue-unmatched"
    )

    logger.info(f"  [{paper_type}] fetching IDs to build author mapping...")
    cur.execute(
        "SELECT raw_id, paper_id FROM papers WHERE type = %s AND raw_id IS NOT NULL",
        (paper_type,),
    )
    for r_id, p_id in cur.fetchall():
        if r_id:
            raw_to_db[str(r_id)] = p_id

    auth_rows = []
    auth_written = 0
    with open(author_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_id = row.get("paper_id", "").strip()
            name = row.get("author_name", "").strip()
            order_s = row.get("author_order", "1").strip()

            db_paper_id = raw_to_db.get(raw_id)
            db_author_id = author_name_to_id.get(name)

            if not db_paper_id or not db_author_id:
                continue
            try:
                order = int(order_s)
            except ValueError:
                order = 1

            auth_rows.append((db_paper_id, db_author_id, order))

            if len(auth_rows) >= BATCH:
                insert_authors_batch(cur, auth_rows)
                conn.commit()
                auth_written += len(auth_rows)
                auth_rows = []
                logger.info(f"  [{paper_type}] {auth_written:,} author links written...")

    if auth_rows:
        insert_authors_batch(cur, auth_rows)
        conn.commit()
        auth_written += len(auth_rows)

    logger.info(f"  [{paper_type}] author links done: {auth_written:,}")
    cur.close()


def _flush_papers(cur, conn, rows):
    """Insert paper rows."""
    upsert_papers_batch(cur, rows)
    conn.commit()


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(level=logging.INFO):
    logging.basicConfig(level=level, format=LOG_FORMAT)


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    conn.autocommit = False

    validate_paper_identity_guard(conn)

    logger.info("Loading venue mappings...")
    conf_map = load_mapping(CONF_MAP_CSV, "booktitle", "conf_id")
    journal_map = load_mapping(JOUR_MAP_CSV, "dblp_journal_name", "journal_id")
    if not conf_map:
        conn.close()
        raise RuntimeError(
            "Conference mapping is empty. Run 05_match_conferences.py first "
            "or verify that booktitle_to_conf_id.csv exists and is non-empty."
        )
    if not journal_map:
        conn.close()
        raise RuntimeError(
            "Journal mapping is empty. Run 06_match_journals.py first "
            "or verify that journal_name_to_id.csv exists and is non-empty."
        )

    logger.info("Loading author name->id map...")
    author_map = load_author_map(conn)
    logger.info(f"  {len(author_map):,} authors in DB")

    logger.info("\nLoading conference papers...")
    process_papers(
        conn,
        INPROC_CSV,
        INPROC_AUTH,
        paper_type="conference",
        venue_map=conf_map,
        venue_key_col="booktitle",
        author_name_to_id=author_map,
    )

    logger.info("\nLoading journal articles...")
    process_papers(
        conn,
        ARTICLES_CSV,
        ARTICLES_AUTH,
        paper_type="journal",
        venue_map=journal_map,
        venue_key_col="journal",
        author_name_to_id=author_map,
    )

    conn.close()
    logger.info("\nAll papers and author links loaded.")


if __name__ == "__main__":
    configure_logging()
    main()
