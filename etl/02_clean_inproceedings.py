"""
02_clean_inproceedings.py
─────────────────────────
Reads the raw DBLP inproceedings (conference papers) CSV,
cleans it, and writes two output files ready for loading:
  - cleaned_inproceedings.csv   (one row per paper)
  - cleaned_inproceedings_authors.csv  (one row per paper-author pair)

Run: python 02_clean_inproceedings.py
"""

import logging
import csv
import os
import sys
import re

# Allow importing config.py from the same folder
sys.path.insert(0, os.path.dirname(__file__))
from config import INPROCEEDINGS_CSV

logger = logging.getLogger(__name__)

OUT_DIR  = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")
os.makedirs(OUT_DIR, exist_ok=True)

OUT_PAPERS  = os.path.join(OUT_DIR, "cleaned_inproceedings.csv")
OUT_AUTHORS = os.path.join(OUT_DIR, "cleaned_inproceedings_authors.csv")

# Columns we actually care about from the raw file
KEEP_COLS = ["id", "author", "booktitle", "crossref", "key",
             "pages", "title", "url", "ee", "year"]

def clean_text(val):
    """Strip surrounding whitespace and internal double-spaces."""
    if val is None:
        return None
    v = val.strip()
    return v if v else None

def clean_year(val):
    v = clean_text(val)
    if v and re.fullmatch(r'\d{4}', v):
        return int(v)
    return None

def split_authors(author_str):
    """Split pipe-separated author string into ordered list."""
    if not author_str:
        return []
    return [a.strip() for a in author_str.split("|") if a.strip()]

REQUIRED = {"id", "title", "year", "booktitle"}

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(level=logging.INFO):
    logging.basicConfig(level=level, format=LOG_FORMAT)


def main():
    skipped = 0
    written = 0

    with (
        open(INPROCEEDINGS_CSV, "r", encoding="utf-8", errors="replace") as fin,
        open(OUT_PAPERS,  "w", encoding="utf-8", newline="") as fp,
        open(OUT_AUTHORS, "w", encoding="utf-8", newline="") as fa,
    ):
        reader = csv.DictReader(fin, delimiter=";")

        paper_writer = csv.writer(fp)
        paper_writer.writerow(["id","title","year","pages","booktitle",
                                "crossref","dblp_key","ee","url"])

        author_writer = csv.writer(fa)
        author_writer.writerow(["paper_id","author_name","author_order"])

        for row in reader:
            pid   = clean_text(row.get("id"))
            title = clean_text(row.get("title"))
            year  = clean_year(row.get("year"))
            btitle = clean_text(row.get("booktitle"))

            # Skip rows missing required fields
            if not pid or not title or year is None or not btitle:
                skipped += 1
                continue

            authors = split_authors(row.get("author", ""))
            # A paper with no authors is still valid in DBLP — keep it

            paper_writer.writerow([
                pid,
                title,
                year,
                clean_text(row.get("pages")),
                btitle,
                clean_text(row.get("crossref")),
                clean_text(row.get("key")),
                clean_text(row.get("ee")),
                clean_text(row.get("url")),
            ])

            for order, name in enumerate(authors, start=1):
                author_writer.writerow([pid, name, order])

            written += 1
            if written % 50_000 == 0:
                logger.info(f"  [inproceedings] {written:,} rows processed...")

    logger.info(f"\nDone. Written: {written:,} | Skipped: {skipped:,}")
    logger.info(f"Output: {OUT_PAPERS}")
    logger.info(f"Output: {OUT_AUTHORS}")

if __name__ == "__main__":
    configure_logging()
    logger.info("Cleaning inproceedings (conference papers)...")
    main()
