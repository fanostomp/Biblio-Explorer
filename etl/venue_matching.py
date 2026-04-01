"""Shared venue normalization and matching helpers for conference and journal ETL."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib import error as urlerror, parse, request
import xml.etree.ElementTree as ET


ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9/&\-]{1,24}\b")
PAREN_RE = re.compile(r"\(([^)]*)\)")
WHITESPACE_RE = re.compile(r"\s+")
LEADING_YEAR_OR_ORDINAL_RE = re.compile(
    r"^(?:(?:18|19|20)\d{2}|\d+(?:st|nd|rd|th))\b[\s,:\-]*",
    re.IGNORECASE,
)
TRAILING_VOLUME_RE = re.compile(
    r"(?:\bvol(?:ume)?\.?\s*(?:\d+|[ivxlcdm]+)\b|\bpart\s+(?:\d+|[ivxlcdm]+)\b|\(\s*(?:\d+|[ivxlcdm]+)\s*\))$",
    re.IGNORECASE,
)
WRAPPER_PREFIX_RE = re.compile(
    r"^(?:proceedings(?: of)?|adjunct proceedings(?: of)?|selected papers(?: of)?|"
    r"companion(?: to)?|satellite activities(?: of)?|tutorial abstracts(?: of)?|"
    r"workshop(?:s)?(?: on| of)?|short papers(?: of)?|posters?(?: of)?|"
    r"demonstrations?(?: of)?|demos?(?: of)?|doctoral consortium(?: of)?|"
    r"student research workshop(?: of)?|proceedings of the)\b[\s,:\-]*",
    re.IGNORECASE,
)
VARIANT_LABEL_RE = re.compile(
    r"\b(?:adjunct proceedings|selected papers|short papers|demo(?:nstration)?s?|"
    r"posters?|doctoral consortium|student research workshop|tutorial abstracts|"
    r"workshops?|companion|satellite activities|industrial and tool volume|"
    r"special sessions?|special session|supplement|volume|vol\.?|part)\b",
    re.IGNORECASE,
)
TRAILING_YEAR_RE = re.compile(r"(?:^|[\s,:\-])(?:18|19|20)\d{2}(?=$)", re.IGNORECASE)
STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}
GENERIC_ACRONYM_TOKENS = {"acm", "gi", "ieee", "ifip", "siam"}
FROZEN_CONFERENCE_REVIEW_ACRONYMS = {
    "ias",
    "icec",
    "icic",
    "iceis",
    "icca",
    "dft",
    "nems",
    "tsd",
}
FROZEN_JOURNAL_REVIEW_ABBREVIATIONS = {"jcp"}
JOURNAL_ABBREVIATIONS = {
    r"\btrans\.?\b": "transactions",
    r"\bj\.?\b": "journal",
    r"\bint\.?\b": "international",
    r"\bintl\.?\b": "international",
    r"\bknowl\.?\b": "knowledge",
    r"\beng\.?\b": "engineering",
    r"\bcomput\.?\b": "computer",
    r"\bcomp\.?\b": "computer",
    r"\bsyst\.?\b": "systems",
    r"\bappl\.?\b": "applications",
    r"\bgeom\.?\b": "geometry",
    r"\bbehav\.?\b": "behaviour",
    r"\bartif\.?\b": "artificial",
    r"\bintell\.?\b": "intelligence",
    r"\binf\.?\b": "information",
}
COMPILED_JOURNAL_ABBREVIATIONS = [
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in JOURNAL_ABBREVIATIONS.items()
]
STAGE_LABELS = (
    "manual aliases",
    "exact normalized local match",
    "parent-series / variant stripping",
    "DBLP local CSV registry",
    "DBLP online fallback",
    "OpenAlex",
    "Crossref",
    "conservative fuzzy fallback",
    "explicit skip / non_venue",
)


def ascii_fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_spaces(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def replace_roman_volume_markers(text: str) -> str:
    roman_map = {
        "i": "1",
        "ii": "2",
        "iii": "3",
        "iv": "4",
        "v": "5",
        "vi": "6",
        "vii": "7",
        "viii": "8",
        "ix": "9",
        "x": "10",
    }

    def repl(match: re.Match[str]) -> str:
        token = match.group(1).casefold()
        return roman_map.get(token, token)

    return re.sub(r"\bvol(?:ume)?\.?\s*([ivxlcdm]+)\b", repl, text, flags=re.IGNORECASE)


def normalize_issn(value: str) -> str:
    return re.sub(r"[^0-9X]", "", (value or "").upper())


def clean_for_match(text: str) -> str:
    lowered = ascii_fold(text).casefold().replace("&", " and ")
    lowered = replace_roman_volume_markers(lowered)
    lowered = lowered.replace("/", " ").replace("-", " ")
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return normalize_spaces(lowered)


def strip_trailing_year(text: str) -> str:
    value = TRAILING_YEAR_RE.sub("", text or "").strip(" ,:-")
    return normalize_spaces(value)


def hyphen_key(text: str) -> str:
    tokens = ASCII_WORD_RE.findall(clean_for_match(text))
    return "-".join(tokens)


def compact_key(text: str) -> str:
    return "".join(ASCII_WORD_RE.findall(clean_for_match(text)))


def strip_known_prefixes(text: str) -> str:
    value = text.strip()
    while True:
        stripped = WRAPPER_PREFIX_RE.sub("", value).strip(" ,:-")
        stripped = re.sub(r"^the\b[\s,:\-]*", "", stripped, flags=re.IGNORECASE)
        stripped = LEADING_YEAR_OR_ORDINAL_RE.sub("", stripped).strip(" ,:-")
        if stripped == value:
            return stripped
        value = stripped


def strip_variant_noise(text: str) -> str:
    value = TRAILING_VOLUME_RE.sub("", text).strip(" ,:-")
    value = PAREN_RE.sub(
        lambda match: "" if VARIANT_LABEL_RE.search(match.group(1)) else f" {match.group(1)} ",
        value,
    )
    value = VARIANT_LABEL_RE.sub(" ", value)
    value = TRAILING_VOLUME_RE.sub("", value).strip(" ,:-")
    return normalize_spaces(value)


def strip_dblp_series_key(value: str) -> str:
    parts = [part for part in (value or "").strip("/").split("/") if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if parts[0] == "conf":
        return "/".join(parts[:2])
    return "/".join(parts[:2])


def stable_canonical_venue_id(series_key: str) -> str:
    key = strip_dblp_series_key(series_key)
    slug = "-".join(ASCII_WORD_RE.findall(ascii_fold(key).casefold()))
    return f"dblp-{slug or 'unknown'}"


def _truthy_csv_flag(value: object) -> bool:
    return str(value or "").strip().casefold() not in {"", "0", "false", "no", "off", "n"}


def _looks_acronym_like(value: str) -> bool:
    raw = normalize_spaces(value or "")
    if not raw:
        return False
    ascii_value = ascii_fold(raw)
    compact = compact_key(ascii_value)
    if not compact or len(compact) > 12:
        return False
    return bool(re.fullmatch(r"[A-Z0-9 .()/+\-]+", ascii_value))


def _looks_non_rankable_journal_title(value: str) -> bool:
    lowered = clean_for_match(value)
    if not lowered:
        return False
    markers = (
        "magazine",
        "bulletin",
        "newsletter",
        "report",
        "technical report",
        "crossroads",
        "queue",
        "news",
        "notes",
    )
    return any(marker in lowered for marker in markers)


def read_delimited_columns(path: str | Path, delimiter: str = ";") -> list[str]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        return next(reader, [])


def detect_dblp_csv_inputs(dataset_dir: str | Path) -> dict[str, object]:
    dataset_path = Path(dataset_dir)
    files = sorted(dataset_path.glob("input_*.csv"))
    discovered: dict[str, object] = {
        "dataset_dir": dataset_path,
        "files": files,
    }
    if not files:
        return discovered

    def choose(preferred_tokens: tuple[str, ...]) -> Path | None:
        ranked = []
        for path in files:
            stem = path.stem.casefold()
            score = 0
            for token in preferred_tokens:
                if token in stem:
                    score += 10
            if path.name.casefold() == f"input_{preferred_tokens[0]}.csv":
                score += 100
            ranked.append((score, len(path.name), path))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2].name.casefold()))
        return ranked[0][2] if ranked and ranked[0][0] > 0 else None

    article_path = choose(("article",))
    inproceedings_path = choose(("inproceedings",))

    if article_path is not None:
        discovered["article"] = {
            "path": article_path,
            "columns": read_delimited_columns(article_path),
        }
    if inproceedings_path is not None:
        discovered["inproceedings"] = {
            "path": inproceedings_path,
            "columns": read_delimited_columns(inproceedings_path),
        }
    return discovered


def conference_parent_alias(text: str) -> str:
    value = strip_known_prefixes(normalize_spaces(text or ""))
    value = strip_variant_noise(value)
    value = strip_trailing_year(value)
    value = re.sub(r"\b(?:18|19|20)\d{2}\b", " ", value)
    return clean_for_match(value)


def conference_child_alias(text: str) -> str:
    value = strip_known_prefixes(normalize_spaces(text or ""))
    value = strip_trailing_year(value)
    value = re.sub(r"\b(?:18|19|20)\d{2}\b", " ", value)
    return clean_for_match(value)


def strip_parenthetical_notes(text: str) -> str:
    return normalize_spaces(PAREN_RE.sub(" ", text or ""))


def split_segments(text: str) -> list[str]:
    segments = []
    for raw in re.split(r"[/:,&+|]", text):
        stripped = normalize_spaces(raw)
        if stripped:
            segments.append(stripped)
    return segments


def acronym_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    for token in ACRONYM_RE.findall(text or ""):
        compact = compact_key(token)
        if 2 <= len(compact) <= 24:
            candidates.add(compact)
        if "-" in token or "/" in token:
            for part in re.split(r"[-/]", token):
                compact = compact_key(part)
                if 2 <= len(compact) <= 24:
                    candidates.add(compact)
    return candidates


def derived_initialisms(text: str) -> set[str]:
    raw_tokens = ASCII_WORD_RE.findall(clean_for_match(strip_parenthetical_notes(text)))
    significant = [token for token in raw_tokens if token not in STOPWORDS]
    if len(significant) < 3:
        return set()

    initialisms = {"".join(token[0] for token in significant)}
    if significant and significant[0] in {"acm", "ieee", "siam", "ifip", "gi"}:
        trimmed = significant[1:]
        if trimmed:
            initialisms.add("".join(token[0] for token in trimmed))
    return {value for value in initialisms if 2 <= len(value) <= 24}


def score_token_overlap(source_tokens: set[str], target_tokens: set[str]) -> float:
    if not source_tokens or not target_tokens:
        return 0.0
    overlap = len(source_tokens & target_tokens)
    if not overlap:
        return 0.0
    return overlap / max(len(source_tokens), len(target_tokens))


def expand_journal_abbreviations(text: str) -> str:
    expanded = text
    for pattern, replacement in COMPILED_JOURNAL_ABBREVIATIONS:
        expanded = pattern.sub(replacement, expanded)
    return expanded


def extract_historical_aliases(title: str) -> set[str]:
    aliases: set[str] = set()
    for group in PAREN_RE.findall(title or ""):
        group_text = normalize_spaces(group)
        if not group_text:
            continue
        lowered = group_text.casefold()
        if not any(
            marker in lowered
            for marker in ("was", "previous", "prior", "former", "merged", "combined", "until")
        ):
            continue

        for token in ACRONYM_RE.findall(group_text):
            if compact_key(token) not in GENERIC_ACRONYM_TOKENS:
                aliases.add(token)
            for part in re.split(r"[-/]", token):
                if part and compact_key(part) not in GENERIC_ACRONYM_TOKENS:
                    aliases.add(part)

        phrases = re.split(r"[;,]", group_text)
        for phrase in phrases:
            fragment = phrase.strip()
            fragment = re.sub(
                r"\b(?:was|previously|prior to \d{4}|prior to|formerly|changed in \d{4}|changed|until \d{4}|until|merged with|merged|combined from \d{4}|combined from|combined)\b",
                " ",
                fragment,
                flags=re.IGNORECASE,
            )
            fragment = normalize_spaces(fragment)
            if fragment:
                aliases.add(fragment)
                for part in re.split(r"\band\b|/|-", fragment):
                    normalized = normalize_spaces(part)
                    if normalized and compact_key(normalized) not in GENERIC_ACRONYM_TOKENS:
                        aliases.add(normalized)

    return {alias for alias in aliases if alias}


@dataclass(frozen=True)
class VenueForms:
    strict: str
    loose: str
    parent: str
    tokens: tuple[str, ...]
    acronym_candidates: tuple[str, ...]
    segment_candidates: tuple[str, ...]
    variant_keys: tuple[str, ...]


@dataclass(frozen=True)
class VenueRecord:
    venue_id: int
    title: str
    acronym: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ManualOverride:
    action: str
    venue_id: int | None
    notes: str = ""


@dataclass(frozen=True)
class ExternalSourceCandidate:
    source: str
    display_name: str
    alternate_titles: tuple[str, ...] = ()
    abbreviated_title: str | None = None
    issn: tuple[str, ...] = ()
    issn_l: str | None = None
    source_type: str | None = None


@dataclass(frozen=True)
class MatchCandidate:
    venue_id: int
    title: str
    match_type: str
    score: float
    evidence_source: str


@dataclass(frozen=True)
class MatchResult:
    status: str
    venue_id: int | None
    match_type: str
    score: float
    confidence: str
    evidence_source: str
    candidates: tuple[MatchCandidate, ...] = ()


def load_persistent_venue_aliases(
    path: str | Path,
    venue_type: str,
) -> dict[str, ManualOverride]:
    alias_path = Path(path)
    if not alias_path.exists():
        return {}

    overrides: dict[str, ManualOverride] = {}
    with alias_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("venue_type") or "").strip().casefold() != venue_type.casefold():
                continue
            if not _truthy_csv_flag(row.get("is_active", "1")):
                continue
            canonical_raw = (row.get("canonical_id") or "").strip()
            alias = (row.get("normalized_alias") or row.get("alias") or "").strip()
            if not canonical_raw or not alias:
                continue
            try:
                venue_id = int(canonical_raw)
            except ValueError:
                continue
            overrides[compact_key(alias)] = ManualOverride(
                action="match",
                venue_id=venue_id,
                notes=(row.get("source") or "").strip(),
            )
    return overrides


def _load_optional_csv_rows(path: str | Path) -> list[dict[str, str]]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{str(key): str(value or "") for key, value in row.items()} for row in reader]


def _write_csv_rows(path: str | Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    csv_path = Path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def upsert_alias_review_records(path: str | Path, review_rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "id",
        "venue_type",
        "raw_name",
        "normalized_name",
        "proposed_canonical_id",
        "proposed_canonical_title",
        "score",
        "match_type",
        "evidence_json",
        "status",
        "reviewed_by",
        "reviewed_at",
    ]
    existing_rows = _load_optional_csv_rows(path)
    merged: dict[tuple[str, str, str], dict[str, object]] = {}

    for row in existing_rows:
        key = (
            (row.get("venue_type") or "").strip(),
            (row.get("normalized_name") or "").strip(),
            (row.get("proposed_canonical_id") or "").strip(),
        )
        if any(key):
            merged[key] = row

    for row in review_rows:
        key = (
            str(row.get("venue_type", "")).strip(),
            str(row.get("normalized_name", "")).strip(),
            str(row.get("proposed_canonical_id", "")).strip(),
        )
        existing = merged.get(key, {})
        status = (existing.get("status") or row.get("status") or "pending").strip() or "pending"
        reviewed_by = (existing.get("reviewed_by") or row.get("reviewed_by") or "").strip()
        reviewed_at = (existing.get("reviewed_at") or row.get("reviewed_at") or "").strip()
        record = {**existing, **row}
        record["status"] = status
        record["reviewed_by"] = reviewed_by
        record["reviewed_at"] = reviewed_at
        if not record.get("id"):
            digest = hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:16]
            record["id"] = digest
        merged[key] = record

    ordered_rows = sorted(
        merged.values(),
        key=lambda row: (
            str(row.get("venue_type", "")),
            str(row.get("normalized_name", "")),
            str(row.get("proposed_canonical_id", "")),
        ),
    )
    _write_csv_rows(path, fieldnames, ordered_rows)


def sync_alias_review_statuses(
    review_path: str | Path,
    alias_path: str | Path,
    *,
    reviewed_at: str = "",
    reviewed_by: str = "alias-memory-sync",
) -> int:
    review_rows = _load_optional_csv_rows(review_path)
    alias_rows = _load_optional_csv_rows(alias_path)
    approved_keys = {
        (
            (row.get("venue_type") or "").strip(),
            (row.get("normalized_alias") or row.get("alias") or "").strip(),
            (row.get("canonical_id") or "").strip(),
        )
        for row in alias_rows
        if _truthy_csv_flag(row.get("is_active", "1"))
        and (row.get("canonical_id") or "").strip()
        and ((row.get("normalized_alias") or row.get("alias") or "").strip())
    }
    if not approved_keys or not review_rows:
        return 0

    updated = 0
    for row in review_rows:
        current_status = (row.get("status") or "").strip()
        if current_status == "rejected":
            continue
        key = (
            (row.get("venue_type") or "").strip(),
            (row.get("normalized_name") or "").strip(),
            (row.get("proposed_canonical_id") or "").strip(),
        )
        if key not in approved_keys:
            continue
        if current_status != "approved":
            updated += 1
        row["status"] = "approved"
        row["reviewed_by"] = (row.get("reviewed_by") or reviewed_by).strip()
        row["reviewed_at"] = (row.get("reviewed_at") or reviewed_at).strip()

    fieldnames = [
        "id",
        "venue_type",
        "raw_name",
        "normalized_name",
        "proposed_canonical_id",
        "proposed_canonical_title",
        "score",
        "match_type",
        "evidence_json",
        "status",
        "reviewed_by",
        "reviewed_at",
    ]
    _write_csv_rows(review_path, fieldnames, review_rows)
    return updated


def sync_review_statuses_from_resolved_rows(
    review_path: str | Path,
    resolved_rows: Iterable[dict[str, object]],
    *,
    reviewed_at: str = "",
    reviewed_by: str = "mapping-sync",
) -> int:
    review_rows = _load_optional_csv_rows(review_path)
    if not review_rows:
        return 0

    resolved_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in resolved_rows:
        venue_type = str(row.get("venue_type") or "").strip()
        normalized_name = str(row.get("normalized_name") or "").strip()
        canonical_id = str(
            row.get("proposed_canonical_id") or row.get("canonical_id") or row.get("venue_id") or ""
        ).strip()
        if not venue_type or not normalized_name or not canonical_id:
            continue
        resolved_by_key[(venue_type, normalized_name)] = {
            "proposed_canonical_id": canonical_id,
            "proposed_canonical_title": str(
                row.get("proposed_canonical_title") or row.get("canonical_title") or row.get("title") or ""
            ).strip(),
            "reviewed_by": str(row.get("reviewed_by") or "").strip(),
            "reviewed_at": str(row.get("reviewed_at") or "").strip(),
            "evidence_json": str(row.get("evidence_json") or "").strip(),
        }

    if not resolved_by_key:
        return 0

    updated = 0
    for row in review_rows:
        current_status = (row.get("status") or "").strip()
        if current_status == "rejected":
            continue
        key = (
            (row.get("venue_type") or "").strip(),
            (row.get("normalized_name") or "").strip(),
        )
        resolved = resolved_by_key.get(key)
        if resolved is None:
            continue

        changed = False
        resolved_id = resolved["proposed_canonical_id"]
        resolved_title = resolved["proposed_canonical_title"]
        if (row.get("proposed_canonical_id") or "").strip() != resolved_id:
            row["proposed_canonical_id"] = resolved_id
            changed = True
        if resolved_title and (row.get("proposed_canonical_title") or "").strip() != resolved_title:
            row["proposed_canonical_title"] = resolved_title
            changed = True
        resolved_evidence_json = resolved.get("evidence_json", "")
        if resolved_evidence_json and (row.get("evidence_json") or "").strip() != resolved_evidence_json:
            row["evidence_json"] = resolved_evidence_json
            changed = True
        if current_status != "approved":
            resolved_reviewed_by = resolved.get("reviewed_by", "")
            resolved_reviewed_at = resolved.get("reviewed_at", "")
            row["status"] = "approved"
            row["reviewed_by"] = (
                row.get("reviewed_by") or resolved_reviewed_by or reviewed_by
            ).strip()
            row["reviewed_at"] = (
                row.get("reviewed_at") or resolved_reviewed_at or reviewed_at
            ).strip()
            changed = True
        if changed:
            updated += 1

    fieldnames = [
        "id",
        "venue_type",
        "raw_name",
        "normalized_name",
        "proposed_canonical_id",
        "proposed_canonical_title",
        "score",
        "match_type",
        "evidence_json",
        "status",
        "reviewed_by",
        "reviewed_at",
    ]
    _write_csv_rows(review_path, fieldnames, review_rows)
    return updated


def write_match_audit_rows(path: str | Path, audit_rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "id",
        "venue_type",
        "raw_name",
        "normalized_name",
        "canonical_id",
        "stage",
        "match_type",
        "score",
        "run_date",
        "evidence_json",
    ]
    existing_rows = _load_optional_csv_rows(path)
    existing_keys = {
        (
            row.get("run_date", ""),
            row.get("venue_type", ""),
            row.get("normalized_name", ""),
        )
        for row in audit_rows
    }
    retained_rows = [
        row
        for row in existing_rows
        if (
            (row.get("run_date", ""), row.get("venue_type", ""), row.get("normalized_name", ""))
            not in existing_keys
        )
    ]
    normalized_new_rows: list[dict[str, object]] = []
    for row in audit_rows:
        record = dict(row)
        if not record.get("id"):
            digest = hashlib.sha1(
                "|".join(
                    [
                        str(record.get("run_date", "")),
                        str(record.get("venue_type", "")),
                        str(record.get("normalized_name", "")),
                    ]
                ).encode("utf-8")
            ).hexdigest()[:16]
            record["id"] = digest
        normalized_new_rows.append(record)
    ordered_rows = sorted(
        [*retained_rows, *normalized_new_rows],
        key=lambda row: (
            str(row.get("run_date", "")),
            str(row.get("venue_type", "")),
            str(row.get("normalized_name", "")),
        ),
    )
    _write_csv_rows(path, fieldnames, ordered_rows)


def build_blocked_reason(
    raw_name: str,
    venue_type: str,
    result: MatchResult,
    score_gap: float,
    series_key: str = "",
) -> str:
    if result.status == "non_venue":
        return "explicitly marked non-rankable"
    if result.status == "skipped":
        return "explicitly skipped by review policy"
    if result.match_type == "ambiguous-acronym":
        return "blocked by acronym collision; requires constrained candidate evidence"
    if result.status == "review":
        if (
            venue_type == "journal"
            and len(result.candidates) > 1
            and score_gap < 0.08
            and _looks_risky_journal_abbreviation(raw_name)
        ):
            return "risky-abbreviation-near-tie"
        if len(result.candidates) > 1 and score_gap < 0.08:
            return "blocked by low margin between top candidates"
        if result.match_type in {"metadata-alias", "derived-acronym", "segment-acronym", "exact-acronym"}:
            return "blocked pending reviewed alias approval"
        return "blocked by conservative acceptance threshold"
    if result.status == "unmatched":
        if venue_type == "conference" and series_key:
            return "blocked because the DBLP series still has no approved local canonical row or alias"
        if venue_type == "journal" and _looks_non_rankable_journal_title(raw_name):
            return "likely non-rankable publication family"
        return "no conservative candidate found"
    return ""


def _base_review_key(raw_name: str) -> str:
    stripped = re.sub(r"\(\s*\d+[\w\s-]*\)$", "", raw_name or "", flags=re.IGNORECASE).strip()
    return compact_key(stripped)


def _looks_risky_journal_abbreviation(raw_name: str) -> bool:
    key = compact_key(raw_name)
    return _looks_acronym_like(raw_name) and (len(key) <= 6 or key in FROZEN_JOURNAL_REVIEW_ABBREVIATIONS)


def classify_conference_review_bucket(
    raw_name: str,
    result: MatchResult,
    series_key: str = "",
) -> str:
    base_key = _base_review_key(raw_name)
    if result.match_type == "ambiguous-acronym":
        return "B"
    if base_key in FROZEN_CONFERENCE_REVIEW_ACRONYMS:
        return "B"
    if result.status == "unmatched" and series_key and "(" not in raw_name:
        return "C"
    if result.candidates and (
        result.score >= 0.90
        or result.match_type in {"metadata-alias", "derived-acronym", "segment-acronym"}
    ):
        return "A"
    if _looks_acronym_like(raw_name):
        return "B"
    return "C"


def classify_journal_suggested_action(raw_name: str, result: MatchResult) -> str:
    if result.status in {"non_venue", "skipped"} or _looks_non_rankable_journal_title(raw_name):
        return "intentional null / likely not rankable"
    if (
        result.status == "review"
        and len(result.candidates) == 1
        and result.score >= 0.90
        and result.match_type in {"metadata-alias", "dblp-local-csv-registry"}
    ):
        return "approve alias"
    return "review manually"


def split_conference_review_rows_by_action(
    review_rows: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    buckets = {
        "approve": [],
        "lookup_enrichment": [],
        "collision_review_only": [],
    }
    for row in review_rows:
        approval_ready = str(row.get("approval_ready") or "").strip().casefold() == "yes"
        bucket = str(row.get("bucket") or "").strip()
        if approval_ready:
            buckets["approve"].append(row)
        elif bucket == "C":
            buckets["lookup_enrichment"].append(row)
        else:
            buckets["collision_review_only"].append(row)
    return buckets


def split_journal_review_rows_by_action(
    review_rows: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    buckets = {
        "approve": [],
        "intentional_null": [],
    }
    for row in review_rows:
        approval_ready = str(row.get("approval_ready") or "").strip().casefold() == "yes"
        suggested_action = str(row.get("suggested_action") or "").strip()
        if approval_ready or suggested_action == "approve alias":
            buckets["approve"].append(row)
        elif suggested_action == "intentional null / likely not rankable":
            buckets["intentional_null"].append(row)
    return buckets


def build_intentional_null_audit_sample(
    candidate_rows: list[dict[str, object]],
    *,
    random_seed: int = 0,
) -> list[dict[str, object]]:
    normalized_rows = [
        {
            "raw_journal_name": str(row.get("raw_journal_name") or row.get("dblp_journal_name") or ""),
            "rows": int(row.get("rows") or row.get("row_count") or 0),
            "candidate_title": str(row.get("candidate_title") or row.get("candidate_1_title") or ""),
            "score": str(row.get("score") or row.get("best_score") or "0.00"),
            "current_reason": str(row.get("current_reason") or row.get("blocked_reason") or ""),
        }
        for row in candidate_rows
        if str(row.get("raw_journal_name") or row.get("dblp_journal_name") or "").strip()
    ]
    ranked = sorted(
        normalized_rows,
        key=lambda row: (-int(row["rows"]), row["raw_journal_name"].casefold()),
    )
    top_rows = ranked[:20]
    remaining = ranked[20:]
    mid_slice_start = max(0, (len(remaining) // 2) - 5)
    medium_rows = remaining[mid_slice_start : mid_slice_start + 10]
    selected_names = {row["raw_journal_name"] for row in [*top_rows, *medium_rows]}
    random_pool = [row for row in remaining if row["raw_journal_name"] not in selected_names]
    rng = random.Random(random_seed)
    random_rows = (
        rng.sample(random_pool, min(10, len(random_pool)))
        if random_pool
        else []
    )

    sampled_rows = [*top_rows, *medium_rows, *random_rows]
    deduped: list[dict[str, object]] = []
    seen = set()
    for row in sampled_rows:
        name = row["raw_journal_name"]
        if name in seen:
            continue
        seen.add(name)
        reason = str(row.get("current_reason") or "")
        if reason.startswith("explicitly") or "non-rankable" in reason:
            audit_verdict = "likely-legitimate"
            notes = "Sampled explicit skip/non-rankable case; no contrary deterministic evidence is present in the generated review artifacts."
        else:
            audit_verdict = "needs-manual-check"
            notes = "Sampled for manual validation because the skip reason is not an explicit non-rankable decision."
        deduped.append(
            {
                **row,
                "audit_verdict": audit_verdict,
                "notes": notes,
            }
        )
    return deduped


class VenueNormalizer:
    """Generate strict, loose, and parent-series forms for venue strings."""

    def __init__(self, venue_type: str | None = None):
        self.venue_type = venue_type or "generic"

    def normalize(self, text: str) -> VenueForms:
        raw = normalize_spaces(text or "")
        raw_expanded = expand_journal_abbreviations(raw) if self.venue_type == "journal" else raw
        strict = clean_for_match(raw_expanded)

        loose_source = strip_known_prefixes(raw_expanded)
        loose = clean_for_match(loose_source)

        parent_source = strip_variant_noise(strip_parenthetical_notes(loose_source))
        parent = clean_for_match(parent_source)
        tokens = tuple(token for token in ASCII_WORD_RE.findall(parent or loose or strict) if token)

        segment_source = strip_variant_noise(strip_known_prefixes(raw))
        raw_segments: list[str] = []
        if any(separator in segment_source for separator in ("-", "/")):
            raw_segments.append(segment_source)
            raw_segments.extend(split_segments(segment_source))
        segment_candidates = {hyphen_key(segment) for segment in raw_segments if hyphen_key(segment)}

        acronyms = acronym_candidates(raw)
        for derived in derived_initialisms(raw):
            acronyms.add(derived)
        for segment in raw_segments:
            acronyms.update(acronym_candidates(segment))
            compact = compact_key(segment)
            if 2 <= len(compact) <= 24:
                acronyms.add(compact)
            for part in re.split(r"[-/]", segment):
                compact = compact_key(part)
                if 2 <= len(compact) <= 24:
                    acronyms.add(compact)

        variant_keys = {value for value in (strict, loose, parent) if value}
        variant_keys.update(segment_candidates)
        if parent:
            variant_keys.add(parent.replace(" ", "-"))

        return VenueForms(
            strict=strict,
            loose=loose,
            parent=parent,
            tokens=tokens,
            acronym_candidates=tuple(sorted(acronyms)),
            segment_candidates=tuple(sorted(segment_candidates)),
            variant_keys=tuple(sorted(variant_keys)),
        )


class FileBackedJsonCache:
    """A tiny deterministic cache for optional external lookups."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        target = self.base_dir / namespace
        target.mkdir(parents=True, exist_ok=True)
        return target / f"{digest}.json"

    def get(self, namespace: str, key: str) -> object | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def set(self, namespace: str, key: str, value: object) -> object:
        path = self._path(namespace, key)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return value


