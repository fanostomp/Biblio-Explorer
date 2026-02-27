"""
04_load_lookups.py
──────────────────
Loads all lookup tables into biblio_db:
  1. primary_for      (from icoreCategories.xlsx)
  2. best_subject_area (from bestSubjectArea.csv)
  3. conferences      (from iCore26_KilledColumnsForLoading.csv)
  4. journals         (from journal_ranking_data_raw.csv)
  5. authors          (collected from both cleaned author files)

Run AFTER:  01_create_schema.sql has been executed
            02_clean_inproceedings.py
            03_clean_articles.py
Run: python 04_load_lookups.py
"""

import csv
import os
import sys
import re

import mysql.connector
import openpyxl          # pip install openpyxl

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG, ICORE_CSV, ICORE_CATS_XLSX, JOURNAL_RANK_CSV, BEST_AREA_CSV

CLEANED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")

def get_conn():
    return mysql.connector.connect(**DB_CONFIG)

# ──────────────────────────────────────────────
# 1. Load primary_for from icoreCategories.xlsx
# ──────────────────────────────────────────────
def load_primary_for(cursor):
    print("Loading primary_for...")
    wb = openpyxl.load_workbook(ICORE_CATS_XLSX, read_only=True, data_only=True)
    ws = wb.active
    rows = iter(ws.rows)
    next(rows)  # skip header

    inserted = 0
    for row in rows:
        code = str(row[0].value).strip() if row[0].value is not None else None
        desc = str(row[1].value).strip() if row[1].value is not None else None
        if not code or not desc:
            continue
        cursor.execute(
            "INSERT IGNORE INTO primary_for (for_code, description) VALUES (%s, %s)",
            (code, desc)
        )
        inserted += 1
    wb.close()
    print(f"  primary_for: {inserted} rows")

