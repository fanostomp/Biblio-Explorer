"""
06_match_journals.py
--------------------
Match DBLP journal names onto the local journals lookup table.

The matcher now uses:
  1. Manual overrides from journal_manual_aliases.csv
  2. Exact/local normalized title matching
  3. Local DBLP article-registry candidates from data/dblp_dataset/input_article.csv
  4. Derived acronym and alternate-title handling for abbreviation-heavy titles
  5. Optional cached DBLP/OpenAlex/Crossref lookups
  6. Conservative prefix-overlap fallback
  7. Review queues for unresolved or low-margin cases

Environment variables:
  - BIBEXP_MATCH_CACHE_DIR
  - BIBEXP_DBLP_DATASET_DIR
  - BIBEXP_ENABLE_DBLP_ONLINE
  - BIBEXP_ENABLE_OPENALEX
  - BIBEXP_ENABLE_CROSSREF
  - BIBEXP_OPENALEX_EMAIL
  - BIBEXP_CROSSREF_MAILTO
  - BIBEXP_DBLP_API_BASE
  - BIBEXP_OPENALEX_API_BASE
  - BIBEXP_CROSSREF_API_BASE

Run: python etl/06_match_journals.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import mysql.connector

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_CONFIG
from venue_matching import (
    COMPILED_JOURNAL_ABBREVIATIONS,
    ExternalSourceCandidate,
    FileBackedJsonCache,
    JsonApiClient,
    JOURNAL_ABBREVIATIONS,
    MatchCandidate,
    MatchResult,
    ManualOverride,
    VenueMatcher,
    VenueNormalizer,
    VenueRecord,
    build_blocked_reason,
    build_intentional_null_audit_sample,
    build_dblp_csv_index,
    classify_match_stage,
    classify_journal_suggested_action,
    clean_for_match,
    compact_key,
    expand_journal_abbreviations,
    load_persistent_venue_aliases,
    normalize_issn,
    split_journal_review_rows_by_action,
    strip_parenthetical_notes,
    sync_alias_review_statuses,
    sync_review_statuses_from_resolved_rows,
    upsert_alias_review_records,
    write_match_audit_rows,
)


CLEANED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cleaned")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "matched")
os.makedirs(OUT_DIR, exist_ok=True)
DBLP_DATASET_DIR = os.environ.get(
    "BIBEXP_DBLP_DATASET_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "dblp_dataset"),
)

ARTICLES_CSV = os.path.join(CLEANED_DIR, "cleaned_articles.csv")
OUT_MAPPING = os.path.join(OUT_DIR, "journal_name_to_id.csv")
OUT_UNMATCHED = os.path.join(OUT_DIR, "unmatched_journals.txt")
OUT_REVIEW = os.path.join(OUT_DIR, "journal_match_review.csv")
OUT_PROPOSALS = os.path.join(OUT_DIR, "journal_alias_proposals.csv")
OUT_APPROVE_CANDIDATES = os.path.join(OUT_DIR, "journal_alias_approve_candidates.csv")
OUT_INTENTIONAL_NULL_CANDIDATES = os.path.join(OUT_DIR, "journal_intentional_null_candidates.csv")
OUT_INTENTIONAL_NULL_AUDIT = os.path.join(OUT_DIR, "journal_intentional_null_audit.csv")
OUT_VARIANT_CACHE = os.path.join(OUT_DIR, "journal_variant_cache.csv")
OUT_ALIAS_REVIEW = os.path.join(OUT_DIR, "venue_alias_review.csv")
OUT_ALIAS_MEMORY = os.path.join(OUT_DIR, "venue_aliases.csv")
OUT_MATCH_AUDIT = os.path.join(OUT_DIR, "venue_match_audit.csv")
MANUAL_ALIAS_CSV = os.path.join(OUT_DIR, "journal_manual_aliases.csv")
CACHE_DIR = os.environ.get(
    "BIBEXP_MATCH_CACHE_DIR",
    os.path.join(OUT_DIR, "cache", "venue_matching"),
)
TOP_REVIEW_CANDIDATES = 3
ABBREV_MAP = JOURNAL_ABBREVIATIONS
COMPILED_ABBREV = COMPILED_JOURNAL_ABBREVIATIONS

STOPWORDS = {
    "of",
    "on",
    "the",
    "and",
    "in",
    "for",
    "to",
    "a",
    "an",
    "with",
    "its",
    "at",
    "by",
    "from",
}
OVERLAP_THRESHOLD = 0.40
SHORT_NAME_OVERLAP_THRESHOLD = 0.70
LOW_CONFIDENCE_LIMIT = 20
_LOCAL_DBLP_CSV_CACHE = None


def console_safe(text):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


def expand_abbrev(text):
    return expand_journal_abbreviations((text or "").lower())


def normalize(text):
    return clean_for_match(expand_abbrev(text))


def canonical_key(text):
    return normalize(text).casefold()


def tokens(text):
    return {
        token
        for token in clean_for_match(text or "").split()
        if token and token not in STOPWORDS
    }


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


def ensure_manual_alias_file():
    if os.path.exists(MANUAL_ALIAS_CSV):
        return

    with open(MANUAL_ALIAS_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "dblp_journal_name",
                "journal_id",
                "action",
                "journal_title",
                "notes",
                "date_added",
                "added_by",
            ]
        )


def load_manual_aliases(valid_journal_ids, manual_alias_csv=MANUAL_ALIAS_CSV, by_title=None):
    alias_map = {}

    if not os.path.exists(manual_alias_csv):
        return alias_map

    with open(manual_alias_csv, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"dblp_journal_name", "action"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            print(
                f"WARNING: skipping manual alias file {manual_alias_csv}: "
                "missing required columns dblp_journal_name, action"
            )
            return alias_map

        for line_no, row in enumerate(reader, start=2):
            dblp_name = (row.get("dblp_journal_name") or "").strip()
            journal_id_raw = (row.get("journal_id") or "").strip()
            journal_title = clean_for_match(row.get("journal_title") or "")
            action = (row.get("action") or "").strip().casefold()

            if not dblp_name and not journal_id_raw and not action:
                continue
            if not dblp_name or action not in {"match", "unmatch", "skip", "non_venue"}:
                print(
                    f"WARNING: ignoring invalid manual alias at line {line_no}: "
                    f"{row!r}"
                )
                continue

            journal_id = None
            if action == "match":
                if journal_id_raw:
                    try:
                        journal_id = int(journal_id_raw)
                    except ValueError:
                        print(
                            f"WARNING: ignoring manual alias with non-integer journal_id "
                            f"at line {line_no}: {journal_id_raw!r}"
                        )
                        continue
                elif journal_title and by_title:
                    journal_id = by_title.get(journal_title)

                if journal_id not in valid_journal_ids:
                    print(
                        f"WARNING: ignoring manual alias with unknown journal_id at "
                        f"line {line_no}: {journal_id!r}"
                    )
                    continue

            key = canonical_key(dblp_name)
            if key:
                alias_map[key] = {
                    "action": action,
                    "journal_id": journal_id,
                    "journal_title": (row.get("journal_title") or "").strip(),
                    "notes": (row.get("notes") or "").strip(),
                    "date_added": (row.get("date_added") or "").strip(),
                    "added_by": (row.get("added_by") or "").strip(),
                }

    return alias_map


def load_variant_cache(path=OUT_VARIANT_CACHE):
    cache = {}
    if not os.path.exists(path):
        return cache

    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            journal_id_raw = (row.get("journal_id") or "").strip()
            if not journal_id_raw:
                continue
            try:
                journal_id = int(journal_id_raw)
            except ValueError:
                continue

            entry = cache.setdefault(
                journal_id,
                {
                    "alternate_titles": set(),
                    "abbreviated_titles": set(),
                    "issn": set(),
                    "issn_l": "",
                },
            )
            variant = (row.get("variant") or "").strip()
            variant_type = (row.get("variant_type") or "").strip().casefold()
            if variant:
                if variant_type == "abbreviated":
                    entry["abbreviated_titles"].add(variant)
                else:
                    entry["alternate_titles"].add(variant)
            issn = (row.get("issn") or "").strip()
            issn_l = (row.get("issn_l") or "").strip()
            if issn:
                for value in issn.split(";"):
                    value = value.strip()
                    if value:
                        entry["issn"].add(value)
            if issn_l:
                entry["issn_l"] = issn_l

    return cache


def load_journal_records(cursor, variant_cache=None):
    cursor.execute("SELECT journal_id, title, COALESCE(dblp_name, '') FROM journals")
    rows = cursor.fetchall()
    variant_cache = variant_cache or {}
    records = []
    for journal_id, title, dblp_name in rows:
        cached = variant_cache.get(journal_id, {})
        alternate_titles = set(cached.get("alternate_titles", set()))
        if dblp_name and clean_for_match(dblp_name) != clean_for_match(title or ""):
            alternate_titles.add(dblp_name)
        metadata = {
            "alternate_titles": tuple(sorted(alternate_titles)),
            "abbreviated_titles": tuple(sorted(cached.get("abbreviated_titles", set()))),
            "issn": tuple(sorted(cached.get("issn", set()))),
            "issn_l": cached.get("issn_l") or None,
            "dblp_name": dblp_name or None,
        }
        records.append(VenueRecord(journal_id, title or "", metadata=metadata))
    return records


def load_journal_name_counts():
    counts = Counter()
    with open(ARTICLES_CSV, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            journal_name = (row.get("journal") or "").strip()
            if journal_name:
                counts[journal_name] += 1
    return counts


def get_local_dblp_csv_index():
    global _LOCAL_DBLP_CSV_CACHE
    if _LOCAL_DBLP_CSV_CACHE is not None:
        return _LOCAL_DBLP_CSV_CACHE
    index = build_dblp_csv_index(
        dataset_dir=DBLP_DATASET_DIR,
        cache_dir=CACHE_DIR,
        persist_dir=OUT_DIR,
    )
    _LOCAL_DBLP_CSV_CACHE = index
    return index


def journal_variant_match_keys(value):
    text = (value or "").strip()
    if not text:
        return set()
    normalizer = VenueNormalizer("journal")
    forms = normalizer.normalize(text)
    keys = {
        forms.strict,
        forms.loose,
        forms.parent,
        clean_for_match(strip_parenthetical_notes(text)),
    }
    return {key for key in keys if key}


def journal_record_match_keys(record):
    keys = set()
    for value in [
        record.title,
        record.metadata.get("dblp_name"),
        *(record.metadata.get("alternate_titles") or ()),
        *(record.metadata.get("abbreviated_titles") or ()),
    ]:
        keys.update(journal_variant_match_keys(str(value or "")))
    return keys


def build_local_dblp_journal_registry_map(records, local_dblp_index):
    record_ids_by_key = {}
    for record in records:
        for key in journal_record_match_keys(record):
            record_ids_by_key.setdefault(key, set()).add(record.venue_id)

    registry_map = {}
    registry_payloads = local_dblp_index.get("journal_registry", {})
    for journal_key, payload in registry_payloads.items():
        matched_ids = set()
        for value in [
            payload.get("dominant_title", ""),
            *(payload.get("alternate_titles", []) or []),
            *(payload.get("abbreviated_titles", []) or []),
        ]:
            for key in journal_variant_match_keys(str(value or "")):
                matched_ids.update(record_ids_by_key.get(key, set()))
        if len(matched_ids) == 1:
            registry_map[journal_key] = next(iter(matched_ids))
    return registry_map


def resolve_local_dblp_exact_series_match(
    journal_name,
    alias_counts,
    alias_payloads,
    journal_registry_map,
    records_by_id,
):
    query_key = compact_key(journal_name)
    exact_candidates = []

    for journal_key, payload in alias_payloads.items():
        if compact_key(payload.get("dominant_title", "")) != query_key:
            continue
        journal_id = journal_registry_map.get(journal_key)
        if journal_id is None or journal_id not in records_by_id:
            continue
        record = records_by_id[journal_id]
        exact_candidates.append(
            (
                alias_counts.get(journal_key, 0),
                journal_key,
                MatchCandidate(
                    journal_id,
                    record.title,
                    "dblp-local-csv-exact-series",
                    0.95,
                    "dblp_local_csv",
                ),
            )
        )

    if not exact_candidates:
        return None

    ranked_exact_candidates = [
        candidate
        for _count, _journal_key, candidate in sorted(
            exact_candidates,
            key=lambda item: (-item[0], item[1]),
        )
    ]
    if len({candidate.venue_id for candidate in ranked_exact_candidates}) != 1:
        return None

    top_candidate = ranked_exact_candidates[0]
    return MatchResult(
        "matched",
        top_candidate.venue_id,
        top_candidate.match_type,
        top_candidate.score,
        "high",
        "dblp_local_csv",
        tuple(ranked_exact_candidates[:TOP_REVIEW_CANDIDATES]),
    )


def resolve_local_dblp_journal_match(
    journal_name,
    records,
    local_dblp_index,
    journal_registry_map=None,
):
    normalizer = VenueNormalizer("journal")
    records_by_id = {record.venue_id: record for record in records}
    journal_registry_map = journal_registry_map or build_local_dblp_journal_registry_map(
        records,
        local_dblp_index,
    )
    alias_counts = Counter()
    alias_payloads = {}
    query_keys = {
        normalizer.normalize(journal_name).strict,
        normalizer.normalize(journal_name).loose,
        normalizer.normalize(journal_name).parent,
        clean_for_match(strip_parenthetical_notes(journal_name)),
        normalize(journal_name),
    }
    query_keys.update(normalizer.normalize(journal_name).acronym_candidates)

    for key in {value for value in query_keys if value}:
        for payload in local_dblp_index.get("journal_aliases", {}).get(key, []):
            journal_key = (payload.get("journal_key") or "").strip()
            if not journal_key:
                continue
            alias_counts[journal_key] += int(payload.get("count") or 1)
            alias_payloads.setdefault(journal_key, payload)

    if not alias_counts:
        return MatchResult("unmatched", None, "unmatched", 0.0, "unmatched", "none", ())

    if len(alias_counts) > 1:
        exact_series_result = resolve_local_dblp_exact_series_match(
            journal_name,
            alias_counts,
            alias_payloads,
            journal_registry_map,
            records_by_id,
        )
        if exact_series_result is not None:
            return exact_series_result

    mapped_candidates = []
    for journal_key, count in alias_counts.items():
        journal_id = journal_registry_map.get(journal_key)
        if journal_id is None or journal_id not in records_by_id:
            continue
        record = records_by_id[journal_id]
        mapped_candidates.append(
            MatchCandidate(
                journal_id,
                record.title,
                "dblp-local-csv-registry",
                0.95,
                "dblp_local_csv",
            )
        )

    unique_candidates = {}
    for candidate in mapped_candidates:
        unique_candidates[candidate.venue_id] = candidate
    ranked_candidates = sorted(
        unique_candidates.values(),
        key=lambda candidate: (-alias_counts.get(next(
            (
                key
                for key, mapped_id in journal_registry_map.items()
                if mapped_id == candidate.venue_id
            ),
            "",
        ), 0), candidate.title.casefold()),
    )

    if not ranked_candidates:
        return MatchResult("unmatched", None, "unmatched", 0.0, "unmatched", "none", ())

    unique_total_series = set(alias_counts)
    unique_mapped_ids = {candidate.venue_id for candidate in ranked_candidates}
    top_candidate = ranked_candidates[0]

    if len(unique_total_series) == 1 and len(unique_mapped_ids) == 1:
        return MatchResult(
            "matched",
            top_candidate.venue_id,
            "dblp-local-csv-registry",
            top_candidate.score,
            "high",
            "dblp_local_csv",
            tuple(ranked_candidates[:TOP_REVIEW_CANDIDATES]),
        )

    return MatchResult(
        "review",
        None,
        "dblp-local-csv-registry",
        top_candidate.score,
        "review",
        "dblp_local_csv",
        tuple(ranked_candidates[:TOP_REVIEW_CANDIDATES]),
    )


def collect_local_dblp_journal_alias_payloads(journal_name, local_dblp_index):
    normalizer = VenueNormalizer("journal")
    query_keys = {
        normalizer.normalize(journal_name).strict,
        normalizer.normalize(journal_name).loose,
        normalizer.normalize(journal_name).parent,
        clean_for_match(strip_parenthetical_notes(journal_name)),
        normalize(journal_name),
    }
    query_keys.update(normalizer.normalize(journal_name).acronym_candidates)

    alias_counts = Counter()
    alias_payloads = {}
    for key in {value for value in query_keys if value}:
        for payload in local_dblp_index.get("journal_aliases", {}).get(key, []):
            journal_key = (payload.get("journal_key") or "").strip()
            if not journal_key:
                continue
            alias_counts[journal_key] += int(payload.get("count") or 1)
            alias_payloads.setdefault(journal_key, payload)
    return alias_counts, alias_payloads


def build_local_dblp_exact_series_collision_reason(
    journal_name,
    local_dblp_index,
    journal_registry_map,
):
    reason = build_local_dblp_series_blocked_reason(
        journal_name,
        local_dblp_index,
        journal_registry_map,
    )
    if reason.startswith("exact-series-collision: "):
        return reason
    return ""


def build_local_dblp_series_blocked_reason(
    journal_name,
    local_dblp_index,
    journal_registry_map,
):
    alias_counts, alias_payloads = collect_local_dblp_journal_alias_payloads(
        journal_name,
        local_dblp_index,
    )
    if not alias_payloads:
        return ""

    query_key = compact_key(journal_name)
    exact_payloads = [
        (alias_counts.get(journal_key, 0), journal_key, payload)
        for journal_key, payload in alias_payloads.items()
        if compact_key(payload.get("dominant_title", "")) == query_key
    ]
    if not exact_payloads:
        return ""

    _exact_count, exact_journal_key, exact_payload = max(exact_payloads)
    if journal_registry_map.get(exact_journal_key) is not None:
        return ""

    exact_title = (exact_payload.get("dominant_title") or journal_name).strip()
    competing_titles = [
        (alias_counts.get(journal_key, 0), (payload.get("dominant_title") or "").strip())
        for journal_key, payload in alias_payloads.items()
        if journal_key != exact_journal_key
        and (payload.get("dominant_title") or "").strip()
        and compact_key(payload.get("dominant_title", "")) != query_key
    ]
    if competing_titles:
        _competing_count, competing_title = max(competing_titles)
        return (
            f'exact-series-collision: exact DBLP series "{exact_title}" '
            f'is unmapped locally, while alias also expands to "{competing_title}"'
        )

    return (
        f'no-ranked-continuation: exact DBLP series "{exact_title}" '
        "has no approved ranked continuation in the local authority layer"
    )


def parse_dblp_api_payload(payload):
    if not payload:
        return []

    hits = (
        payload.get("result", {})
        .get("hits", {})
        .get("hit", [])
    )
    if isinstance(hits, dict):
        hits = [hits]

    candidates = []
    for hit in hits:
        info = hit.get("info", {}) if isinstance(hit, dict) else {}
        display_name = (
            info.get("venue")
            or info.get("title")
            or info.get("label")
            or ""
        ).strip()
        if not display_name:
            continue
        candidates.append(
            ExternalSourceCandidate(
                source="dblp_api",
                display_name=display_name,
                alternate_titles=tuple(filter(None, [info.get("venue"), info.get("title")])),
                source_type=info.get("type"),
            )
        )
    return candidates


def parse_openalex_payload(payload):
    if not payload:
        return []

    candidates = []
    for result in payload.get("results", []):
        display_name = (result.get("display_name") or "").strip()
        if not display_name:
            continue
        alternate_titles = tuple(result.get("alternate_titles") or [])
        abbreviated_title = (result.get("abbreviated_title") or "").strip() or None
        issn = tuple(result.get("issn") or [])
        issn_l = (result.get("issn_l") or "").strip() or None
        candidates.append(
            ExternalSourceCandidate(
                source="openalex",
                display_name=display_name,
                alternate_titles=alternate_titles,
                abbreviated_title=abbreviated_title,
                issn=issn,
                issn_l=issn_l,
                source_type=result.get("type"),
            )
        )
    return candidates


def parse_crossref_payload(payload):
    if not payload:
        return []

    candidates = []
    for item in payload.get("message", {}).get("items", []):
        container_titles = tuple(item.get("container-title") or [])
        short_titles = tuple(item.get("short-container-title") or [])
        issn = tuple(item.get("ISSN") or [])
        display_name = next((title for title in container_titles if title), "").strip()
        if not display_name:
            continue
        candidates.append(
            ExternalSourceCandidate(
                source="crossref",
                display_name=display_name,
                alternate_titles=short_titles,
                issn=issn,
                source_type=item.get("type"),
            )
        )
    return candidates


def build_external_candidates(query, include_local_dblp=True):
    candidates = []
    cache = FileBackedJsonCache(CACHE_DIR)
    normalized_query = normalize(query)
    local_dblp_index = get_local_dblp_csv_index()

    if include_local_dblp and local_dblp_index:
        lookup_keys = {normalized_query, clean_for_match(query)}
        lookup_keys.update(VenueNormalizer("journal").normalize(query).acronym_candidates)
        seen_local_keys = set()
        for key in lookup_keys:
            for entry in local_dblp_index.get("journal_aliases", {}).get(key, []):
                journal_key = (entry.get("journal_key") or "").strip()
                if not journal_key or journal_key in seen_local_keys:
                    continue
                seen_local_keys.add(journal_key)
                alternate_titles = tuple(entry.get("alternate_titles") or [])
                abbreviated_titles = tuple(entry.get("abbreviated_titles") or [])
                candidates.append(
                    ExternalSourceCandidate(
                        source="dblp_local_csv",
                        display_name=(entry.get("dominant_title") or "").strip(),
                        alternate_titles=tuple([*alternate_titles, *abbreviated_titles]),
                        abbreviated_title=abbreviated_titles[0] if abbreviated_titles else None,
                        source_type="journal",
                    )
                )

    if os.environ.get("BIBEXP_ENABLE_DBLP_ONLINE", "0") == "1":
        client = JsonApiClient(
            cache=cache,
            namespace="dblp_api",
            base_url=os.environ.get("BIBEXP_DBLP_API_BASE", "https://dblp.org/search/venue/api"),
            enabled=True,
        )
        payload = client.fetch_json(
            query_key=query,
            params={"q": query, "h": "5", "format": "json"},
        )
        candidates.extend(parse_dblp_api_payload(payload))

    if os.environ.get("BIBEXP_ENABLE_OPENALEX", "0") == "1":
        params = {"search": query, "per-page": "5"}
        mailto = os.environ.get("BIBEXP_OPENALEX_EMAIL", "").strip()
        if mailto:
            params["mailto"] = mailto
        client = JsonApiClient(
            cache=cache,
            namespace="openalex_sources",
            base_url=os.environ.get("BIBEXP_OPENALEX_API_BASE", "https://api.openalex.org/sources"),
            enabled=True,
        )
        payload = client.fetch_json(query_key=query, params=params)
        candidates.extend(parse_openalex_payload(payload))

    if os.environ.get("BIBEXP_ENABLE_CROSSREF", "0") == "1":
        params = {"query.container-title": query, "rows": "5"}
        mailto = os.environ.get("BIBEXP_CROSSREF_MAILTO", "").strip()
        if mailto:
            params["mailto"] = mailto
        client = JsonApiClient(
            cache=cache,
            namespace="crossref_container",
            base_url=os.environ.get("BIBEXP_CROSSREF_API_BASE", "https://api.crossref.org/works"),
            enabled=True,
        )
        payload = client.fetch_json(query_key=query, params=params)
        candidates.extend(parse_crossref_payload(payload))

    unique = {}
    for candidate in candidates:
        key = (candidate.source, compact_key(candidate.display_name), candidate.issn_l or "")
        unique[key] = candidate
    return list(unique.values())


def review_action_for_result(result):
    if result.status == "matched":
        return "auto_accept"
    if result.status == "non_venue":
        return "non_venue"
    if result.status == "skipped":
        return "skip"
    return "review"


def classify_variant_type(journal_name, suggested_action):
    lowered = clean_for_match(journal_name)
    if suggested_action == "intentional null / likely not rankable":
        if "magazine" in lowered:
            return "magazine-like"
        if "newsletter" in lowered or "report" in lowered:
            return "report-like"
        return "non-rankable-likely"
    if compact_key(journal_name) == lowered.replace(" ", "") and len(compact_key(journal_name)) <= 8:
        return "abbreviation"
    return "title-variant"


def approval_ready_for_review(journal_name, result, candidates, blocked_reason, suggested_action):
    if suggested_action != "approve alias":
        return False
    if blocked_reason == "risky-abbreviation-near-tie":
        return False
    if len(candidates) != 1 or result.score < 0.95:
        return False
    if result.match_type not in {
        "metadata-alias",
        "dblp-local-csv-registry",
        "dblp-local-csv-exact-series",
    }:
        return False
    return compact_key(journal_name) != "jcp"


def freeze_risky_journal_match(journal_name, result):
    if result.status != "matched":
        return result

    runner_up_score = result.candidates[1].score if len(result.candidates) > 1 else 0.0
    score_gap = result.score - runner_up_score
    review_projection = MatchResult(
        "review",
        None,
        result.match_type,
        result.score,
        "review",
        result.evidence_source,
        result.candidates,
    )
    blocked_reason = build_blocked_reason(
        raw_name=journal_name,
        venue_type="journal",
        result=review_projection,
        score_gap=score_gap,
    )
    if compact_key(journal_name) == "jcp" and not result.match_type.endswith("-issn"):
        return review_projection
    if blocked_reason == "risky-abbreviation-near-tie":
        return review_projection
    return result


def build_review_row(
    journal_name,
    row_count,
    result,
    normalizer,
    *,
    local_dblp_index=None,
    journal_registry_map=None,
):
    forms = normalizer.normalize(journal_name)
    candidates = list(result.candidates[:TOP_REVIEW_CANDIDATES])
    runner_up_score = candidates[1].score if len(candidates) > 1 else 0.0
    score_gap = result.score - runner_up_score
    blocked_reason = build_blocked_reason(
        raw_name=journal_name,
        venue_type="journal",
        result=result,
        score_gap=score_gap,
    )
    if (
        local_dblp_index
        and journal_registry_map is not None
        and result.status in {"review", "unmatched"}
    ):
        exact_series_reason = build_local_dblp_series_blocked_reason(
            journal_name,
            local_dblp_index,
            journal_registry_map,
        )
        if exact_series_reason:
            blocked_reason = exact_series_reason
    suggested_action = classify_journal_suggested_action(journal_name, result)
    variant_type = classify_variant_type(journal_name, suggested_action)
    row = {
        "dblp_journal_name": journal_name,
        "normalized_strict": forms.strict,
        "normalized_loose": forms.loose,
        "normalized_parent": forms.parent,
        "row_count": row_count,
        "status": result.status,
        "recommended_action": review_action_for_result(result),
        "match_stage": classify_match_stage(result.match_type),
        "match_type": result.match_type,
        "best_score": f"{result.score:.2f}",
        "score_gap": f"{score_gap:.2f}",
        "top_candidate_margin": f"{score_gap:.2f}",
        "candidate_count": len(candidates),
        "evidence_source": result.evidence_source,
        "blocked_reason": blocked_reason,
        "suggested_action": suggested_action,
        "variant_type": variant_type,
        "approval_ready": "yes"
        if approval_ready_for_review(journal_name, result, candidates, blocked_reason, suggested_action)
        else "no",
    }

    for index in range(TOP_REVIEW_CANDIDATES):
        prefix = f"candidate_{index + 1}"
        if index < len(candidates):
            candidate = candidates[index]
            row[f"{prefix}_journal_id"] = candidate.venue_id
            row[f"{prefix}_title"] = candidate.title
            row[f"{prefix}_match_type"] = candidate.match_type
            row[f"{prefix}_stage"] = classify_match_stage(candidate.match_type)
            row[f"{prefix}_score"] = f"{candidate.score:.2f}"
            row[f"{prefix}_evidence_source"] = candidate.evidence_source
        else:
            row[f"{prefix}_journal_id"] = ""
            row[f"{prefix}_title"] = ""
            row[f"{prefix}_match_type"] = ""
            row[f"{prefix}_stage"] = ""
            row[f"{prefix}_score"] = ""
            row[f"{prefix}_evidence_source"] = ""
    return row


def build_manual_alias_review_evidence(journal_name, manual_alias, record):
    notes = (manual_alias.get("notes") or "").strip()
    payload = {
        "source": "manual-reviewed-alias",
        "raw_name": journal_name,
        "manual_alias_action": (manual_alias.get("action") or "").strip(),
        "resolved_title": record.title if record is not None else "",
    }
    if notes:
        payload["manual_alias_notes"] = notes
    reviewer = (manual_alias.get("added_by") or "").strip()
    reviewed_at = (manual_alias.get("date_added") or "").strip()
    if reviewer:
        payload["manual_alias_reviewed_by"] = reviewer
    if reviewed_at:
        payload["manual_alias_reviewed_at"] = reviewed_at
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def write_review_csv(review_rows):
    fieldnames = [
        "dblp_journal_name",
        "normalized_strict",
        "normalized_loose",
        "normalized_parent",
        "row_count",
        "status",
        "recommended_action",
        "match_stage",
        "match_type",
        "best_score",
        "score_gap",
        "top_candidate_margin",
        "candidate_count",
        "evidence_source",
        "blocked_reason",
        "suggested_action",
        "variant_type",
        "approval_ready",
    ]
    for index in range(TOP_REVIEW_CANDIDATES):
        prefix = f"candidate_{index + 1}"
        fieldnames.extend(
            [
                f"{prefix}_journal_id",
                f"{prefix}_title",
                f"{prefix}_match_type",
                f"{prefix}_stage",
                f"{prefix}_score",
                f"{prefix}_evidence_source",
            ]
        )

    with open(OUT_REVIEW, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in review_rows:
            writer.writerow(row)


def write_proposal_csv(review_rows):
    fieldnames = [
        "dblp_journal_name",
        "action",
        "journal_id",
        "journal_title",
        "journal_match_type",
        "match_stage",
        "score",
        "score_gap",
        "row_count",
        "evidence_source",
        "blocked_reason",
        "suggested_action",
        "recommended_action",
    ]
    with open(OUT_PROPOSALS, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in review_rows:
            writer.writerow(
                {
                    "dblp_journal_name": row["dblp_journal_name"],
                    "action": row["recommended_action"],
                    "journal_id": row["candidate_1_journal_id"],
                    "journal_title": row["candidate_1_title"],
                    "journal_match_type": row["candidate_1_match_type"],
                    "match_stage": row["candidate_1_stage"] or row["match_stage"],
                    "score": row["candidate_1_score"] or row["best_score"],
                    "score_gap": row["score_gap"],
                    "row_count": row["row_count"],
                    "evidence_source": row["candidate_1_evidence_source"] or row["evidence_source"],
                    "blocked_reason": row["blocked_reason"],
                    "suggested_action": row["suggested_action"],
                    "recommended_action": row["recommended_action"],
                }
            )


def _write_split_review_csv(path, fieldnames, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def merge_intentional_null_candidate_rows(review_rows, explicit_rows):
    merged_rows = []
    seen = set()

    for row in explicit_rows:
        raw_name = str(row.get("raw_journal_name") or "").strip()
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        merged_rows.append(
            {
                "raw_journal_name": raw_name,
                "rows": int(row.get("rows") or 0),
                "candidate_title": str(row.get("candidate_title") or ""),
                "score": str(row.get("score") or "0.00"),
                "current_reason": str(row.get("current_reason") or ""),
            }
        )

    for row in split_journal_review_rows_by_action(review_rows)["intentional_null"]:
        raw_name = str(row.get("dblp_journal_name") or "").strip()
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        merged_rows.append(
            {
                "raw_journal_name": raw_name,
                "rows": int(row.get("row_count") or 0),
                "candidate_title": str(row.get("candidate_1_title") or ""),
                "score": str(row.get("candidate_1_score") or row.get("best_score") or "0.00"),
                "current_reason": str(row.get("blocked_reason") or ""),
            }
        )

    return sorted(merged_rows, key=lambda row: (-int(row["rows"]), row["raw_journal_name"].casefold()))


def write_split_proposal_csvs(review_rows, intentional_null_rows, merged_intentional_null_rows=None):
    split_rows = split_journal_review_rows_by_action(review_rows)
    if merged_intentional_null_rows is None:
        merged_intentional_null_rows = merge_intentional_null_candidate_rows(
            review_rows,
            intentional_null_rows,
        )
    review_fieldnames = [
        "dblp_journal_name",
        "row_count",
        "approval_ready",
        "variant_type",
        "candidate_1_journal_id",
        "candidate_1_title",
        "candidate_1_match_type",
        "candidate_1_stage",
        "candidate_1_score",
        "top_candidate_margin",
        "candidate_count",
        "evidence_source",
        "blocked_reason",
        "suggested_action",
    ]
    intentional_null_fieldnames = [
        "raw_journal_name",
        "rows",
        "candidate_title",
        "score",
        "current_reason",
    ]
    _write_split_review_csv(OUT_APPROVE_CANDIDATES, review_fieldnames, split_rows["approve"])
    _write_split_review_csv(
        OUT_INTENTIONAL_NULL_CANDIDATES,
        intentional_null_fieldnames,
        merged_intentional_null_rows,
    )


def write_intentional_null_audit_csv(audit_rows):
    fieldnames = [
        "raw_journal_name",
        "rows",
        "candidate_title",
        "score",
        "current_reason",
        "audit_verdict",
        "notes",
    ]
    _write_split_review_csv(OUT_INTENTIONAL_NULL_AUDIT, fieldnames, audit_rows)


def build_alias_review_records(review_rows):
    records = []
    for row in review_rows:
        evidence_payload = {
            "row_count": int(row.get("row_count") or 0),
            "match_stage": row.get("match_stage", ""),
            "match_type": row.get("match_type", ""),
            "blocked_reason": row.get("blocked_reason", ""),
            "suggested_action": row.get("suggested_action", ""),
            "candidates": [
                {
                    "journal_id": row.get(f"candidate_{index}_journal_id", ""),
                    "title": row.get(f"candidate_{index}_title", ""),
                    "match_type": row.get(f"candidate_{index}_match_type", ""),
                    "stage": row.get(f"candidate_{index}_stage", ""),
                    "score": row.get(f"candidate_{index}_score", ""),
                }
                for index in range(1, TOP_REVIEW_CANDIDATES + 1)
                if row.get(f"candidate_{index}_journal_id") or row.get(f"candidate_{index}_title")
            ],
        }
        records.append(
            {
                "venue_type": "journal",
                "raw_name": row.get("dblp_journal_name", ""),
                "normalized_name": row.get("normalized_strict", ""),
                "proposed_canonical_id": row.get("candidate_1_journal_id", ""),
                "proposed_canonical_title": row.get("candidate_1_title", ""),
                "score": row.get("best_score", ""),
                "match_type": row.get("match_type", ""),
                "evidence_json": json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True),
                "status": "pending",
                "reviewed_by": "",
                "reviewed_at": "",
            }
        )
    return records


def write_variant_cache(variant_rows):
    fieldnames = [
        "journal_id",
        "journal_title",
        "variant",
        "variant_type",
        "source",
        "issn",
        "issn_l",
    ]
    with open(OUT_VARIANT_CACHE, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(
            variant_rows.values(),
            key=lambda item: (
                int(item["journal_id"]),
                item["variant_type"],
                item["variant"].casefold(),
                item["source"],
            ),
        ):
            writer.writerow(row)


def register_variant_row(variant_rows, journal_id, journal_title, variant, variant_type, source, issn="", issn_l=""):
    value = (variant or "").strip()
    if not journal_id or not value:
        return
    key = (int(journal_id), value.casefold(), variant_type, source, issn, issn_l)
    variant_rows[key] = {
        "journal_id": int(journal_id),
        "journal_title": journal_title,
        "variant": value,
        "variant_type": variant_type,
        "source": source,
        "issn": issn,
        "issn_l": issn_l,
    }


def external_candidate_supports_record(candidate, record):
    candidate_titles = {
        (candidate.display_name or "").strip(),
        *((value or "").strip() for value in candidate.alternate_titles),
    }
    if candidate.abbreviated_title:
        candidate_titles.add(candidate.abbreviated_title.strip())

    record_titles = {
        (record.title or "").strip(),
        *((value or "").strip() for value in record.metadata.get("alternate_titles", ()) or ()),
        *((value or "").strip() for value in record.metadata.get("abbreviated_titles", ()) or ()),
        (record.metadata.get("dblp_name") or "").strip(),
    }

    candidate_keys = set()
    for value in candidate_titles:
        candidate_keys.update(journal_variant_match_keys(value))
    record_keys = set()
    for value in record_titles:
        record_keys.update(journal_variant_match_keys(value))

    if candidate_keys & record_keys:
        return True

    candidate_issn = {
        normalize_issn(value)
        for value in (*candidate.issn, candidate.issn_l or "")
        if normalize_issn(value)
    }
    record_issn = {
        normalize_issn(value)
        for value in (*record.metadata.get("issn", ()), record.metadata.get("issn_l") or "")
        if normalize_issn(value)
    }
    return bool(candidate_issn & record_issn)


def main():
    local_dblp_index = get_local_dblp_csv_index()
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    records = load_journal_records(cur)
    cur.close()
    conn.close()

    valid_journal_ids = {record.venue_id for record in records}
    by_title = {
        clean_for_match(record.title): record.venue_id
        for record in records
        if clean_for_match(record.title)
    }
    persistent_aliases = load_persistent_venue_aliases(OUT_ALIAS_MEMORY, "journal")
    manual_aliases = load_manual_aliases(valid_journal_ids, MANUAL_ALIAS_CSV, by_title=by_title)
    manual_overrides = {
        key: ManualOverride(action=value["action"], venue_id=value["journal_id"])
        for key, value in manual_aliases.items()
    }
    manual_overrides = {**persistent_aliases, **manual_overrides}
    records_by_id = {record.venue_id: record for record in records}
    journal_registry_map = build_local_dblp_journal_registry_map(records, local_dblp_index)

    matcher = VenueMatcher(
        venue_type="journal",
        records=records,
        manual_overrides=manual_overrides,
    )
    normalizer = VenueNormalizer("journal")

    journal_name_counts = load_journal_name_counts()
    journal_names = sorted(journal_name_counts, key=lambda name: (-journal_name_counts[name], name.casefold()))
    db_journals_tokenized = [
        (record.venue_id, record.title, tokens(record.title))
        for record in records
        if record.title
    ]
    variant_rows = {}
    stage_counts = Counter()
    stage_rows = Counter()

    for alias, payload in manual_aliases.items():
        journal_id = payload["journal_id"]
        if payload["action"] == "match" and journal_id in records_by_id:
            register_variant_row(
                variant_rows,
                journal_id,
                records_by_id[journal_id].title,
                alias,
                "alternate",
                "manual",
            )

    print(f"Distinct DBLP journal names to match: {len(journal_names):,}")
    print(f"Manual journal alias entries loaded: {len(manual_aliases):,}")
    print(
        f"Local DBLP article CSV: {local_dblp_index.get('inputs', {}).get('article', {}).get('path', '')}"
    )
    print(
        "Local DBLP article columns: "
        + ", ".join(local_dblp_index.get("inputs", {}).get("article", {}).get("columns", []))
    )

    matched = 0
    unresolved = []
    skipped = []
    review_rows = []
    resolved_review_rows = []
    audit_rows = []
    intentional_null_candidates = []
    low_confidence_matches = []
    run_date = date.today().isoformat()

    with open(OUT_MAPPING, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "dblp_journal_name",
                "journal_id",
                "match_stage",
                "match_type",
                "evidence_source",
                "confidence",
                "score",
            ]
        )

        for journal_name in journal_names:
            row_count = journal_name_counts[journal_name]
            external_candidates = []
            result = matcher.match(journal_name)

            if result.status in {"unmatched", "review"}:
                registry_result = resolve_local_dblp_journal_match(
                    journal_name,
                    records,
                    local_dblp_index,
                    journal_registry_map=journal_registry_map,
                )
                if registry_result.status in {"matched", "review"}:
                    result = registry_result

            if result.status in {"unmatched", "review"}:
                external_candidates = build_external_candidates(journal_name, include_local_dblp=False)
                external_result = matcher.match(journal_name, external_candidates=external_candidates)
                if external_result.status in {"matched", "review"}:
                    result = external_result

            if result.status in {"unmatched", "review"}:
                source_tokens = tokens(journal_name)
                best_jid, best_title, best_tokens, best_score = find_best_overlap_match(
                    source_tokens,
                    db_journals_tokenized,
                )
                if should_accept_overlap(source_tokens, best_tokens, best_score):
                    confidence = classify_confidence(best_score)
                    result = result.__class__(
                        "matched",
                        best_jid,
                        f"prefix-overlap:{best_score:.2f}",
                        best_score,
                        confidence,
                        "local",
                        result.candidates,
                    )
            result = freeze_risky_journal_match(journal_name, result)
            match_stage = classify_match_stage(result.match_type)
            evidence_payload = {
                "status": result.status,
                "confidence": result.confidence,
                "evidence_source": result.evidence_source,
                "candidates": [
                    {
                        "journal_id": candidate.venue_id,
                        "title": candidate.title,
                        "match_type": candidate.match_type,
                        "score": round(candidate.score, 2),
                        "evidence_source": candidate.evidence_source,
                    }
                    for candidate in result.candidates[:TOP_REVIEW_CANDIDATES]
                ],
            }
            audit_rows.append(
                {
                    "venue_type": "journal",
                    "raw_name": journal_name,
                    "normalized_name": normalizer.normalize(journal_name).strict,
                    "canonical_id": result.venue_id or "",
                    "stage": match_stage,
                    "match_type": result.match_type if result.match_type else result.status,
                    "score": f"{result.score:.2f}",
                    "run_date": run_date,
                    "evidence_json": json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True),
                }
            )

            if result.status == "matched" and result.venue_id is not None:
                record = records_by_id.get(result.venue_id)
                manual_alias = manual_aliases.get(canonical_key(journal_name), {})
                resolved_review_rows.append(
                    {
                        "venue_type": "journal",
                        "raw_name": journal_name,
                        "normalized_name": normalizer.normalize(journal_name).strict,
                        "proposed_canonical_id": result.venue_id,
                        "proposed_canonical_title": record.title if record is not None else "",
                        "reviewed_by": (
                            manual_alias.get("added_by", "")
                            if result.evidence_source == "manual"
                            else ""
                        ),
                        "reviewed_at": (
                            manual_alias.get("date_added", "")
                            if result.evidence_source == "manual"
                            else ""
                        ),
                        "evidence_json": (
                            build_manual_alias_review_evidence(journal_name, manual_alias, record)
                            if result.evidence_source == "manual"
                            else ""
                        ),
                    }
                )
                confidence = classify_confidence(result.score)
                writer.writerow(
                    [
                        journal_name,
                        result.venue_id,
                        match_stage,
                        result.match_type,
                        result.evidence_source,
                        confidence,
                        f"{result.score:.2f}",
                    ]
                )
                matched += 1
                stage_counts[match_stage] += 1
                stage_rows[match_stage] += row_count
                if confidence == "low":
                    record_title = records_by_id[result.venue_id].title if result.venue_id in records_by_id else ""
                    low_confidence_matches.append(
                        (result.score, journal_name, result.venue_id, record_title)
                    )
                if result.venue_id in records_by_id:
                    record = records_by_id[result.venue_id]
                    if result.evidence_source == "manual":
                        register_variant_row(
                            variant_rows,
                            result.venue_id,
                            record.title,
                            journal_name,
                            "alternate",
                            "manual",
                        )
                    if result.match_type.startswith("dblp-local-csv"):
                        register_variant_row(
                            variant_rows,
                            result.venue_id,
                            record.title,
                            journal_name,
                            "alternate",
                            "dblp_local_csv",
                        )
                    for candidate in external_candidates:
                        if result.evidence_source not in {candidate.source, "manual"}:
                            continue
                        if not external_candidate_supports_record(candidate, record):
                            continue
                        register_variant_row(
                            variant_rows,
                            result.venue_id,
                            record.title,
                            candidate.display_name,
                            "alternate",
                            candidate.source,
                            issn=";".join(candidate.issn),
                            issn_l=candidate.issn_l or "",
                        )
                        for value in candidate.alternate_titles:
                            register_variant_row(
                                variant_rows,
                                result.venue_id,
                                record.title,
                                value,
                                "alternate",
                                candidate.source,
                                issn=";".join(candidate.issn),
                                issn_l=candidate.issn_l or "",
                            )
                        if candidate.abbreviated_title:
                            register_variant_row(
                                variant_rows,
                                result.venue_id,
                                record.title,
                                candidate.abbreviated_title,
                                "abbreviated",
                                candidate.source,
                                issn=";".join(candidate.issn),
                                issn_l=candidate.issn_l or "",
                            )
                continue

            confidence = result.confidence if result.confidence else "unmatched"
            writer.writerow(
                [
                    journal_name,
                    "",
                    match_stage,
                    result.match_type,
                    result.evidence_source,
                    confidence,
                    f"{result.score:.2f}",
                ]
            )

            if result.status in {"skipped", "non_venue"}:
                skipped.append((journal_name, row_count))
                stage_counts[match_stage] += 1
                stage_rows[match_stage] += row_count
                intentional_null_candidates.append(
                    {
                        "raw_journal_name": journal_name,
                        "rows": row_count,
                        "candidate_title": result.candidates[0].title if result.candidates else "",
                        "score": f"{result.score:.2f}",
                        "current_reason": build_blocked_reason(
                            raw_name=journal_name,
                            venue_type="journal",
                            result=result,
                            score_gap=0.0,
                        ),
                    }
                )
                continue

            unresolved.append((journal_name, result.score, row_count))
            review_rows.append(
                build_review_row(
                    journal_name,
                    row_count,
                    result,
                    normalizer,
                    local_dblp_index=local_dblp_index,
                    journal_registry_map=journal_registry_map,
                )
            )

    with open(OUT_UNMATCHED, "w", encoding="utf-8") as handle:
        handle.write(f"# {len(unresolved)} unresolved DBLP journal names\n")
        handle.write("# Format: best_score | rows | dblp_name\n\n")
        for journal_name, score, row_count in sorted(unresolved, key=lambda item: (-item[2], item[0].casefold())):
            handle.write(f"{score:.2f} | {row_count} | {journal_name}\n")

    write_review_csv(review_rows)
    write_proposal_csv(review_rows)
    merged_intentional_null_rows = merge_intentional_null_candidate_rows(
        review_rows,
        intentional_null_candidates,
    )
    write_split_proposal_csvs(review_rows, intentional_null_candidates, merged_intentional_null_rows)
    write_intentional_null_audit_csv(
        build_intentional_null_audit_sample(merged_intentional_null_rows, random_seed=7)
    )
    write_variant_cache(variant_rows)
    upsert_alias_review_records(OUT_ALIAS_REVIEW, build_alias_review_records(review_rows))
    synced_approved_rows = sync_alias_review_statuses(
        OUT_ALIAS_REVIEW,
        OUT_ALIAS_MEMORY,
        reviewed_at=run_date,
    )
    synced_resolved_rows = sync_review_statuses_from_resolved_rows(
        OUT_ALIAS_REVIEW,
        resolved_review_rows,
        reviewed_at=run_date,
    )
    write_match_audit_rows(OUT_MATCH_AUDIT, audit_rows)

    pct = matched / len(journal_names) * 100 if journal_names else 0.0
    print(f"Matched: {matched:,} / {len(journal_names):,} ({pct:.1f}%)")
    print(f"Unresolved journal names: {len(unresolved):,}")
    print(f"Skipped/non-venue journal names: {len(skipped):,}")
    print("Breakdown by match stage:")
    for key in sorted(stage_counts):
        print(f"  {key}: {stage_counts[key]:,} distinct / {stage_rows[key]:,} rows")
    print("Top low-confidence journal matches for review:")
    for score, journal_name, journal_id, title in sorted(
        low_confidence_matches,
        key=lambda item: (item[0], item[1].casefold()),
    )[:LOW_CONFIDENCE_LIMIT]:
        print(console_safe(f"  {score:.2f} | {journal_name} -> [{journal_id}] {title}"))
    print(f"Mapping: {OUT_MAPPING}")
    print(f"Unresolved: {OUT_UNMATCHED}")
    print(f"Review CSV: {OUT_REVIEW}")
    print(f"Proposal CSV: {OUT_PROPOSALS}")
    print(f"Approve CSV: {OUT_APPROVE_CANDIDATES}")
    print(f"Intentional-null candidates: {OUT_INTENTIONAL_NULL_CANDIDATES}")
    print(f"Intentional-null audit: {OUT_INTENTIONAL_NULL_AUDIT}")
    print(f"Variant cache: {OUT_VARIANT_CACHE}")
    print(f"Review memory: {OUT_ALIAS_REVIEW}")
    print(f"Alias memory: {OUT_ALIAS_MEMORY}")
    print(f"Approval sync rows: {synced_approved_rows}")
    print(f"Resolved sync rows: {synced_resolved_rows}")
    print(f"Match audit: {OUT_MATCH_AUDIT}")


if __name__ == "__main__":
    print("Matching DBLP journal names to journals table...")
    main()
