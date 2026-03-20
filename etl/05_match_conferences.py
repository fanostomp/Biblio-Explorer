"""
05_match_conferences.py
-----------------------
Matches DBLP booktitle values from cleaned_inproceedings.csv
to conference entries in the `conferences` table.

Strategy (in order):
  1. Manual curated aliases from conference_manual_aliases.csv
  2. Exact acronym/title match after normalization
  3. Legacy acronym aliases (for example NIPS -> NeurIPS)
  4. Delimiter-aware matching for composite booktitles such as ACL/IJCNLP
  5. Conservative parent-event mapping for official workshop/satellite variants
  6. Conservative fuzzy title overlap for descriptive booktitles

Outputs:
  - booktitle_to_conf_id.csv
  - unmatched_conferences.txt
  - unmatched_conferences_by_rows.txt

Run: python 05_match_conferences.py
"""

import csv
import os
import sys
import re
from collections import Counter, defaultdict

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG

CLEANED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "matched")
os.makedirs(OUT_DIR, exist_ok=True)

INPROC_CSV = os.path.join(CLEANED_DIR, "cleaned_inproceedings.csv")
OUT_MAPPING = os.path.join(OUT_DIR, "booktitle_to_conf_id.csv")
OUT_UNMATCHED = os.path.join(OUT_DIR, "unmatched_conferences.txt")
OUT_UNMATCHED_BY_ROWS = os.path.join(OUT_DIR, "unmatched_conferences_by_rows.txt")
MANUAL_ALIAS_CSV = os.path.join(OUT_DIR, "conference_manual_aliases.csv")

GENERIC_PREFIX_RE = re.compile(
    r"^(?:proceedings of the|proceedings of|proceedings|proc\.?|"
    r"conference on|conference|symposium on|symposium|workshop on|workshop|"
    r"annual|international|joint|selected papers|companion|volume|vol\.?|"
    r"tutorials|special session|special sessions|series)\b[\s:,-]*",
    re.IGNORECASE,
)
PAREN_RE = re.compile(r"\([^)]*\)")
TRAILING_VOLUME_RE = re.compile(r"\s*\((?:\d+|[ivxlcdm]+)\)\s*$", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
ORDINAL_RE = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE)
NON_WORD_RE = re.compile(r"[^\w\s]")
SPLIT_RE = re.compile(r"[\/&+|]")
ACRONYM_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9-]{1,20}\b")
VARIANT_MARKERS = (
    "workshop",
    "workshops",
    "symposium",
    "symposia",
    "fall symposium",
    "spring symposium",
    "poster",
    "posters",
    "doctoral consortium",
    "consortium",
    "extended abstracts",
    "companion",
    "adjunct",
    "satellite",
    "forum",
    "demo",
    "demos",
    "demonstration",
    "demonstrations",
    "challenge",
    "competition",
    "tutorial",
    "tutorials",
)
SAFE_EMBEDDED_CONTEXT_TOKENS = {
    "spring", "fall", "summer", "winter", "autumn",
    "conference", "conferences", "symposium", "symposia",
    "workshop", "workshops", "forum", "meeting", "summit",
    "colloquium", "annual", "international", "joint",
    "extended", "abstracts", "poster", "posters", "demo", "demos",
    "doctoral", "consortium", "satellite", "main", "technical",
    "proceedings", "edition", "special", "session", "sessions",
}
STOPWORDS = {
    "of", "on", "the", "and", "in", "for", "to", "a", "an", "with",
    "its", "at", "by", "from", "or", "proceedings", "proceeding",
    "conference", "symposium", "workshop", "workshops", "annual",
    "international", "joint", "selected", "papers", "companion",
    "volume", "vol", "tutorials", "special", "session", "sessions",
    "edition", "ed", "part", "series"
}

# Legacy aliases are keyed by the same normalized/acronym-safe representation
# used for incoming booktitles so mixed-case manual or source values still match.
LEGACY_ACRONYM_ALIASES = {
    "nips": "neurips",
    "eurospeech": "interspeech",
    "icslp": "interspeech",
}


def normalize(text):
    """Lowercase, remove generic wrappers, punctuation, years, and collapse spaces."""
    text = text.strip()
    text = TRAILING_VOLUME_RE.sub(" ", text)
    text = PAREN_RE.sub(" ", text.lower())
    text = YEAR_RE.sub(" ", text)
    text = ORDINAL_RE.sub(" ", text)
    text = GENERIC_PREFIX_RE.sub(" ", text)
    text = text.replace("@", " ")
    text = NON_WORD_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_key(text):
    """Case-insensitive normalized key for manual alias matching."""
    return normalize(text).casefold()