class JsonApiClient:
    """Small helper for cache-first optional HTTP JSON lookups."""

    def __init__(
        self,
        cache: FileBackedJsonCache,
        namespace: str,
        base_url: str,
        enabled: bool = False,
        headers: dict[str, str] | None = None,
        timeout: int = 10,
    ):
        self.cache = cache
        self.namespace = namespace
        self.base_url = base_url
        self.enabled = enabled
        self.headers = headers or {}
        self.timeout = timeout

    def fetch_json(self, query_key: str, params: dict[str, str]) -> object | None:
        cached = self.cache.get(self.namespace, query_key)
        if cached is not None:
            return cached
        if not self.enabled:
            return None

        sep = "&" if "?" in self.base_url else "?"
        url = f"{self.base_url}{sep}{parse.urlencode(params)}"
        req = request.Request(url, headers=self.headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            urlerror.HTTPError,
            urlerror.URLError,
        ):
            return None
        return self.cache.set(self.namespace, query_key, payload)


class VenueMatcher:
    """Deterministic staged matcher shared by conference and journal ETL."""

    def __init__(
        self,
        venue_type: str,
        records: Iterable[VenueRecord],
        manual_overrides: dict[str, ManualOverride] | None = None,
        conflict_acronyms: Iterable[str] | None = None,
        accept_threshold: float = 0.88,
        review_threshold: float = 0.60,
        required_margin: float = 0.08,
    ):
        self.venue_type = venue_type
        self.normalizer = VenueNormalizer(venue_type)
        self.records = list(records)
        self.manual_overrides = {
            compact_key(key): value for key, value in (manual_overrides or {}).items() if compact_key(key)
        }
        self.conflict_acronyms = {compact_key(value) for value in (conflict_acronyms or set())}
        self.accept_threshold = accept_threshold
        self.review_threshold = review_threshold
        self.required_margin = required_margin
        self.records_by_id = {record.venue_id: record for record in self.records}
        self.index_by_variant: dict[str, list[MatchCandidate]] = defaultdict(list)
        self.index_by_acronym: dict[str, list[MatchCandidate]] = defaultdict(list)
        self.index_by_title_compact: dict[str, list[MatchCandidate]] = defaultdict(list)
        self.token_index: dict[int, set[str]] = {}
        self.record_variant_compacts: dict[int, set[str]] = {}
        self.record_issn_values: dict[int, set[str]] = {}
        self.venue_ids_by_variant_compact: dict[str, set[int]] = defaultdict(set)
        self.venue_ids_by_issn: dict[str, set[int]] = defaultdict(set)
        self._build_indices()

    def _build_indices(self) -> None:
        for record in self.records:
            forms = self.normalizer.normalize(record.title)
            self.token_index[record.venue_id] = set(forms.tokens)
            record_variants = {compact_key(record.title)}

            for variant in (forms.strict, forms.loose, forms.parent):
                if variant:
                    self.index_by_variant[variant].append(
                        MatchCandidate(record.venue_id, record.title, "exact-title", 0.99, "local")
                    )

            self.index_by_title_compact[compact_key(record.title)].append(
                MatchCandidate(record.venue_id, record.title, "exact-title", 0.99, "local")
            )

            explicit_acronyms = set()
            if record.acronym:
                explicit_acronyms.add(compact_key(record.acronym))
            explicit_acronyms.update(derived_initialisms(record.title))
            if self.venue_type == "journal":
                explicit_acronyms.update(derived_initialisms(expand_journal_abbreviations(record.title)))

            for value in explicit_acronyms:
                if not value:
                    continue
                match_type = "derived-acronym" if compact_key(record.acronym) != value else "exact-acronym"
                self.index_by_acronym[value].append(
                    MatchCandidate(
                        record.venue_id,
                        record.title,
                        match_type,
                        0.99 if match_type == "exact-acronym" else 0.90,
                        "local",
                    )
                )

            for alias in extract_historical_aliases(record.title):
                alias_forms = self.normalizer.normalize(alias)
                for variant in (alias_forms.strict, alias_forms.loose, alias_forms.parent):
                    if variant:
                        self.index_by_variant[variant].append(
                            MatchCandidate(
                            record.venue_id,
                            record.title,
                            "historical-alias",
                            0.94,
                            "local",
                        )
                    )
                for value in alias_forms.acronym_candidates:
                    self.index_by_acronym[value].append(
                        MatchCandidate(
                            record.venue_id,
                            record.title,
                            "historical-alias",
                            0.94,
                            "local",
                        )
                    )

            metadata_aliases = []
            for key in ("alternate_titles", "historical_titles", "abbreviated_titles"):
                raw_value = record.metadata.get(key, ())
                if isinstance(raw_value, str):
                    metadata_aliases.append(raw_value)
                else:
                    metadata_aliases.extend(str(value) for value in raw_value if value)
            for alias in metadata_aliases:
                record_variants.add(compact_key(alias))
                alias_forms = self.normalizer.normalize(alias)
                for variant in (alias_forms.strict, alias_forms.loose, alias_forms.parent):
                    if variant:
                        self.index_by_variant[variant].append(
                            MatchCandidate(
                                record.venue_id,
                                record.title,
                                "metadata-alias",
                                0.97,
                                "local",
                            )
                        )
                for value in alias_forms.acronym_candidates:
                    self.index_by_acronym[value].append(
                        MatchCandidate(
                            record.venue_id,
                            record.title,
                            "metadata-alias",
                            0.96,
                            "local",
                        )
                    )

            self.record_variant_compacts[record.venue_id] = {
                value for value in record_variants if value
            }
            for value in self.record_variant_compacts[record.venue_id]:
                self.venue_ids_by_variant_compact[value].add(record.venue_id)
            record_issn_raw = record.metadata.get("issn", ())
            record_issn = (
                {normalize_issn(value) for value in record_issn_raw if normalize_issn(value)}
                if not isinstance(record_issn_raw, str)
                else ({normalize_issn(record_issn_raw)} if normalize_issn(record_issn_raw) else set())
            )
            if record.metadata.get("issn_l"):
                normalized_record_issn_l = normalize_issn(str(record.metadata["issn_l"]))
                if normalized_record_issn_l:
                    record_issn.add(normalized_record_issn_l)
            self.record_issn_values[record.venue_id] = record_issn
            for value in record_issn:
                self.venue_ids_by_issn[value].add(record.venue_id)

    def _manual_override(self, query: str) -> MatchResult | None:
        forms = self.normalizer.normalize(query)
        override = None
        for key in {
            compact_key(query),
            compact_key(forms.strict),
            compact_key(forms.loose),
            compact_key(forms.parent),
        }:
            override = self.manual_overrides.get(key)
            if override is not None:
                break
        if override is None:
            return None
        action = override.action.casefold()
        if action in {"non_venue", "skip", "unmatch"}:
            status = "non_venue" if action == "non_venue" else "skipped"
            label = action.replace("_", "-")
            return MatchResult(status, None, f"manual-{label}", 1.0, status, "manual", ())
        if override.venue_id is None:
            return MatchResult("review", None, "manual-review", 0.0, "review", "manual", ())
        record = self.records_by_id.get(override.venue_id)
        candidates = ()
        if record is not None:
            candidates = (
                MatchCandidate(record.venue_id, record.title, "manual-match", 1.0, "manual"),
            )
        return MatchResult("matched", override.venue_id, "manual-match", 1.0, "high", "manual", candidates)

    def _unique_candidates(self, candidates: Iterable[MatchCandidate]) -> list[MatchCandidate]:
        best_by_id: dict[int, MatchCandidate] = {}
        for candidate in candidates:
            current = best_by_id.get(candidate.venue_id)
            if current is None or candidate.score > current.score:
                best_by_id[candidate.venue_id] = candidate
        return sorted(best_by_id.values(), key=lambda item: (-item.score, item.title.casefold()))

    def _local_candidates(self, query: str, forms: VenueForms) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        query_compact = compact_key(query)
        query_tokens = set(forms.tokens)
        has_segment_separator = any(separator in query for separator in ("-", "/"))
        variant_stage_map = (
            (forms.strict, None),
            (forms.loose, "variant-strip"),
            (forms.parent, "parent-series"),
        )
        for variant, override_type in variant_stage_map:
            variant_compact = compact_key(variant)
            for candidate in self.index_by_variant.get(variant, ()):
                if override_type and variant not in {forms.strict, ""}:
                    score = 0.97 if override_type == "variant-strip" else 0.96
                    candidates.append(
                        MatchCandidate(
                            candidate.venue_id,
                            candidate.title,
                            override_type,
                            score,
                            candidate.evidence_source,
                        )
                    )
                else:
                    candidates.append(candidate)
            for candidate in self.index_by_title_compact.get(variant_compact, ()):
                if override_type and variant not in {forms.strict, ""}:
                    score = 0.97 if override_type == "variant-strip" else 0.96
                    candidates.append(
                        MatchCandidate(
                            candidate.venue_id,
                            candidate.title,
                            override_type,
                            score,
                            candidate.evidence_source,
                        )
                    )
                else:
                    candidates.append(candidate)

        if 2 <= len(query_compact) <= 24:
            candidates.extend(self.index_by_acronym.get(query_compact, ()))

        for segment in forms.segment_candidates:
            for piece in re.split(r"-", segment):
                if len(piece) < 5:
                    continue
                for candidate in self.index_by_acronym.get(piece, ()):
                    match_type = (
                        "historical-alias"
                        if candidate.match_type == "historical-alias"
                        else "segment-acronym"
                    )
                    candidates.append(
                        MatchCandidate(candidate.venue_id, candidate.title, match_type, 1.0, "local")
                    )

        for value in forms.acronym_candidates:
            indexed_candidates = list(self.index_by_acronym.get(value, ()))
            if has_segment_separator and value != query_compact and len(value) < 5:
                indexed_candidates = [
                    candidate
                    for candidate in indexed_candidates
                    if candidate.match_type == "historical-alias"
                ]
            candidates.extend(indexed_candidates)

        if self.venue_type == "conference":
            for record in self.records:
                record_compact = compact_key(record.title)
                if query_compact and len(query_tokens) >= 2 and query_compact in record_compact:
                    candidates.append(
                        MatchCandidate(
                            record.venue_id,
                            record.title,
                            "title-contains-query",
                            0.86,
                            "local",
                        )
                    )
                score = score_token_overlap(set(forms.tokens), self.token_index.get(record.venue_id, set()))
                if score >= 0.70:
                    candidates.append(
                        MatchCandidate(
                            record.venue_id,
                            record.title,
                            "token-overlap",
                            0.80 + (score * 0.10),
                            "local",
                        )
                    )

        return self._unique_candidates(candidates)

    def _external_candidates(
        self,
        query: str,
        external_candidates: Iterable[ExternalSourceCandidate] | None,
    ) -> list[MatchCandidate]:
        if not external_candidates:
            return []

        candidates: list[MatchCandidate] = []
        for external in external_candidates:
            alt_titles = tuple(external.alternate_titles) + (
                (external.abbreviated_title,) if external.abbreviated_title else ()
            )
            alt_compact = {compact_key(title) for title in alt_titles if compact_key(title)}
            display_compact = compact_key(external.display_name)
            external_titles = {display_compact, *alt_compact}
            issn_values = {normalize_issn(value) for value in external.issn if normalize_issn(value)}
            if external.issn_l:
                normalized_issn_l = normalize_issn(external.issn_l)
                if normalized_issn_l:
                    issn_values.add(normalized_issn_l)

            matched_by_issn = set()
            for value in issn_values:
                matched_by_issn.update(self.venue_ids_by_issn.get(value, set()))
            for venue_id in matched_by_issn:
                record = self.records_by_id[venue_id]
                candidates.append(
                    MatchCandidate(
                        record.venue_id,
                        record.title,
                        f"{external.source}-issn",
                        1.0,
                        external.source,
                    )
                )

            matched_by_title = {
                candidate.venue_id
                for candidate in self.index_by_title_compact.get(display_compact, ())
            }
            for venue_id in matched_by_title:
                record = self.records_by_id[venue_id]
                candidates.append(
                    MatchCandidate(
                        record.venue_id,
                        record.title,
                        f"{external.source}-title",
                        0.92,
                        external.source,
                    )
                )

            matched_by_alt = set()
            for value in external_titles:
                matched_by_alt.update(self.venue_ids_by_variant_compact.get(value, set()))
            for venue_id in matched_by_alt - matched_by_title:
                record = self.records_by_id[venue_id]
                candidates.append(
                    MatchCandidate(
                        record.venue_id,
                        record.title,
                        f"{external.source}-alt-title",
                        0.96,
                        external.source,
                    )
                )

        return self._unique_candidates(candidates)

    def _classify_confidence(self, score: float, status: str) -> str:
        if status == "matched":
            if score >= 0.95:
                return "high"
            if score >= 0.75:
                return "medium"
            return "low"
        return status

    def match(
        self,
        query: str,
        external_candidates: Iterable[ExternalSourceCandidate] | None = None,
    ) -> MatchResult:
        manual = self._manual_override(query)
        if manual is not None:
            return manual

        forms = self.normalizer.normalize(query)
        local = self._local_candidates(query, forms)
        external = self._external_candidates(query, external_candidates)
        candidates = self._unique_candidates([*local, *external])

        if not candidates:
            return MatchResult("unmatched", None, "unmatched", 0.0, "unmatched", "none", ())

        top = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        ambiguous_acronym = any(value in self.conflict_acronyms for value in forms.acronym_candidates)
        frozen_conference_acronym = (
            self.venue_type == "conference"
            and _looks_acronym_like(query)
            and _base_review_key(query) in FROZEN_CONFERENCE_REVIEW_ACRONYMS
        )

        if top.match_type.endswith("-issn"):
            runner_up_is_issn = runner_up.match_type.endswith("-issn") if runner_up else False
            if not runner_up_is_issn or (runner_up and runner_up.venue_id == top.venue_id):
                return MatchResult(
                    "matched",
                    top.venue_id,
                    top.match_type,
                    top.score,
                    "high",
                    top.evidence_source,
                    tuple(candidates[:5]),
                )

        if (ambiguous_acronym or frozen_conference_acronym) and top.match_type in {
            "exact-acronym",
            "derived-acronym",
            "segment-acronym",
        }:
            return MatchResult(
                "review",
                None,
                "ambiguous-acronym",
                top.score,
                "review",
                top.evidence_source,
                tuple(candidates[:5]),
            )

        margin = top.score - (runner_up.score if runner_up else 0.0)
        if top.match_type in {"exact-title", "exact-acronym", "metadata-alias"} and top.score >= 0.97:
            return MatchResult(
                "matched",
                top.venue_id,
                top.match_type,
                top.score,
                self._classify_confidence(top.score, "matched"),
                top.evidence_source,
                tuple(candidates[:5]),
            )
        if top.score >= self.accept_threshold and margin >= self.required_margin:
            return MatchResult(
                "matched",
                top.venue_id,
                top.match_type,
                top.score,
                self._classify_confidence(top.score, "matched"),
                top.evidence_source,
                tuple(candidates[:5]),
            )

        if top.score >= self.review_threshold:
            return MatchResult(
                "review",
                None,
                top.match_type,
                top.score,
                "review",
                top.evidence_source,
                tuple(candidates[:5]),
            )

        return MatchResult(
            "unmatched",
            None,
            "unmatched",
            top.score,
            "unmatched",
            top.evidence_source,
            tuple(candidates[:5]),
        )