# ──────────────────────────────────────────────
# 2. Load best_subject_area from bestSubjectArea.csv
# ──────────────────────────────────────────────
def load_best_subject_area(cursor):
    print("Loading best_subject_area...")
    inserted = 0
    with open(BEST_AREA_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            if not row or not row[0].strip():
                continue
            name = row[0].strip().strip('"')
            cursor.execute(
                "INSERT IGNORE INTO best_subject_area (area_name) VALUES (%s)",
                (name,)
            )
            inserted += 1
    print(f"  best_subject_area: {inserted} rows")

# ──────────────────────────────────────────────
# 3. Load conferences from iCore26 CSV
# ──────────────────────────────────────────────
def load_conferences(cursor):
    print("Loading conferences...")
    inserted = 0
    skipped  = 0
    with open(ICORE_CSV, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            acronym = row.get("Acronym", "").strip()
            title   = row.get(" Title", row.get("Title", "")).strip()
            rank    = row.get("Rank", "").strip()
            for_code = row.get("PrimaryFoR", "").strip() or None
            dblp    = row.get("DBLP", "").strip()
            dblp_val = "Yes" in dblp  # iCORE marks DBLP presence as 'Yes'/'No'

            if not acronym or not title:
                skipped += 1
                continue

            # Validate rank value
            valid_ranks = {"A*", "A", "B", "C"}
            rank = rank if rank in valid_ranks else None

            cursor.execute("""
                INSERT IGNORE INTO conferences (title, acronym, rank, primary_for)
                VALUES (%s, %s, %s, %s)
            """, (title, acronym, rank, for_code))
            inserted += 1

    print(f"  conferences: {inserted} rows (skipped: {skipped})")

# ──────────────────────────────────────────────
# 4. Load journals from Kaggle ranking CSV
# The file is comma-delimited but journal titles contain commas too.
# The professor's notes say to make it TSV — we handle both by
# reading the raw line and splitting on the last safe delimiter.
# ──────────────────────────────────────────────
def normalize_area_name(name):
    """Strip quotes and extra spaces."""
    return name.strip().strip('"').strip()

def load_journals(cursor, area_name_to_id):
    print("Loading journals...")
    inserted = 0
    skipped  = 0

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
        # Try tab-delimiter first (per professor's notes about the problems.txt)
        sample = f.read(4096)
        f.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delimiter)

        for row in reader:
            title = row.get("Title", "").strip().strip('"')
            if not title:
                skipped += 1
                continue

            rank_val = row.get("Rank", "").strip()
            country  = row.get("Country", "").strip() or None
            sjr      = safe_decimal(row.get("SJR", "") or row.get("SJR-index", ""))
            cscore   = safe_decimal(row.get("CiteScore", ""))
            h_idx    = safe_int(row.get("H index", "") or row.get("H-index",""))
            quartile = row.get("Best Quartile", "").strip() or None
            if quartile and quartile not in ("Q1","Q2","Q3","Q4"):
                quartile = None

            area_name = normalize_area_name(row.get("Best Subject Area", ""))
            area_id   = area_name_to_id.get(area_name)

            total_docs      = safe_int(row.get("Total Docs.", ""))
            total_refs      = safe_int(row.get("Total Refs.", ""))
            total_cites_3y  = safe_int(row.get("Total Cites 3y", ""))
            citable_3y      = safe_int(row.get("Citable Docs. 3y", ""))
            cpd2y           = safe_decimal(row.get("Cites / Doc. 2y", ""))
            rpd             = safe_decimal(row.get("Ref. / Doc.", ""))
            publisher       = row.get("Publisher", "").strip() or None

            cursor.execute("""
                INSERT IGNORE INTO journals
                (title, country, publisher, sjr_index, cite_score, h_index,
                 best_quartile, best_subject_area,
                 total_docs, total_refs, total_cites_3y, citable_docs_3y,
                 cites_per_doc_2y, refs_per_doc)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (title, country, publisher, sjr, cscore, h_idx,
                  quartile, area_id,
                  total_docs, total_refs, total_cites_3y, citable_3y,
                  cpd2y, rpd))
            inserted += 1

    print(f"  journals: {inserted} rows (skipped: {skipped})")

# ──────────────────────────────────────────────
# 5. Load authors from both cleaned author files
# ──────────────────────────────────────────────
def load_authors(cursor):
    print("Loading authors...")
    author_files = [
        os.path.join(CLEANED_DIR, "cleaned_inproceedings_authors.csv"),
        os.path.join(CLEANED_DIR, "cleaned_articles_authors.csv"),
    ]

    unique_names = set()
    for path in author_files:
        if not os.path.exists(path):
            print(f"  WARNING: {path} not found — run 02 and 03 first")
            continue
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("author_name", "").strip()
                if name:
                    unique_names.add(name)

    # Batch insert
    batch = []
    for name in unique_names:
        batch.append((name,))
    # Insert in chunks of 10,000
    chunk = 10_000
    for i in range(0, len(batch), chunk):
        cursor.executemany(
            "INSERT IGNORE INTO authors (name) VALUES (%s)",
            batch[i:i+chunk]
        )
        if i % 100_000 == 0 and i > 0:
            print(f"  authors: {i:,} inserted so far...", flush=True)

    print(f"  authors: {len(unique_names):,} unique names loaded")

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("USE biblio_db")

    load_primary_for(cursor)
    conn.commit()

    load_best_subject_area(cursor)
    conn.commit()

    load_conferences(cursor)
    conn.commit()

    # Build area_name → area_id map for journal loading
    cursor.execute("SELECT area_id, area_name FROM best_subject_area")
    area_name_to_id = {name: aid for aid, name in cursor.fetchall()}

    load_journals(cursor, area_name_to_id)
    conn.commit()

    load_authors(cursor)
    conn.commit()

    cursor.close()
    conn.close()
    print("\nAll lookup tables loaded successfully.")

if __name__ == "__main__":
    main()
