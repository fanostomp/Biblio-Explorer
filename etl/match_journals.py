"""Import-friendly wrapper around the journal matching ETL script."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_MODULE_PATH = Path(__file__).with_name("06_match_journals.py")
_SPEC = spec_from_file_location("etl._match_journals_impl", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load journal matcher module from {_MODULE_PATH}")

_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

ABBREV_MAP = _MODULE.ABBREV_MAP
COMPILED_ABBREV = _MODULE.COMPILED_ABBREV
OVERLAP_THRESHOLD = _MODULE.OVERLAP_THRESHOLD
SHORT_NAME_OVERLAP_THRESHOLD = _MODULE.SHORT_NAME_OVERLAP_THRESHOLD
STOPWORDS = _MODULE.STOPWORDS
MANUAL_ALIAS_CSV = _MODULE.MANUAL_ALIAS_CSV

expand_abbrev = _MODULE.expand_abbrev
normalize = _MODULE.normalize
canonical_key = _MODULE.canonical_key
tokens = _MODULE.tokens
load_manual_aliases = _MODULE.load_manual_aliases
overlap_score_from_tokens = _MODULE.overlap_score_from_tokens
token_overlap = _MODULE.token_overlap
required_overlap_threshold = _MODULE.required_overlap_threshold
should_accept_overlap = _MODULE.should_accept_overlap
classify_confidence = _MODULE.classify_confidence
find_best_overlap_match = _MODULE.find_best_overlap_match
resolve_local_dblp_journal_match = _MODULE.resolve_local_dblp_journal_match
build_local_dblp_exact_series_collision_reason = _MODULE.build_local_dblp_exact_series_collision_reason
build_local_dblp_series_blocked_reason = _MODULE.build_local_dblp_series_blocked_reason
merge_intentional_null_candidate_rows = _MODULE.merge_intentional_null_candidate_rows
main = _MODULE.main
