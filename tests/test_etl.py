import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from etl.match_journals import (
    OVERLAP_THRESHOLD,
    SHORT_NAME_OVERLAP_THRESHOLD,
    build_local_dblp_exact_series_collision_reason,
    build_local_dblp_series_blocked_reason,
    canonical_key,
    classify_confidence,
    expand_abbrev,
    find_best_overlap_match,
    load_manual_aliases,
    merge_intentional_null_candidate_rows,
    normalize,
    overlap_score_from_tokens,
    resolve_local_dblp_journal_match,
    required_overlap_threshold,
    should_accept_overlap,
    token_overlap,
    tokens,
)
from etl.venue_matching import VenueRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_local_module(module_name: str, relative_path: str):
    module_path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expand_abbrev_expands_common_journal_terms():
    expanded = expand_abbrev("IEEE Trans. Knowl. Data Eng.")
    assert "transactions" in expanded
    assert "knowledge" in expanded
    assert "engineering" in expanded


def test_normalize_expands_abbreviations_and_cleans_punctuation():
    assert (
        normalize(" IEEE Trans. Knowl., Data Eng. ")
        == "ieee transactions knowledge data engineering"
    )


def test_canonical_key_normalizes_manual_alias_lookup_values():
    assert canonical_key(" Math. Program. ") == canonical_key("math program")


def test_tokens_removes_stopwords():
    assert tokens("Journal of the ACM") == {"journal", "acm"}


def test_token_overlap_exact_match_is_one():
    assert token_overlap("Machine Learning", "Machine Learning") == 1.0


def test_token_overlap_partial_match_scores_fractionally():
    score = token_overlap("Journal of Machine Learning", "Machine Learning")
    assert score == pytest.approx(2 / 3)


def test_token_overlap_handles_abbreviated_journal_prefixes():
    assert token_overlap("Acta Inf.", "Acta Informatica") == pytest.approx(1.0)
    assert token_overlap("Comput. Geom.", "Computational Geometry") == pytest.approx(1.0)


def test_find_best_overlap_match_returns_highest_scoring_candidate():
    db_journals = [
        (1, "Distributed Systems", tokens("Distributed Systems")),
        (2, "Machine Learning", tokens("Machine Learning")),
    ]

    best_jid, best_title, _best_tokens, best_score = find_best_overlap_match(
        tokens("Distributed"),
        db_journals,
    )

    assert best_jid == 1
    assert best_title == "Distributed Systems"
    assert best_score == pytest.approx(0.5)


def test_required_overlap_threshold_uses_short_name_guard():
    assert required_overlap_threshold({"distributed"}, {"distributed", "systems"}) == (
        SHORT_NAME_OVERLAP_THRESHOLD
    )
    assert required_overlap_threshold(
        {"distributed", "systems"},
        {"distributed", "systems", "journal"},
    ) == OVERLAP_THRESHOLD


def test_low_token_guard_rejects_weak_short_match():
    source_tokens = {"distributed"}
    target_tokens = {"distributed", "systems"}
    score = overlap_score_from_tokens(source_tokens, target_tokens)

    assert score == pytest.approx(0.5)
    assert not should_accept_overlap(source_tokens, target_tokens, score)


def test_confidence_bands_match_output_contract():
    assert classify_confidence(0.70) == "high"
    assert classify_confidence(0.60) == "medium"
    assert classify_confidence(0.45) == "low"
    assert classify_confidence(0.0, matched=False) == "unmatched"


