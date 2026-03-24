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
 3. Token-overlap score: if ≥ THRESHOLD of meaningful tokens overlap → match
 4. Unmatched → written to unmatched_journals.txt for review

Output: journal_name_to_journal_id.csv (used by 07_load_papers.py)

Run: python 06_match_journals.py
"""

import csv
import os
import sys
import re

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG

CLEANED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "matched")
os.makedirs(OUT_DIR, exist_ok=True)

ARTICLES_CSV = os.path.join(CLEANED_DIR, "cleaned_articles.csv")
OUT_MAPPING = os.path.join(OUT_DIR, "journal_name_to_id.csv")
OUT_UNMATCHED = os.path.join(OUT_DIR, "unmatched_journals.txt")
MANUAL_ALIAS_CSV = os.path.join(OUT_DIR, "journal_manual_aliases.csv")

# EXTENDED: Common abbreviations found in DBLP journal names
ABBREV_MAP = {
    # Original core abbreviations
    r'\btrans\.?\b': 'transactions',
    r'\beng\.?\b': 'engineering',
    r'\bcomput\.?\b': 'computing',
    r'\bsci\.?\b': 'science',
    r'\bint\.?\b': 'international',
    r'\bj\.?\b': 'journal',
    r'\bsyst\.?\b': 'systems',
    r'\bmanag\.?\b': 'management',
    r'\binform\.?\b': 'information',
    r'\bconf\.?\b': 'conference',
    r'\bproc\.?\b': 'proceedings',
    r'\bres\.?\b': 'research',
    r'\bappl\.?\b': 'applications',
    r'\btheor\.?\b': 'theoretical',
    r'\bcommun\.?\b': 'communications',
    r'\bknowl\.?\b': 'knowledge',
    r'\bdata\b': 'data',
    r'\bnetw\.?\b': 'networks',
    r'\bdistrib\.?\b': 'distributed',
    r'\bparallel\b': 'parallel',
    r'\bann\.?\b': 'annals',
    r'\bvldb\b': 'very large data bases',
    # NEW: Additional common abbreviations
    r'\bmath\.?\b': 'mathematics',
    r'\bartif\.?\b': 'artificial',
    r'\bintell\.?\b': 'intelligence',
    r'\btech\.?\b': 'technology',
    r'\btechnol\.?\b': 'technology',
    r'\bsoc\.?\b': 'society',
    r'\blett\.?\b': 'letters',
    r'\bcomp\.?\b': 'computer',
    r'\bprog\.?\b': 'programming',
    r'\bimpl\.?\b': 'implementation',
    r'\bpract\.?\b': 'practice',
    r'\bexp\.?\b': 'experimental',
    r'\beduc\.?\b': 'education',
    r'\bind\.?\b': 'industrial',
    r'\bindust\.?\b': 'industrial',
    r'\bserv\.?\b': 'services',
    r'\bdes\.?\b': 'design',
    r'\benviron\.?\b': 'environment',
    r'\bmed\.?\b': 'medical',
    r'\bimag\.?\b': 'imaging',
    r'\bgraph\.?\b': 'graphics',
    r'\bvis\.?\b': 'visualization',
    r'\bmultim\.?\b': 'multimedia',
    r'\bsoftw\.?\b': 'software',
    r'\bengin\.?\b': 'engineering',
    r'\barchit\.?\b': 'architecture',
    r'\bcomputat\.?\b': 'computation',
    r'\bautom\.?\b': 'automation',
    r'\bcontr\.?\b': 'control',
    r'\bmod\.?\b': 'modeling',
    r'\bsimul\.?\b': 'simulation',
    r'\banal\.?\b': 'analysis',
    r'\bmeth\.?\b': 'methods',
    r'\bmethodol\.?\b': 'methodology',
    r'\bsolv\.?\b': 'solving',
    r'\boptim\.?\b': 'optimization',
    r'\bcomplex\.?\b': 'complex',
    r'\boper\.?\b': 'operations',
    r'\bmanuf\.?\b': 'manufacturing',
    r'\bprod\.?\b': 'production',
    r'\becon\.?\b': 'economics',
    r'\bfinanc\.?\b': 'finance',
    r'\bcybern\.?\b': 'cybernetics',
    r'\btelecommun\.?\b': 'telecommunications',
    r'\btelecom\.?\b': 'telecommunications',
    r'\bphotogr\.?\b': 'photogrammetry',
    r'\breconstr\.?\b': 'reconstruction',
    r'\bdts\.?\b': 'digital technology systems',
    r'\bgeoinf\.?\b': 'geoinformation',
    r'\bgeomatics\b': 'geomatics',
    r'\bphotogramm\.?\b': 'photogrammetry',
    r'\brem\.?\b': 'remote',
    r'\bsens\.?\b': 'sensing',
    r'\bphotobiol\.?\b': 'photobiology',
    r'\bphotochem\.?\b': 'photochemistry',
    r'\bphotophys\.?\b': 'photophysics',
    r'\bphotonics\b': 'photonics',
    r'\bdiscret\.?\b': 'discrete',
    r'\bcontin\.?\b': 'continuous',
    r'\bdyn\.?\b': 'dynamic',
    r'\bdynam\.?\b': 'dynamics',
    r'\bstat\.?\b': 'statistics',
    r'\bstoch\.?\b': 'stochastic',
    r'\bprobab\.?\b': 'probability',
    r'\bstatist\.?\b': 'statistics',
}

STOPWORDS = {'of', 'on', 'the', 'and', 'in', 'for', 'to', 'a', 'an',
             'with', 'its', 'at', 'by', 'from'}
COMPILED_ABBREV = [
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in ABBREV_MAP.items()
]
OVERLAP_THRESHOLD = 0.40
SHORT_NAME_OVERLAP_THRESHOLD = 0.70
LOW_CONFIDENCE_LIMIT = 20

def console_safe(text):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)

def expand_abbrev(text):
    t = text.lower()
    for pattern, replacement in COMPILED_ABBREV:
        t = pattern.sub(replacement, t)
    return t

def normalize(text):
    t = expand_abbrev(text)
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def canonical_key(text):
    return normalize(text).casefold()

def tokens(text):
    return set(normalize(text).split()) - STOPWORDS

def load_manual_aliases(valid_journal_ids, manual_alias_csv=MANUAL_ALIAS_CSV):
    alias_map = {}

    if not os.path.exists(manual_alias_csv):
        return alias_map

    with open(manual_alias_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"dblp_journal_name", "journal_id", "action"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            print(
                f"WARNING: skipping manual alias file {manual_alias_csv}: "
                "missing required columns dblp_journal_name, journal_id, action"
            )
            return alias_map

        for line_no, row in enumerate(reader, start=2):
            dblp_name = (row.get("dblp_journal_name") or "").strip()
            journal_id_raw = (row.get("journal_id") or "").strip()
            action = (row.get("action") or "").strip().casefold()

            if not dblp_name and not journal_id_raw and not action:
                continue
            if not dblp_name or action not in {"match", "unmatch"}:
                print(
                    f"WARNING: ignoring invalid manual alias at line {line_no}: "
                    f"{row!r}"
                )
                continue

            journal_id = None
            if action == "match":
                if not journal_id_raw:
                    print(
                        f"WARNING: ignoring manual match without journal_id at "
                        f"line {line_no}: {row!r}"
                    )
                    continue
                try:
                    journal_id = int(journal_id_raw)
                except ValueError:
                    print(
                        f"WARNING: ignoring manual alias with non-integer journal_id "
                        f"at line {line_no}: {journal_id_raw!r}"
                    )
                    continue
                if journal_id not in valid_journal_ids:
                    print(
                        f"WARNING: ignoring manual alias with unknown journal_id at "
                        f"line {line_no}: {journal_id}"
                    )
                    continue

            key = canonical_key(dblp_name)
            if key:
                alias_map[key] = {"action": action, "journal_id": journal_id}

    return alias_map

def overlap_score_from_tokens(source_tokens, target_tokens):
    if not source_tokens or not target_tokens:
        return 0.0

    match_count = 0
    for source_token in source_tokens:
        if any(
            target_token.startswith(source_token) or source_token.startswith(target_token)
            for target_token in target_tokens
        ):
            match_count += 1

    return match_count / max(len(source_tokens), len(target_tokens))

def token_overlap(a, b):
    return overlap_score_from_tokens(tokens(a), tokens(b))

def required_overlap_threshold(source_tokens, target_tokens):
    if max(len(source_tokens), len(target_tokens)) <= 2:
        return SHORT_NAME_OVERLAP_THRESHOLD
    return OVERLAP_THRESHOLD

def should_accept_overlap(source_tokens, target_tokens, score):
    return score >= required_overlap_threshold(source_tokens, target_tokens)

def classify_confidence(score, matched=True):
    if not matched:
        return "unmatched"
    if score >= 0.70:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"

def find_best_overlap_match(journal_name_tokens, db_journals_tokenized):
    best_jid = None
    best_title = ""
    best_tokens = set()
    best_score = 0.0

    if not journal_name_tokens:
        return best_jid, best_title, best_tokens, best_score

    for jid, title, db_tokens in db_journals_tokenized:
        if not db_tokens:
            continue
        score = overlap_score_from_tokens(journal_name_tokens, db_tokens)
        if score > best_score:
            best_jid = jid
            best_title = title
            best_tokens = db_tokens
            best_score = score

    return best_jid, best_title, best_tokens, best_score

def main():
    import mysql.connector

    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT journal_id, title FROM journals")
    db_journals = cur.fetchall()
    cur.close()
    conn.close()

    # Build lookup maps
    exact_map = {}  # normalized title -> journal_id
    valid_journal_ids = set()
    for jid, title in db_journals:
        valid_journal_ids.add(jid)
        if title:
            exact_map[normalize(title)] = jid

    manual_aliases = load_manual_aliases(valid_journal_ids)

    # Collect distinct journal names from cleaned articles
    journal_names = set()
    with open(ARTICLES_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            jname = (row.get("journal") or "").strip()
            if jname:
                journal_names.add(jname)

    print(f"Distinct DBLP journal names to match: {len(journal_names):,}")
    print(f"Manual journal alias entries loaded: {len(manual_aliases):,}")

    matched = 0
    unmatched = []
    low_confidence_matches = []

    # Pre-tokenize all db journals to avoid 25 million regex executions
    db_journals_tokenized = [(jid, title, tokens(title)) for jid, title in db_journals if title]

    with open(OUT_MAPPING, "w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["dblp_journal_name", "journal_id", "match_type", "confidence"])

        for jname in sorted(journal_names):
            manual_override = manual_aliases.get(canonical_key(jname))
            if manual_override:
                if manual_override["action"] == "match":
                    writer.writerow([jname, manual_override["journal_id"], "manual-alias", "high"])
                    matched += 1
                    continue

                writer.writerow([jname, "", "manual-unmatch", "unmatched"])
                unmatched.append((jname, 0.0))
                continue

            norm_jname = normalize(jname)

            # 1. Exact match
            jid = exact_map.get(norm_jname)
            if jid:
                writer.writerow([jname, jid, "exact", "high"])
                matched += 1
                continue

            # 2. Token-overlap match
            ta = tokens(jname)
            best_jid, best_title, best_tokens, best_score = find_best_overlap_match(
                ta, db_journals_tokenized
            )

            if should_accept_overlap(ta, best_tokens, best_score):
                confidence = classify_confidence(best_score)
                writer.writerow([jname, best_jid, f"overlap:{best_score:.2f}", confidence])
                matched += 1
                if confidence == "low":
                    low_confidence_matches.append((best_score, jname, best_jid, best_title))
            else:
                writer.writerow([jname, "", "unmatched", "unmatched"])
                unmatched.append((jname, best_score))

    with open(OUT_UNMATCHED, "w", encoding="utf-8") as fu:
        fu.write(f"# {len(unmatched)} unmatched DBLP journal names\n")
        fu.write("# Format: best_overlap_score | dblp_name\n\n")
        for jname, score in sorted(unmatched, key=lambda x: -x[1]):
            fu.write(f"{score:.2f} | {jname}\n")

    pct = matched / len(journal_names) * 100 if journal_names else 0
    print(f"Matched: {matched:,} / {len(journal_names):,} ({pct:.1f}%)")
    print("Top low-confidence journal matches for review:")
    for score, jname, jid, title in sorted(
        low_confidence_matches,
        key=lambda item: (item[0], item[1].casefold()),
    )[:LOW_CONFIDENCE_LIMIT]:
        print(console_safe(f"  {score:.2f} | {jname} -> [{jid}] {title}"))
    print(f"Mapping: {OUT_MAPPING}")
    print(f"Unmatched: {OUT_UNMATCHED}")

if __name__ == "__main__":
    print("Matching DBLP journal names to journals table...")
    main()