def _iter_delimited_rows(path: str | Path, delimiter: str = ";") -> Iterable[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            yield {str(key): (value or "").strip() for key, value in row.items()}


def _looks_like_abbreviated_title(text: str) -> bool:
    value = normalize_spaces(text or "")
    if not value:
        return False
    compact = re.sub(r"[^A-Za-z0-9]", "", value)
    if not compact:
        return False
    if "." in value:
        return True
    if value.upper() == value and 2 <= len(compact) <= 16:
        return True
    tokens = re.findall(r"[A-Za-z0-9]+", value)
    if tokens and len(tokens) <= 3 and all(len(token) <= 5 for token in tokens):
        return True
    return False


def _prefer_registry_title(title_counts: Counter[str], venue_type: str) -> str:
    if not title_counts:
        return ""
    eligible = {
        title: count
        for title, count in title_counts.items()
        if not _looks_like_abbreviated_title(title)
    }
    pool = eligible or title_counts
    if venue_type == "conference":
        return max(
            pool,
            key=lambda title: (
                pool[title],
                len(conference_parent_alias(title)),
                len(title),
                title.casefold(),
            ),
        )
    return max(
        pool,
        key=lambda title: (
            pool[title],
            len(clean_for_match(expand_journal_abbreviations(title))),
            len(title),
            title.casefold(),
        ),
    )


def _conference_alias_keys(title: str, normalizer: VenueNormalizer | None = None) -> set[str]:
    forms = (normalizer or VenueNormalizer("conference")).normalize(title)
    keys = {forms.strict, forms.loose, forms.parent, conference_child_alias(title), conference_parent_alias(title)}
    keys.update(forms.segment_candidates)
    keys.update(forms.acronym_candidates)
    return {value for value in keys if value}


def _journal_alias_keys(title: str, normalizer: VenueNormalizer | None = None) -> set[str]:
    forms = (normalizer or VenueNormalizer("journal")).normalize(title)
    base_title = clean_for_match(strip_parenthetical_notes(title))
    keys = {forms.strict, forms.loose, forms.parent, base_title}
    keys.update(forms.acronym_candidates)
    return {value for value in keys if value}


def load_dblp_inproceedings_registry(path: str | Path) -> dict[str, object]:
    series_raw: dict[str, dict[str, object]] = {}
    alias_counts: dict[str, Counter[str]] = defaultdict(Counter)
    series_by_booktitle_raw: dict[str, Counter[str]] = defaultdict(Counter)
    normalizer = VenueNormalizer("conference")

    for row in _iter_delimited_rows(path):
        booktitle = (row.get("booktitle") or "").strip()
        if not booktitle:
            continue
        series_key = strip_dblp_series_key(row.get("crossref") or row.get("key") or "")
        if not series_key.startswith("conf/"):
            continue

        entry = series_raw.setdefault(
            series_key,
            {
                "canonical_venue_id": stable_canonical_venue_id(series_key),
                "series_key": series_key,
                "_booktitle_counts": Counter(),
                "_acronyms": Counter(),
                "_parent_aliases": Counter(),
                "_aliases": Counter(),
                "_dblp_keys": Counter(),
                "_crossrefs": Counter(),
                "_child_relationships": set(),
            },
        )
        entry["_booktitle_counts"][booktitle] += 1
        if row.get("key"):
            entry["_dblp_keys"][row["key"]] += 1
        if row.get("crossref"):
            entry["_crossrefs"][row["crossref"]] += 1
        series_by_booktitle_raw[booktitle][series_key] += 1

        child_alias = conference_child_alias(booktitle)
        parent_alias = conference_parent_alias(booktitle)
        if child_alias:
            entry["_aliases"][child_alias] += 1
        if parent_alias:
            entry["_parent_aliases"][parent_alias] += 1
        if child_alias and parent_alias and child_alias != parent_alias:
            entry["_child_relationships"].add(("parent-series", child_alias, parent_alias))

        for alias in _conference_alias_keys(booktitle, normalizer):
            alias_counts[alias][series_key] += 1
        for acronym in normalizer.normalize(booktitle).acronym_candidates:
            entry["_acronyms"][acronym] += 1

    series_registry: dict[str, dict[str, object]] = {}
    for series_key, entry in series_raw.items():
        booktitle_counts: Counter[str] = entry["_booktitle_counts"]  # type: ignore[assignment]
        dominant_booktitle = booktitle_counts.most_common(1)[0][0]
        representative_title = _prefer_registry_title(booktitle_counts, "conference")
        series_registry[series_key] = {
            "canonical_venue_id": str(entry["canonical_venue_id"]),
            "series_key": series_key,
            "row_count": sum(booktitle_counts.values()),
            "distinct_booktitles": len(booktitle_counts),
            "dominant_booktitle": dominant_booktitle,
            "representative_title": representative_title,
            "top_booktitles": [title for title, _count in booktitle_counts.most_common(10)],
            "acronyms": sorted(entry["_acronyms"]),
            "aliases": sorted(entry["_aliases"]),
            "parent_aliases": sorted(entry["_parent_aliases"]),
            "dblp_keys": [value for value, _count in entry["_dblp_keys"].most_common(10)],
            "crossrefs": [value for value, _count in entry["_crossrefs"].most_common(5)],
            "child_relationships": [
                {"relation": relation, "child": child, "parent": parent}
                for relation, child, parent in sorted(entry["_child_relationships"])
            ],
        }

    conference_aliases: dict[str, list[dict[str, object]]] = {}
    for alias, counts in alias_counts.items():
        conference_aliases[alias] = [
            {
                "value": alias,
                "series_key": series_key,
                "canonical_venue_id": series_registry[series_key]["canonical_venue_id"],
                "count": count,
                "dominant_booktitle": series_registry[series_key]["dominant_booktitle"],
                "representative_title": series_registry[series_key]["representative_title"],
            }
            for series_key, count in counts.most_common()
            if series_key in series_registry
        ]

    conference_series_by_booktitle = {
        booktitle: counts.most_common(1)[0][0]
        for booktitle, counts in series_by_booktitle_raw.items()
        if counts
    }
    return {
        "conference_aliases": conference_aliases,
        "conference_series_by_booktitle": conference_series_by_booktitle,
        "conference_series_registry": series_registry,
    }


def load_dblp_article_registry(path: str | Path) -> dict[str, object]:
    journal_raw: dict[str, dict[str, object]] = {}
    alias_counts: dict[str, Counter[str]] = defaultdict(Counter)
    normalizer = VenueNormalizer("journal")

    for row in _iter_delimited_rows(path):
        journal = (row.get("journal") or "").strip()
        if not journal:
            continue
        key = strip_dblp_series_key(row.get("key") or "")
        journal_key = key or f"journal/{hyphen_key(journal)}"

        entry = journal_raw.setdefault(
            journal_key,
            {
                "canonical_venue_id": stable_canonical_venue_id(journal_key),
                "journal_key": journal_key,
                "_title_counts": Counter(),
                "_publishers": Counter(),
            },
        )
        entry["_title_counts"][journal] += 1
        if row.get("publisher"):
            entry["_publishers"][row["publisher"]] += 1

        for alias in _journal_alias_keys(journal, normalizer):
            alias_counts[alias][journal_key] += 1

    journal_registry: dict[str, dict[str, object]] = {}
    for journal_key, entry in journal_raw.items():
        title_counts: Counter[str] = entry["_title_counts"]  # type: ignore[assignment]
        dominant_title = _prefer_registry_title(title_counts, "journal")
        abbreviated_titles = sorted(
            title for title in title_counts if title != dominant_title and _looks_like_abbreviated_title(title)
        )
        alternate_titles = sorted(
            title for title in title_counts if title != dominant_title and title not in abbreviated_titles
        )
        dominant_publisher = next(iter(entry["_publishers"].most_common(1)), ("", 0))[0]
        journal_registry[journal_key] = {
            "canonical_venue_id": str(entry["canonical_venue_id"]),
            "journal_key": journal_key,
            "row_count": sum(title_counts.values()),
            "distinct_titles": len(title_counts),
            "dominant_title": dominant_title,
            "alternate_titles": alternate_titles,
            "abbreviated_titles": abbreviated_titles,
            "publisher": dominant_publisher,
        }

    journal_aliases: dict[str, list[dict[str, object]]] = {}
    for alias, counts in alias_counts.items():
        journal_aliases[alias] = [
            {
                "value": alias,
                "journal_key": journal_key,
                "canonical_venue_id": journal_registry[journal_key]["canonical_venue_id"],
                "count": count,
                "dominant_title": journal_registry[journal_key]["dominant_title"],
                "alternate_titles": journal_registry[journal_key]["alternate_titles"],
                "abbreviated_titles": journal_registry[journal_key]["abbreviated_titles"],
                "publisher": journal_registry[journal_key]["publisher"],
            }
            for journal_key, count in counts.most_common()
            if journal_key in journal_registry
        ]

    return {
        "journal_aliases": journal_aliases,
        "journal_registry": journal_registry,
    }


def _persist_registry_snapshot(path: str | Path, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_dblp_csv_index(
    dataset_dir: str | Path | None = None,
    *,
    article_csv: str | Path | None = None,
    inproceedings_csv: str | Path | None = None,
    cache_dir: str | Path | None = None,
    persist_dir: str | Path | None = None,
) -> dict[str, object]:
    if dataset_dir is not None:
        detected = detect_dblp_csv_inputs(dataset_dir)
        article_info = detected.get("article", {})
        inproc_info = detected.get("inproceedings", {})
        article_path = Path(article_csv) if article_csv else article_info.get("path")
        inproc_path = Path(inproceedings_csv) if inproceedings_csv else inproc_info.get("path")
        source_inputs = {
            "article": {
                "path": str(article_path) if article_path else "",
                "columns": list(article_info.get("columns", [])),
            },
            "inproceedings": {
                "path": str(inproc_path) if inproc_path else "",
                "columns": list(inproc_info.get("columns", [])),
            },
        }
    else:
        article_path = Path(article_csv) if article_csv else None
        inproc_path = Path(inproceedings_csv) if inproceedings_csv else None
        source_inputs = {
            "article": {
                "path": str(article_path) if article_path else "",
                "columns": read_delimited_columns(article_path) if article_path else [],
            },
            "inproceedings": {
                "path": str(inproc_path) if inproc_path else "",
                "columns": read_delimited_columns(inproc_path) if inproc_path else [],
            },
        }

    cache_key_parts = []
    for label, path in (("article", article_path), ("inproceedings", inproc_path)):
        if path and path.exists():
            stat = path.stat()
            cache_key_parts.append(
                f"{label}:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{'|'.join(source_inputs[label]['columns'])}"
            )
    cache_key = "::".join(cache_key_parts)
    if cache_dir and cache_key:
        cache = FileBackedJsonCache(cache_dir)
        cached = cache.get("dblp_csv_index", cache_key)
        if cached is not None:
            return cached

    index: dict[str, object] = {"inputs": source_inputs}
    if inproc_path and inproc_path.exists():
        index.update(load_dblp_inproceedings_registry(inproc_path))
    else:
        index.update(
            {
                "conference_aliases": {},
                "conference_series_by_booktitle": {},
                "conference_series_registry": {},
            }
        )
    if article_path and article_path.exists():
        index.update(load_dblp_article_registry(article_path))
    else:
        index.update(
            {
                "journal_aliases": {},
                "journal_registry": {},
            }
        )

    if persist_dir:
        persist_path = Path(persist_dir)
        _persist_registry_snapshot(
            persist_path / "dblp_conference_csv_registry.json",
            {
                "inputs": {"inproceedings": source_inputs["inproceedings"]},
                "conference_aliases": index.get("conference_aliases", {}),
                "conference_series_by_booktitle": index.get("conference_series_by_booktitle", {}),
                "conference_series_registry": index.get("conference_series_registry", {}),
            },
        )
        _persist_registry_snapshot(
            persist_path / "dblp_journal_csv_registry.json",
            {
                "inputs": {"article": source_inputs["article"]},
                "journal_aliases": index.get("journal_aliases", {}),
                "journal_registry": index.get("journal_registry", {}),
            },
        )

    if cache_dir and cache_key:
        cache.set("dblp_csv_index", cache_key, index)
    return index


def build_dblp_snapshot_index(snapshot_path: str | Path) -> dict[str, object]:
    """Legacy XML helper for DBLP snapshots when a raw CSV dataset is not being used."""

    path = Path(snapshot_path)
    opener = gzip.open if path.suffix == ".gz" else open
    conference_aliases: dict[str, list[dict[str, str]]] = defaultdict(list)
    journal_aliases: dict[str, list[dict[str, str]]] = defaultdict(list)
    conference_registry_raw: dict[str, dict[str, object]] = {}

    def ensure_registry_entry(series_key: str) -> dict[str, object]:
        normalized_series_key = strip_dblp_series_key(series_key)
        canonical_venue_id = stable_canonical_venue_id(normalized_series_key)
        return conference_registry_raw.setdefault(
            canonical_venue_id,
            {
                "canonical_venue_id": canonical_venue_id,
                "series_key": normalized_series_key,
                "_acronyms": set(),
                "_aliases": set(),
                "_parent_aliases": set(),
                "_dblp_keys": set(),
                "_child_relationships": set(),
            },
        )

    def add_conference_alias(series_key: str, dblp_key: str, value: str, url: str) -> None:
        alias_key = clean_for_match(value)
        if not alias_key:
            return
        entry = ensure_registry_entry(series_key)
        canonical_venue_id = str(entry["canonical_venue_id"])
        alias_payload = {
            "dblp_key": dblp_key,
            "series_key": str(entry["series_key"]),
            "canonical_venue_id": canonical_venue_id,
            "value": value,
            "url": url,
        }
        if alias_payload not in conference_aliases[alias_key]:
            conference_aliases[alias_key].append(alias_payload)

        child_alias = conference_child_alias(value)
        parent_alias = conference_parent_alias(value)
        if child_alias:
            entry["_aliases"].add(child_alias)
        if parent_alias:
            entry["_parent_aliases"].add(parent_alias)
        if dblp_key:
            entry["_dblp_keys"].add(dblp_key)

        normalizer = VenueNormalizer("conference")
        forms = normalizer.normalize(value)
        for acronym in forms.acronym_candidates:
            entry["_acronyms"].add(acronym)

        if child_alias and parent_alias and child_alias != parent_alias:
            relation = (
                "parent-series",
                child_alias,
                parent_alias,
            )
            entry["_child_relationships"].add(relation)

    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag not in {"article", "inproceedings", "proceedings"}:
                continue

            dblp_key = element.attrib.get("key", "")
            url = (element.findtext("url") or "").strip()

            def add_alias(target: dict[str, list[dict[str, str]]], value: str) -> None:
                alias_key = clean_for_match(value)
                if not alias_key:
                    return
                target[alias_key].append({"dblp_key": dblp_key, "value": value, "url": url})

            if element.tag == "article":
                journal = (element.findtext("journal") or "").strip()
                add_alias(journal_aliases, journal)
            else:
                crossref = (element.findtext("crossref") or "").strip()
                booktitle = (element.findtext("booktitle") or "").strip()
                title = (element.findtext("title") or "").strip()
                series_key = strip_dblp_series_key(crossref or dblp_key)
                add_conference_alias(series_key or dblp_key, dblp_key, booktitle, url)
                if title and title != booktitle:
                    add_conference_alias(series_key or dblp_key, dblp_key, title, url)

            element.clear()

    conference_registry = {}
    for canonical_venue_id, entry in conference_registry_raw.items():
        conference_registry[canonical_venue_id] = {
            "canonical_venue_id": canonical_venue_id,
            "series_key": str(entry["series_key"]),
            "acronyms": sorted(entry["_acronyms"]),
            "aliases": sorted(entry["_aliases"]),
            "parent_aliases": sorted(entry["_parent_aliases"]),
            "dblp_keys": sorted(entry["_dblp_keys"]),
            "child_relationships": [
                {"relation": relation, "child": child, "parent": parent}
                for relation, child, parent in sorted(entry["_child_relationships"])
            ],
        }

    return {
        "conference_aliases": dict(conference_aliases),
        "journal_aliases": dict(journal_aliases),
        "conference_registry": conference_registry,
    }


def classify_match_stage(match_type: str) -> str:
    normalized = (match_type or "").strip().casefold()
    if not normalized:
        return "conservative fuzzy fallback"
    if normalized.startswith("manual-"):
        if any(flag in normalized for flag in ("skip", "non-venue", "unmatch")):
            return "explicit skip / non_venue"
        return "manual aliases"
    if normalized in {"exact-title", "exact-acronym", "derived-acronym", "metadata-alias"}:
        return "exact normalized local match"
    if normalized in {"historical-alias", "segment-acronym", "parent-series", "parent-series-fallback"}:
        return "parent-series / variant stripping"
    if normalized.startswith("dblp") or "dblp-series-key" in normalized or "dblp-local" in normalized:
        if "api" in normalized or "online" in normalized:
            return "DBLP online fallback"
        return "DBLP local CSV registry"
    if normalized.startswith("openalex"):
        return "OpenAlex"
    if normalized.startswith("crossref"):
        return "Crossref"
    if normalized in {"unmatched", "review", "ambiguous-acronym"}:
        return "conservative fuzzy fallback"
    if normalized.startswith("prefix-overlap") or normalized in {"token-overlap", "title-contains-query"}:
        return "conservative fuzzy fallback"
    return "conservative fuzzy fallback"


def _load_value_counts(path: str | Path, key_col: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = (row.get(key_col) or "").strip()
            if value:
                counts[value] += 1
    return counts


def _load_mapping_rows(path: str | Path, key_col: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (row.get(key_col) or "").strip()
            if key:
                rows[key] = {str(field): str(value or "") for field, value in row.items()}
    return rows


def _empty_stage_counts() -> dict[str, dict[str, int]]:
    return {label: {"distinct": 0, "rows": 0} for label in STAGE_LABELS}


def _summarize_mapping(
    input_counts: Counter[str],
    mapping_rows: dict[str, dict[str, str]],
    key_id_field: str,
) -> dict[str, object]:
    stage_counts = _empty_stage_counts()
    matched_distinct = 0
    unmatched_distinct = 0
    unmatched_rows = 0
    skipped_distinct = 0
    skipped_rows = 0

    for value, row_count in input_counts.items():
        mapping_row = mapping_rows.get(value, {})
        venue_id = (mapping_row.get(key_id_field) or "").strip()
        match_type = (mapping_row.get("match_type") or mapping_row.get("status") or "unmatched").strip()
        stage = classify_match_stage(match_type)

        if venue_id:
            matched_distinct += 1
            stage_counts[stage]["distinct"] += 1
            stage_counts[stage]["rows"] += row_count
            continue

        if stage == "explicit skip / non_venue":
            skipped_distinct += 1
            skipped_rows += row_count
            stage_counts[stage]["distinct"] += 1
            stage_counts[stage]["rows"] += row_count
            continue

        unmatched_distinct += 1
        unmatched_rows += row_count

    return {
        "distinct_inputs": len(input_counts),
        "matched_distinct": matched_distinct,
        "unmatched_distinct": unmatched_distinct,
        "unmatched_rows": unmatched_rows,
        "skipped_or_non_venue_distinct": skipped_distinct,
        "skipped_or_non_venue_rows": skipped_rows,
        "stage_counts": {label: counts for label, counts in stage_counts.items() if counts["distinct"]},
    }


def compute_mapping_metrics(
    conference_input_csv: str | Path,
    conference_mapping_csv: str | Path,
    journal_input_csv: str | Path,
    journal_mapping_csv: str | Path,
) -> dict[str, dict[str, object]]:
    conference_input_counts = _load_value_counts(conference_input_csv, "booktitle")
    conference_mapping_rows = _load_mapping_rows(conference_mapping_csv, "booktitle")
    journal_input_counts = _load_value_counts(journal_input_csv, "journal")
    journal_mapping_rows = _load_mapping_rows(journal_mapping_csv, "dblp_journal_name")

    return {
        "conference": _summarize_mapping(
            conference_input_counts,
            conference_mapping_rows,
            "conf_id",
        ),
        "journal": _summarize_mapping(
            journal_input_counts,
            journal_mapping_rows,
            "journal_id",
        ),
    }
