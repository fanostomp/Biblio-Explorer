"""
05_match_conferences.py
────────────────────────
Matches DBLP booktitle values from cleaned_inproceedings.csv
to conference entries in the `conferences` table.

Strategy (in order):
  1. Exact acronym match (case-insensitive)
  2. Exact title match (case-insensitive)
  3. Writes unmatched booktitles to unmatched_conferences.txt for review

The result is a mapping file  booktitle_to_conf_id.csv
used by 07_load_papers.py.

Run: python 05_match_conferences.py
"""

import csv
import os
import sys
import re

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG

CLEANED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")
OUT_DIR     = os.path.join(os.path.dirname(__file__), "..", "data", "matched")
os.makedirs(OUT_DIR, exist_ok=True)

INPROC_CSV   = os.path.join(CLEANED_DIR, "cleaned_inproceedings.csv")
OUT_MAPPING  = os.path.join(OUT_DIR, "booktitle_to_conf_id.csv")
OUT_UNMATCHED = os.path.join(OUT_DIR, "unmatched_conferences.txt")

def normalize(text):
    """Lowercase, remove non-alphanumeric, collapse spaces."""
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', text.lower())).strip()

def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("SELECT conf_id, acronym, title FROM conferences")
    db_confs = cur.fetchall()
    cur.close()
    conn.close()

    # Build lookup maps
    acronym_map = {}   # normalized acronym → conf_id
    title_map   = {}   # normalized title   → conf_id
    for conf_id, acronym, title in db_confs:
        if acronym:
            acronym_map[normalize(acronym)] = conf_id
        if title:
            title_map[normalize(title)] = conf_id

    # Collect all distinct booktitles from the cleaned CSV
    booktitles = set()
    with open(INPROC_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bt = (row.get("booktitle") or "").strip()
            if bt:
                booktitles.add(bt)

    print(f"Distinct booktitles to match: {len(booktitles):,}")

    matched   = 0
    unmatched = []

    with open(OUT_MAPPING, "w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["booktitle", "conf_id"])

        for bt in sorted(booktitles):
            nbt = normalize(bt)
            conf_id = acronym_map.get(nbt) or title_map.get(nbt)

            if conf_id:
                writer.writerow([bt, conf_id])
                matched += 1
            else:
                # Try partial: first word of booktitle vs acronym
                first_word = nbt.split()[0] if nbt.split() else ""
                conf_id = acronym_map.get(first_word)
                if conf_id:
                    writer.writerow([bt, conf_id])
                    matched += 1
                else:
                    writer.writerow([bt, ""])  # empty = no match
                    unmatched.append(bt)

    with open(OUT_UNMATCHED, "w", encoding="utf-8") as fu:
        fu.write(f"# {len(unmatched)} unmatched booktitles\n")
        fu.write("# Review these and manually add to conferences table if needed\n\n")
        for bt in unmatched:
            fu.write(bt + "\n")

    pct = matched / len(booktitles) * 100 if booktitles else 0
    print(f"Matched: {matched:,} / {len(booktitles):,} ({pct:.1f}%)")
    print(f"Mapping:   {OUT_MAPPING}")
    print(f"Unmatched: {OUT_UNMATCHED}")

if __name__ == "__main__":
    print("Matching DBLP booktitles → conferences table...")
    main()