def test_load_manual_aliases_supports_match_and_unmatch_actions():
    alias_file = Path(".tmp") / f"journal_manual_aliases_{uuid4().hex}.csv"
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_text(
        "\n".join(
            [
                "dblp_journal_name,journal_id,action,notes",
                "Fundam. Inform.,11956,match,Known abbreviation",
                "Object Oriented Systems,,unmatch,Reject risky false positive",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        aliases = load_manual_aliases({11956}, manual_alias_csv=alias_file)

        assert aliases[canonical_key("fundam inform")]["action"] == "match"
        assert aliases[canonical_key("fundam inform")]["journal_id"] == 11956
        assert aliases[canonical_key("Object Oriented Systems")]["action"] == "unmatch"
        assert aliases[canonical_key("Object Oriented Systems")]["journal_id"] is None
    finally:
        alias_file.unlink(missing_ok=True)


def test_load_manual_aliases_preserves_manual_review_metadata():
    alias_file = Path(".tmp") / f"journal_manual_aliases_{uuid4().hex}.csv"
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_text(
        "\n".join(
            [
                "dblp_journal_name,journal_id,action,notes,date_added,added_by",
                "iJET,8537,match,Reviewed deterministic branded title alias,2026-03-31,manual-reviewed-journal-batch-1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        aliases = load_manual_aliases({8537}, manual_alias_csv=alias_file)

        assert aliases[canonical_key("iJET")]["action"] == "match"
        assert aliases[canonical_key("iJET")]["journal_id"] == 8537
        assert aliases[canonical_key("iJET")]["date_added"] == "2026-03-31"
        assert aliases[canonical_key("iJET")]["added_by"] == "manual-reviewed-journal-batch-1"
    finally:
        alias_file.unlink(missing_ok=True)


def test_load_manual_aliases_preserves_batch_3_manual_review_metadata():
    alias_file = Path(".tmp") / f"journal_manual_aliases_{uuid4().hex}.csv"
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_text(
        "\n".join(
            [
                "dblp_journal_name,journal_id,action,notes,date_added,added_by",
                "JTAER,5637,match,Reviewed exact DBLP series journals/jtaer,2026-03-31,manual-reviewed-journal-batch-3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        aliases = load_manual_aliases({5637}, manual_alias_csv=alias_file)

        assert aliases[canonical_key("JTAER")]["action"] == "match"
        assert aliases[canonical_key("JTAER")]["journal_id"] == 5637
        assert aliases[canonical_key("JTAER")]["date_added"] == "2026-03-31"
        assert aliases[canonical_key("JTAER")]["added_by"] == "manual-reviewed-journal-batch-3"
    finally:
        alias_file.unlink(missing_ok=True)


def test_load_manual_aliases_ignores_non_csv_placeholder_files(caplog):
    alias_file = Path(".tmp") / f"journal_manual_aliases_{uuid4().hex}.csv"
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_text(
        "\n".join(
            [
                "version https://git-lfs.github.com/spec/v1",
                "oid sha256:placeholder",
                "size 1234",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        with caplog.at_level("WARNING"):
            aliases = load_manual_aliases({11956}, manual_alias_csv=alias_file)

        assert aliases == {}
        assert "WARNING: skipping manual alias file" in caplog.text
    finally:
        alias_file.unlink(missing_ok=True)


def test_conference_manual_overrides_ignore_non_csv_placeholder_files(monkeypatch, caplog):
    module = load_local_module("conference_match_for_test", "etl/05_match_conferences.py")
    alias_file = Path(".tmp") / f"conference_manual_aliases_{uuid4().hex}.csv"
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_text(
        "\n".join(
            [
                "version https://git-lfs.github.com/spec/v1",
                "oid sha256:placeholder",
                "size 1234",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        monkeypatch.setattr(module, "MANUAL_ALIAS_CSV", str(alias_file))

        with caplog.at_level("WARNING"):
            overrides = module.load_manual_overrides(set(), {}, {})

        assert overrides == {}
        assert "WARNING: skipping manual alias file" in caplog.text
    finally:
        alias_file.unlink(missing_ok=True)


def test_journal_main_loads_saved_variant_cache_before_building_records(monkeypatch):
    module = load_local_module("journal_match_for_test", "etl/06_match_journals.py")
    tmp_dir = Path(".tmp") / f"journal-main-variant-cache-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    observed = {}
    sentinel_variant_cache = {"loaded": "variant-cache"}

    class FakeCursor:
        def close(self):
            return None

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    def fake_load_variant_cache(path=None):
        observed["variant_cache_path"] = path
        return sentinel_variant_cache

    def fake_load_journal_records(cursor, variant_cache=None):
        observed["cursor_type"] = type(cursor).__name__
        observed["variant_cache"] = variant_cache
        return []

    monkeypatch.setattr(module, "get_local_dblp_csv_index", lambda: {})
    monkeypatch.setattr(module.mysql.connector, "connect", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(module, "load_variant_cache", fake_load_variant_cache)
    monkeypatch.setattr(module, "load_journal_records", fake_load_journal_records)
    monkeypatch.setattr(module, "build_local_dblp_journal_registry_map", lambda records, index: {})
    monkeypatch.setattr(module, "load_journal_name_counts", lambda: {})
    monkeypatch.setattr(module, "load_persistent_venue_aliases", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "load_manual_aliases", lambda *args, **kwargs: {})

    monkeypatch.setattr(module, "OUT_MAPPING", str(tmp_dir / "journal_name_to_id.csv"))
    monkeypatch.setattr(module, "OUT_UNMATCHED", str(tmp_dir / "unmatched_journals.txt"))
    monkeypatch.setattr(module, "OUT_REVIEW", str(tmp_dir / "journal_match_review.csv"))
    monkeypatch.setattr(module, "OUT_PROPOSALS", str(tmp_dir / "journal_alias_proposals.csv"))
    monkeypatch.setattr(
        module,
        "OUT_APPROVE_CANDIDATES",
        str(tmp_dir / "journal_alias_approve_candidates.csv"),
    )
    monkeypatch.setattr(
        module,
        "OUT_INTENTIONAL_NULL_CANDIDATES",
        str(tmp_dir / "journal_intentional_null_candidates.csv"),
    )
    monkeypatch.setattr(
        module,
        "OUT_INTENTIONAL_NULL_AUDIT",
        str(tmp_dir / "journal_intentional_null_audit.csv"),
    )
    monkeypatch.setattr(module, "OUT_VARIANT_CACHE", str(tmp_dir / "journal_variant_cache.csv"))
    monkeypatch.setattr(module, "OUT_ALIAS_REVIEW", str(tmp_dir / "venue_alias_review.csv"))
    monkeypatch.setattr(module, "OUT_ALIAS_MEMORY", str(tmp_dir / "venue_aliases.csv"))
    monkeypatch.setattr(module, "OUT_MATCH_AUDIT", str(tmp_dir / "venue_match_audit.csv"))
    monkeypatch.setattr(module, "MANUAL_ALIAS_CSV", str(tmp_dir / "journal_manual_aliases.csv"))

    module.main()

    assert observed["variant_cache"] == sentinel_variant_cache
    assert observed["cursor_type"] == "FakeCursor"


def test_merge_intentional_null_candidate_rows_adds_report_like_review_rows():
    review_rows = [
        {
            "dblp_journal_name": "IWBS Report",
            "row_count": 158,
            "candidate_1_title": "",
            "best_score": "0.00",
            "blocked_reason": "likely non-rankable publication family",
            "suggested_action": "intentional null / likely not rankable",
        },
        {
            "dblp_journal_name": "Transactions of the SDPS",
            "row_count": 309,
            "candidate_1_title": "",
            "best_score": "0.00",
            "blocked_reason": "no conservative candidate found",
            "suggested_action": "review manually",
        },
    ]
    explicit_rows = [
        {
            "raw_journal_name": "Computerworld",
            "rows": 1,
            "candidate_title": "",
            "score": "1.00",
            "current_reason": "explicitly marked non-rankable",
        }
    ]

    merged_rows = merge_intentional_null_candidate_rows(review_rows, explicit_rows)

    assert [row["raw_journal_name"] for row in merged_rows] == [
        "IWBS Report",
        "Computerworld",
    ]
    assert next(row for row in merged_rows if row["raw_journal_name"] == "IWBS Report")[
        "current_reason"
    ] == "likely non-rankable publication family"


def test_local_dblp_registry_matches_unique_journal_series_to_ranked_parenthetical_title():
    records = [VenueRecord(5111, "Computing (Vienna/New York)")]
    local_dblp_index = {
        "journal_aliases": {
            "computing": [
                {
                    "value": "computing",
                    "journal_key": "journals/computing",
                    "canonical_venue_id": "dblp-journals-computing",
                    "count": 12,
                    "dominant_title": "Computing",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "Springer",
                }
            ]
        },
        "journal_registry": {
            "journals/computing": {
                "canonical_venue_id": "dblp-journals-computing",
                "journal_key": "journals/computing",
                "row_count": 12,
                "distinct_titles": 1,
                "dominant_title": "Computing",
                "alternate_titles": [],
                "abbreviated_titles": [],
                "publisher": "Springer",
            }
        },
    }

    result = resolve_local_dblp_journal_match("Computing", records, local_dblp_index)

    assert result.status == "matched"
    assert result.venue_id == 5111
    assert result.match_type == "dblp-local-csv-registry"


def test_local_dblp_registry_prefers_exact_series_continuation_over_acronym_noise():
    records = [
        VenueRecord(
            13242,
            "Digital Creativity",
            metadata={"alternate_titles": ("Intelligent Tutoring Media",)},
        ),
        VenueRecord(8566, "Information Technology and Management"),
        VenueRecord(1672, "IEEE Transactions on Multimedia"),
    ]
    local_dblp_index = {
        "journal_aliases": {
            "intelligent tutoring media": [
                {
                    "value": "intelligent tutoring media",
                    "journal_key": "journals/creativity",
                    "canonical_venue_id": "dblp-journals-creativity",
                    "count": 202,
                    "dominant_title": "Intelligent Tutoring Media",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "",
                }
            ],
            "itm": [
                {
                    "value": "itm",
                    "journal_key": "journals/creativity",
                    "canonical_venue_id": "dblp-journals-creativity",
                    "count": 202,
                    "dominant_title": "Intelligent Tutoring Media",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "",
                },
                {
                    "value": "itm",
                    "journal_key": "journals/itm",
                    "canonical_venue_id": "dblp-journals-itm",
                    "count": 306,
                    "dominant_title": "Information Technology and Management",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "",
                },
                {
                    "value": "itm",
                    "journal_key": "journals/tmm",
                    "canonical_venue_id": "dblp-journals-tmm",
                    "count": 1504,
                    "dominant_title": "IEEE Transactions on Multimedia",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "",
                },
            ],
        },
        "journal_registry": {
            "journals/creativity": {
                "canonical_venue_id": "dblp-journals-creativity",
                "journal_key": "journals/creativity",
                "row_count": 202,
                "distinct_titles": 2,
                "dominant_title": "Digital Creativity",
                "alternate_titles": ["Intelligent Tutoring Media"],
                "abbreviated_titles": [],
                "publisher": "",
            },
            "journals/itm": {
                "canonical_venue_id": "dblp-journals-itm",
                "journal_key": "journals/itm",
                "row_count": 306,
                "distinct_titles": 1,
                "dominant_title": "Information Technology and Management",
                "alternate_titles": [],
                "abbreviated_titles": [],
                "publisher": "",
            },
            "journals/tmm": {
                "canonical_venue_id": "dblp-journals-tmm",
                "journal_key": "journals/tmm",
                "row_count": 1504,
                "distinct_titles": 1,
                "dominant_title": "IEEE Transactions on Multimedia",
                "alternate_titles": [],
                "abbreviated_titles": [],
                "publisher": "",
            },
        },
    }

    result = resolve_local_dblp_journal_match(
        "Intelligent Tutoring Media",
        records,
        local_dblp_index,
    )

    assert result.status == "matched"
    assert result.venue_id == 13242
    assert result.match_type == "dblp-local-csv-exact-series"


def test_local_dblp_registry_keeps_ambiguous_abbreviation_in_review():
    records = [VenueRecord(1496, "Journal of Computational Physics")]
    local_dblp_index = {
        "journal_aliases": {
            "jcp": [
                {
                    "value": "jcp",
                    "journal_key": "journals/jcp",
                    "canonical_venue_id": "dblp-journals-jcp",
                    "count": 120,
                    "dominant_title": "JCP",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "Academy Publisher",
                },
                {
                    "value": "jcp",
                    "journal_key": "journals/jcphys",
                    "canonical_venue_id": "dblp-journals-jcphys",
                    "count": 75,
                    "dominant_title": "Journal of Computational Physics",
                    "alternate_titles": ["J. Comput. Physics"],
                    "abbreviated_titles": ["JCP"],
                    "publisher": "Elsevier",
                },
            ]
        },
        "journal_registry": {
            "journals/jcp": {
                "canonical_venue_id": "dblp-journals-jcp",
                "journal_key": "journals/jcp",
                "row_count": 120,
                "distinct_titles": 1,
                "dominant_title": "JCP",
                "alternate_titles": [],
                "abbreviated_titles": [],
                "publisher": "Academy Publisher",
            },
            "journals/jcphys": {
                "canonical_venue_id": "dblp-journals-jcphys",
                "journal_key": "journals/jcphys",
                "row_count": 75,
                "distinct_titles": 2,
                "dominant_title": "Journal of Computational Physics",
                "alternate_titles": ["J. Comput. Physics"],
                "abbreviated_titles": ["JCP"],
                "publisher": "Elsevier",
            },
        },
    }

    result = resolve_local_dblp_journal_match("JCP", records, local_dblp_index)

    assert result.status == "review"
    assert result.venue_id is None


def test_build_local_dblp_exact_series_collision_reason_flags_unmapped_jcp_alias_collision():
    local_dblp_index = {
        "journal_aliases": {
            "jcp": [
                {
                    "value": "jcp",
                    "journal_key": "journals/jcp",
                    "canonical_venue_id": "dblp-journals-jcp",
                    "count": 1956,
                    "dominant_title": "JCP",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "",
                },
                {
                    "value": "jcp",
                    "journal_key": "journals/jcphy",
                    "canonical_venue_id": "dblp-journals-jcphy",
                    "count": 3918,
                    "dominant_title": "J. Comput. Physics",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "",
                },
            ]
        }
    }

    reason = build_local_dblp_exact_series_collision_reason(
        "JCP",
        local_dblp_index,
        {"journals/jcphy": 1496},
    )

    assert reason == (
        'exact-series-collision: exact DBLP series "JCP" is unmapped locally, '
        'while alias also expands to "J. Comput. Physics"'
    )


def test_build_local_dblp_series_blocked_reason_reports_no_ranked_continuation():
    local_dblp_index = {
        "journal_aliases": {
            "jcm": [
                {
                    "value": "jcm",
                    "journal_key": "journals/jcm",
                    "canonical_venue_id": "dblp-journals-jcm",
                    "count": 516,
                    "dominant_title": "JCM",
                    "alternate_titles": (),
                    "abbreviated_titles": (),
                    "publisher": "",
                }
            ]
        }
    }

    reason = build_local_dblp_series_blocked_reason(
        "JCM",
        local_dblp_index,
        {},
    )

    assert reason == (
        'no-ranked-continuation: exact DBLP series "JCM" has no approved ranked '
        "continuation in the local authority layer"
    )