def compact(text):
    """Compact acronym-like representation used for acronym comparisons."""
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def token_set(text):
    """Normalized tokens with generic event words removed."""
    return {
        token
        for token in normalize(text).split()
        if token and token not in STOPWORDS
    }


def is_acronym_like(text):
    compact_text = compact(text)
    condensed = re.sub(r"\s+", "", text)
    return 2 <= len(compact_text) <= 20 and compact_text.isalnum() and condensed.upper() == condensed


def contains_variant_marker(text):
    lowered = text.casefold()
    return any(marker in lowered for marker in VARIANT_MARKERS)


def is_safe_embedded_acronym_context(text, acronym_token):
    """
    Allow acronym mentions embedded in lightweight wrappers such as
    'SIGMOD Conference' or 'VTC Spring' without reverting to broad
    whitespace-based matching.
    """
    remaining = [
        token
        for token in normalize(text).split()
        if compact(token) != compact(acronym_token) and token not in STOPWORDS
    ]
    if not remaining:
        return True
    return all(token in SAFE_EMBEDDED_CONTEXT_TOKENS for token in remaining)


def extract_acronym_mentions(text):
    """Return standalone token mentions that may refer to known acronyms."""
    mentions = []
    for token in ACRONYM_TOKEN_RE.findall(text):
        compact_token = compact(token)
        if 2 <= len(compact_token) <= 20:
            mentions.append(token)
    return mentions


def candidate_strings(booktitle):
    """Generate stripped variants of a DBLP booktitle."""
    raw = booktitle.strip()
    candidates = {
        raw,
        TRAILING_VOLUME_RE.sub(" ", raw).strip(),
        PAREN_RE.sub(" ", raw).strip(),
        YEAR_RE.sub(" ", raw).strip(),
    }

    if ":" in raw:
        candidates.add(raw.split(":", 1)[1].strip())

    for part in SPLIT_RE.split(raw):
        part = TRAILING_VOLUME_RE.sub(" ", part).strip()
        if part:
            candidates.add(part)

    cleaned = normalize(raw)
    if cleaned:
        candidates.add(cleaned)

    stripped = raw
    for _ in range(3):
        new_value = GENERIC_PREFIX_RE.sub(" ", stripped).strip()
        new_value = TRAILING_VOLUME_RE.sub(" ", new_value).strip()
        if new_value == stripped:
            break
        stripped = new_value
        if stripped:
            candidates.add(stripped)

    return [candidate for candidate in candidates if candidate and candidate.strip()]


def match_token_overlap(book_tokens, conf_tokens):
    if not book_tokens or not conf_tokens:
        return 0.0
    overlap = len(book_tokens & conf_tokens)
    if overlap == 0:
        return 0.0
    return overlap / max(len(book_tokens), len(conf_tokens))


def load_manual_aliases(valid_conf_ids):
    """
    Read curated aliases from CSV.

    Matching is case-insensitive by design: both the alias CSV and incoming
    DBLP booktitles are normalized via canonical_key() before lookup.
    """
    alias_map = {}

    if not os.path.exists(MANUAL_ALIAS_CSV):
        return alias_map

    with open(MANUAL_ALIAS_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"booktitle", "conf_id"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"{MANUAL_ALIAS_CSV} must contain at least the columns: "
                "booktitle, conf_id"
            )

        for line_no, row in enumerate(reader, start=2):
            booktitle = (row.get("booktitle") or "").strip()
            conf_id_raw = (row.get("conf_id") or "").strip()
            if not booktitle and not conf_id_raw:
                continue
            if not booktitle or not conf_id_raw:
                print(
                    f"WARNING: ignoring incomplete manual alias at line {line_no}: "
                    f"{row!r}"
                )
                continue
            try:
                conf_id = int(conf_id_raw)
            except ValueError:
                print(
                    f"WARNING: ignoring manual alias with non-integer conf_id at "
                    f"line {line_no}: {conf_id_raw!r}"
                )
                continue
            if conf_id not in valid_conf_ids:
                print(
                    f"WARNING: ignoring manual alias with unknown conf_id at "
                    f"line {line_no}: {conf_id}"
                )
                continue

            key = canonical_key(booktitle)
            if not key:
                continue
            alias_map[key] = conf_id

    return alias_map


