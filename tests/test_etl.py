from pathlib import Path
from uuid import uuid4

import pytest

from etl.match_journals import (
    OVERLAP_THRESHOLD,
    SHORT_NAME_OVERLAP_THRESHOLD,
    canonical_key,
    classify_confidence,
    expand_abbrev,
    find_best_overlap_match,
    load_manual_aliases,
    normalize,
    overlap_score_from_tokens,
    required_overlap_threshold,
    should_accept_overlap,
    token_overlap,
    tokens,
)


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
