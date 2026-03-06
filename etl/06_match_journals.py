"""
06_match_journals.py
─────────────────────
Matches DBLP journal name values from cleaned_articles.csv
to journal entries in the `journals` table.

The DBLP journal name (e.g. "IEEE Trans. Knowl. Data Eng.")
often does NOT match the Kaggle title (e.g. "IEEE Transactions on
Knowledge and Data Engineering") character-for-character.

Strategy (in order):
  1. Exact match (after normalization)
  2. Abbreviation expansion: try expanding common abbreviations
  3. Token-overlap score: if ≥ 70% of meaningful tokens overlap → match
  4. Unmatched → written to unmatched_journals.txt for review

Output: journal_name_to_journal_id.csv (used by 07_load_papers.py)

Run: python 06_match_journals.py
"""

import csv
import os
import sys
import re

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG

CLEANED_DIR  = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")
OUT_DIR      = os.path.join(os.path.dirname(__file__), "..", "data", "matched")
os.makedirs(OUT_DIR, exist_ok=True)

ARTICLES_CSV  = os.path.join(CLEANED_DIR, "cleaned_articles.csv")
OUT_MAPPING   = os.path.join(OUT_DIR, "journal_name_to_id.csv")
OUT_UNMATCHED = os.path.join(OUT_DIR, "unmatched_journals.txt")

# Common abbreviations found in DBLP journal names
ABBREV_MAP = {
    r'\btrans\.?\b':       'transactions',
    r'\beng\.?\b':         'engineering',
    r'\bcomput\.?\b':      'computing',
    r'\bsci\.?\b':         'science',
    r'\bint\.?\b':         'international',
    r'\bj\.?\b':           'journal',
    r'\bsyst\.?\b':        'systems',
    r'\bmanag\.?\b':       'management',
    r'\binform\.?\b':      'information',
    r'\bconf\.?\b':        'conference',
    r'\bproc\.?\b':        'proceedings',
    r'\bres\.?\b':         'research',
    r'\bappl\.?\b':        'applications',
    r'\btheor\.?\b':       'theoretical',
    r'\bcommun\.?\b':      'communications',
    r'\bknowl\.?\b':       'knowledge',
    r'\bdata\b':           'data',
    r'\bnetw\.?\b':        'networks',
    r'\bdistrib\.?\b':     'distributed',
    r'\bparallel\b':       'parallel',
    r'\bann\.?\b':         'annals',
    r'\bvldb\b':           'very large data bases',
    r'\brev\.?\b':         'review',
    r'\bcomb\.?\b':        'combinatoria',
    r'\banal\.?\b':        'analysis',
    r'\bapp\.?\b':         'applied',
    r'\boptim\.?\b':       'optimization',
    r'\bmath\.?\b':        'mathematics',
    r'\bsoftw\.?\b':       'software',
    r'\bvis\.?\b':         'visualization',
    r'\bgraph\.?\b':       'graphics',
    r'\btechnol\.?\b':     'technology',
    r'\belectr\.?\b':      'electronic',
    r'\bmed\.?\b':         'medical',
    r'\bphys\.?\b':        'physics',
    r'\bchem\.?\b':        'chemistry',
    r'\bautom\.?\b':       'automatic',
    r'\bintell\.?\b':      'intelligent',
    r'\bcomp\.?\b':        'computer',
    r'\bgeom\.?\b':        'geometry',
    r'\bsec\.?\b':         'security',
    r'\bsecur\.?\b':       'security',
    r'\bfundam\.?\b':      'fundamenta',
    r'\binf\.?\b':         'information',
    r'\brobot\.?\b':       'robotics',
    r'\bmodel\.?\b':       'modeling',
    r'\bsimul\.?\b':       'simulation',
}

STOPWORDS = {'of', 'on', 'the', 'and', 'in', 'for', 'to', 'a', 'an',
             'with', 'its', 'at', 'by', 'from'}

def expand_abbrev(text):
    t = text.lower()
    for pattern, replacement in ABBREV_MAP.items():
        t = re.sub(pattern, replacement, t)
    return t

def normalize(text):
    t = expand_abbrev(text)
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def tokens(text):
    return set(normalize(text).split()) - STOPWORDS

def token_overlap(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))

def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("SELECT journal_id, title FROM journals")
    db_journals = cur.fetchall()
    cur.close()
    conn.close()

    # Build lookup maps
    exact_map  = {}   # normalized title → journal_id
    for jid, title in db_journals:
        if title:
            exact_map[normalize(title)] = jid

    # Collect distinct journal names from cleaned articles
    journal_names = set()
    with open(ARTICLES_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            jname = (row.get("journal") or "").strip()
            if jname:
                journal_names.add(jname)

    print(f"Distinct DBLP journal names to match: {len(journal_names):,}")

    matched   = 0
    unmatched = []

    OVERLAP_THRESHOLD = 0.55   # 55% token overlap required

    # Pre-tokenize all db journals to avoid 25 million regex executions
    db_journals_tokenized = [(jid, title, tokens(title)) for jid, title in db_journals if title]

    with open(OUT_MAPPING, "w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["dblp_journal_name", "journal_id", "match_type"])

        for jname in sorted(journal_names):
            norm_jname = normalize(jname)

            # 1. Exact match
            jid = exact_map.get(norm_jname)
            if jid:
                writer.writerow([jname, jid, "exact"])
                matched += 1
                continue

            # 2. Token-overlap match
            best_jid    = None
            best_score  = 0.0
            ta = tokens(jname)
            len_ta = len(ta)
            if len_ta > 0:
                for jid2, title, tb in db_journals_tokenized:
                    len_tb = len(tb)
                    if len_tb == 0:
                        continue
                    # Inline overlap calculation
                    score = len(ta & tb) / max(len_ta, len_tb)
                    if score > best_score:
                        best_score = score
                        best_jid   = jid2

            if best_score >= OVERLAP_THRESHOLD:
                writer.writerow([jname, best_jid, f"overlap:{best_score:.2f}"])
                matched += 1
            else:
                writer.writerow([jname, "", "unmatched"])
                unmatched.append((jname, best_score))

    with open(OUT_UNMATCHED, "w", encoding="utf-8") as fu:
        fu.write(f"# {len(unmatched)} unmatched DBLP journal names\n")
        fu.write("# Format: best_overlap_score | dblp_name\n\n")
        for jname, score in sorted(unmatched, key=lambda x: -x[1]):
            fu.write(f"{score:.2f} | {jname}\n")

    pct = matched / len(journal_names) * 100 if journal_names else 0
    print(f"Matched: {matched:,} / {len(journal_names):,} ({pct:.1f}%)")
    print(f"Mapping:   {OUT_MAPPING}")
    print(f"Unmatched: {OUT_UNMATCHED}")

if __name__ == "__main__":
    print("Matching DBLP journal names → journals table...")
    main()