def ensure_manual_alias_file():
    """Create the curated alias CSV with audit columns if it does not exist yet.

    WARNING: The seed conf_id values below (930 for NeurIPS, 251 for Interspeech)
    are specific to the current pipeline's auto-increment history. If the
    conferences table is rebuilt with different row ordering, re-verify them with:
        SELECT conf_id FROM conferences WHERE acronym IN ('NeurIPS', 'Interspeech');
    """
    if os.path.exists(MANUAL_ALIAS_CSV):
        return

    with open(MANUAL_ALIAS_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["booktitle", "conf_id", "notes", "date_added", "added_by"])
        writer.writerow(["nips", "930", "Legacy acronym for NeurIPS", "2026-03-18", "Codex"])
        writer.writerow(["EuRoSpEeCh", "251", "Legacy series merged into Interspeech", "2026-03-18", "Codex"])
        writer.writerow(["icslp", "251", "Legacy series merged into Interspeech", "2026-03-18", "Codex"])


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT conf_id, acronym, title FROM conferences")
    db_confs = cur.fetchall()
    cur.close()
    conn.close()

    acronym_map = {}
    title_map = {}
    title_tokens = []
    conf_titles_by_id = {}
    conf_acronyms_by_id = {}
    valid_conf_ids = set()

    for conf_id, acronym, title in db_confs:
        valid_conf_ids.add(conf_id)
        conf_titles_by_id[conf_id] = title or ""
        conf_acronyms_by_id[conf_id] = acronym or ""
        if acronym:
            acronym_map[compact(acronym)] = conf_id
        if title:
            title_map[normalize(title)] = conf_id
            title_tokens.append((conf_id, title, token_set(title)))

    ensure_manual_alias_file()
    manual_aliases = load_manual_aliases(valid_conf_ids)

    booktitle_counts = Counter()
    with open(INPROC_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bt = (row.get("booktitle") or "").strip()
            if bt:
                booktitle_counts[bt] += 1

    booktitles = sorted(booktitle_counts)
    total_rows = sum(booktitle_counts.values())
    print(f"Distinct booktitles to match: {len(booktitles):,}")
    print(f"Conference paper rows to classify: {total_rows:,}")
    print(f"Manual alias entries loaded: {len(manual_aliases):,}")

    matched_distinct = 0
    matched_rows = 0
    unmatched = []
    unmatched_rows = 0
    match_counts = defaultdict(int)
    match_rows = defaultdict(int)

    def record_match(booktitle, conf_id, match_type, score, writer):
        nonlocal matched_distinct, matched_rows
        row_count = booktitle_counts[booktitle]
        writer.writerow([booktitle, conf_id, match_type, f"{score:.2f}"])
        matched_distinct += 1
        matched_rows += row_count
        match_counts[match_type] += 1
        match_rows[match_type] += row_count

    def score_and_match(booktitle):
        manual_key = canonical_key(booktitle)
        if manual_key in manual_aliases:
            return manual_aliases[manual_key], "manual-alias", 1.0, "manual"

        for candidate in candidate_strings(booktitle):
            norm_candidate = normalize(candidate)
            compact_candidate = compact(candidate)

            if compact_candidate in acronym_map:
                return acronym_map[compact_candidate], "normalized-acronym", 1.0, candidate
            if norm_candidate in title_map:
                return title_map[norm_candidate], "title", 1.0, candidate

            legacy_target = LEGACY_ACRONYM_ALIASES.get(compact_candidate)
            if legacy_target and legacy_target in acronym_map:
                return acronym_map[legacy_target], "legacy-alias", 0.99, candidate

            for part in SPLIT_RE.split(candidate):
                part = part.strip()
                if not part:
                    continue
                part_compact = compact(part)
                part_norm = normalize(part)
                if part_compact in acronym_map:
                    return acronym_map[part_compact], "segment-acronym", 0.98, part
                if part_norm in title_map:
                    return title_map[part_norm], "segment-title", 0.98, part

                legacy_target = LEGACY_ACRONYM_ALIASES.get(part_compact)
                if legacy_target and legacy_target in acronym_map:
                    return acronym_map[legacy_target], "legacy-alias", 0.97, part

        if contains_variant_marker(booktitle):
            for token in extract_acronym_mentions(booktitle):
                compact_token = compact(token)
                conf_id = acronym_map.get(compact_token)
                if conf_id:
                    return conf_id, "parent-variant", 0.95, token

                legacy_target = LEGACY_ACRONYM_ALIASES.get(compact_token)
                if legacy_target and legacy_target in acronym_map:
                    return acronym_map[legacy_target], "parent-variant", 0.94, token

        for candidate in candidate_strings(booktitle):
            mentions = extract_acronym_mentions(candidate)
            for token in mentions:
                compact_token = compact(token)
                conf_id = acronym_map.get(compact_token)
                if conf_id and is_safe_embedded_acronym_context(candidate, token):
                    return conf_id, "embedded-acronym", 0.96, token

                legacy_target = LEGACY_ACRONYM_ALIASES.get(compact_token)
                if legacy_target and legacy_target in acronym_map and is_safe_embedded_acronym_context(candidate, token):
                    return acronym_map[legacy_target], "embedded-acronym", 0.95, token

        book_tokens = token_set(booktitle)
        if book_tokens:
            best = None
            best_score = 0.0
            for conf_id, title, conf_tokens in title_tokens:
                score = match_token_overlap(book_tokens, conf_tokens)
                if score > best_score:
                    best = (conf_id, title, score)
                    best_score = score

            if best:
                conf_id, title, score = best
                threshold = 0.80 if len(book_tokens) <= 2 else 0.67 if len(book_tokens) <= 4 else 0.75
                if score >= threshold:
                    return conf_id, "token-overlap", score, title

        return None

    with open(OUT_MAPPING, "w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["booktitle", "conf_id", "match_type", "score"])

        for bt in booktitles:
            result = score_and_match(bt)
            if result:
                conf_id, match_type, score, _matched_value = result
                record_match(bt, conf_id, match_type, score, writer)
            else:
                writer.writerow([bt, "", "unmatched", "0.00"])
                unmatched.append(bt)
                unmatched_rows += booktitle_counts[bt]

    with open(OUT_UNMATCHED, "w", encoding="utf-8") as fu:
        fu.write(f"# {len(unmatched)} unmatched booktitles\n")
        fu.write(f"# {unmatched_rows} unmatched conference rows\n")
        fu.write("# Review these and add curated aliases where justified.\n\n")
        for bt in unmatched:
            fu.write(bt + "\n")

    with open(OUT_UNMATCHED_BY_ROWS, "w", encoding="utf-8") as fu:
        fu.write(f"# {len(unmatched)} unmatched booktitles sorted by row frequency\n")
        fu.write(f"# {unmatched_rows} unmatched conference rows\n")
        fu.write("# rows\tbooktitle\n\n")
        for bt, rows in sorted(
            ((bt, booktitle_counts[bt]) for bt in unmatched),
            key=lambda item: (-item[1], item[0].casefold()),
        ):
            fu.write(f"{rows}\t{bt}\n")

    unmatched_distinct = len(unmatched)
    pct_distinct = (matched_distinct / len(booktitles) * 100) if booktitles else 0
    pct_rows = (matched_rows / total_rows * 100) if total_rows else 0

    print(f"Matched distinct booktitles: {matched_distinct:,} / {len(booktitles):,} ({pct_distinct:.1f}%)")
    print(f"Matched conference rows: {matched_rows:,} / {total_rows:,} ({pct_rows:.1f}%)")
    print(f"Unmatched distinct booktitles: {unmatched_distinct:,}")
    print(f"Unmatched conference rows: {unmatched_rows:,}")
    print("Breakdown by match type:")
    for key in sorted(match_counts):
        print(
            f"  {key}: {match_counts[key]:,} distinct / "
            f"{match_rows[key]:,} rows"
        )

    print("Top unmatched booktitles by row count:")
    for bt, rows in sorted(
        ((bt, booktitle_counts[bt]) for bt in unmatched),
        key=lambda item: (-item[1], item[0].casefold()),
    )[:20]:
        print(f"  {rows:,}  {bt}")

    print(f"Mapping:            {OUT_MAPPING}")
    print(f"Unmatched:          {OUT_UNMATCHED}")
    print(f"Unmatched by rows:  {OUT_UNMATCHED_BY_ROWS}")
    print(f"Manual aliases:     {MANUAL_ALIAS_CSV}")


if __name__ == "__main__":
    print("Matching DBLP booktitles to conferences table...")
    main()
