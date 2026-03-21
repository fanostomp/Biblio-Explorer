import pytest

from etl.match_journals import (
    OVERLAP_THRESHOLD,
    SHORT_NAME_OVERLAP_THRESHOLD,
    classify_confidence,
    expand_abbrev,
    find_best_overlap_match,
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
