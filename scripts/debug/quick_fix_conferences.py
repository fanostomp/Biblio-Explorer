#!/usr/bin/env python3
"""
Quick fix: backfill unmatched conference papers with newly available conf_id values
without rerunning the full papers loader.
"""

import csv
import os
import sys

import mysql.connector

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "etl"))
from config import DB_CONFIG

CONF_MAP_CSV = os.path.join("data", "matched", "booktitle_to_conf_id.csv")
INPROC_CSV = os.path.join("data", "cleaned", "cleaned_inproceedings.csv")


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    print("=" * 70)
    print("QUICK FIX: Link unmatched conference papers via raw_id")
    print("=" * 70)

    conf_mappings = {}
    with open(CONF_MAP_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            conf_id = (row.get("conf_id") or "").strip()
            booktitle = (row.get("booktitle") or "").strip()
            if conf_id and booktitle:
                conf_mappings[booktitle] = int(conf_id)

    print(f"\nLoaded {len(conf_mappings):,} conference mappings")

    print("Building raw_id -> conf_id mapping from cleaned_inproceedings.csv...")
    raw_to_conf = {}
    with open(INPROC_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_id = (row.get("id") or "").strip()
            booktitle = (row.get("booktitle") or "").strip()
            if raw_id and booktitle and booktitle in conf_mappings:
                raw_to_conf[raw_id] = conf_mappings[booktitle]

    print(f"  Built {len(raw_to_conf):,} raw_id -> conf_id mappings")

    print("\nFinding papers to update...")
    cursor.execute(
        """
        SELECT paper_id, raw_id
        FROM papers
        WHERE type = 'conference' AND conf_id IS NULL
        """
    )
    papers_to_update = cursor.fetchall()
    print(f"  Found {len(papers_to_update):,} papers with NULL conf_id")

    updates = []
    not_found = 0
    for paper_id, raw_id in papers_to_update:
        key = str(raw_id).strip() if raw_id is not None else ""
        if key and key in raw_to_conf:
            updates.append((raw_to_conf[key], paper_id))
        else:
            not_found += 1

    print(f"  Can fix: {len(updates):,} papers")
    print(f"  Cannot fix: {not_found:,} papers (no matching raw_id/booktitle)")

    if updates:
        print(f"\nUpdating {len(updates):,} papers...")
        cursor.executemany(
            "UPDATE papers SET conf_id = %s WHERE paper_id = %s AND conf_id IS NULL",
            updates,
        )
        conn.commit()
        print(f"  Updated {cursor.rowcount:,} papers")

        cursor.execute(
            "SELECT COUNT(*) FROM papers WHERE type = 'conference' AND conf_id IS NULL"
        )
        remaining = cursor.fetchone()[0]
        print(f"\nRemaining unlinked conference papers: {remaining:,}")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
