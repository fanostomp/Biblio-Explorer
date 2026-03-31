import json
import csv
from pathlib import Path
from uuid import uuid4

import pytest

from etl.venue_matching import (
    ExternalSourceCandidate,
    FileBackedJsonCache,
    JsonApiClient,
    ManualOverride,
    MatchCandidate,
    MatchResult,
    VenueMatcher,
    VenueNormalizer,
    VenueRecord,
    build_dblp_csv_index,
    build_blocked_reason,
    build_dblp_snapshot_index,
    classify_conference_review_bucket,
    classify_journal_suggested_action,
    detect_dblp_csv_inputs,
    classify_match_stage,
    build_intentional_null_audit_sample,
    load_persistent_venue_aliases,
    split_conference_review_rows_by_action,
    split_journal_review_rows_by_action,
    stable_canonical_venue_id,
    sync_alias_review_statuses,
    sync_review_statuses_from_resolved_rows,
)


def test_venue_normalizer_builds_parent_and_acronym_forms():
    forms = VenueNormalizer().normalize(
        "Proceedings of the 10th HLT-NAACL (Short Papers), Vol. II"
    )

    assert forms.parent == "hlt naacl"
    assert "naacl" in forms.acronym_candidates
    assert "hlt-naacl" in forms.segment_candidates
    assert "hlt-naacl" in forms.variant_keys


@pytest.mark.parametrize(
    ("query", "expected_id", "expected_match_type"),
    [
        ("HLT-NAACL", 264, "segment-acronym"),
        ("ECDL", 522, "historical-alias"),
        ("ICSM", 1028, "historical-alias"),
        ("UML", 1056, "historical-alias"),
        ("IPPS/SPDP", 613, "historical-alias"),
    ],
)
def test_conference_matcher_resolves_historical_and_segment_aliases(
    query, expected_id, expected_match_type
):
    matcher = VenueMatcher(
        venue_type="conference",
        records=[
            VenueRecord(264, "North American Association for Computational Linguistics", "NAACL"),
            VenueRecord(
                522,
                "International Conference on Theory and Practice of Digital Libraries (was ECDL until 2010)",
                "TPDL",
            ),
            VenueRecord(
                1028,
                "IEEE International Conference on Software Maintenance and Evolution (prior to 2014 was ICSM, IEEE International Conference on Software Maintenance)",
                "ICSME",
            ),
            VenueRecord(
                1056,
                "International Conference on Model Driven Engineering Languages and Systems (Previously UML, changed in 2005)",
                "MODELS",
            ),
            VenueRecord(
                613,
                "IEEE International Parallel and Distributed Processing Symposium (was IPPS and SPDP)",
                "IPDPS",
            ),
        ],
    )

    result = matcher.match(query)

    assert result.status == "matched"
    assert result.venue_id == expected_id
    assert result.match_type == expected_match_type


def test_conference_matcher_matches_exact_acronym_and_title_series_queries():
    matcher = VenueMatcher(
        venue_type="conference",
        records=[
            VenueRecord(1, "Hawaii International Conference on System Sciences", "HICSS"),
            VenueRecord(2, "Americas Conference on Information Systems", "AMCIS"),
            VenueRecord(3, "International Conference on Information Systems", "ICIS"),
            VenueRecord(4, "European Conference on Information Systems", "ECIS"),
            VenueRecord(5, "Pacific Asia Conference on Information Systems", "PACIS"),
        ],
    )

    assert matcher.match("HICSS").venue_id == 1
    assert matcher.match("Americas Conference on Information Systems").venue_id == 2
    assert matcher.match("ICIS").venue_id == 3
    assert matcher.match("ECIS").venue_id == 4
    assert matcher.match("PACIS").venue_id == 5


def test_conference_matcher_sends_conflicted_acronym_to_review():
    matcher = VenueMatcher(
        venue_type="conference",
        conflict_acronyms={"ACE"},
        records=[
            VenueRecord(
                10,
                "ACM International Conference on Advances in Computer Entertainment",
                "ACE",
            ),
        ],
    )

    result = matcher.match("ACE")

    assert result.status == "review"
    assert result.venue_id is None
    assert result.candidates
    assert result.candidates[0].venue_id == 10


def test_manual_override_can_mark_non_venue():
    matcher = VenueMatcher(
        venue_type="conference",
        manual_overrides={
            "festschrift for example": ManualOverride(action="non_venue", venue_id=None)
        },
        records=[],
    )

    result = matcher.match("Festschrift for Example")

    assert result.status == "non_venue"
    assert result.venue_id is None
    assert result.match_type == "manual-non-venue"


def test_journal_matcher_derives_international_journal_acronyms():
    matcher = VenueMatcher(
        venue_type="journal",
        records=[
            VenueRecord(
                22,
                "International Journal of Agent Oriented Software Engineering",
            )
        ],
    )

    result = matcher.match("IJAOSE")

    assert result.status == "matched"
    assert result.venue_id == 22
    assert result.match_type == "derived-acronym"


def test_external_source_candidate_matches_by_alt_title_and_issn():
    matcher = VenueMatcher(
        venue_type="journal",
        records=[
            VenueRecord(
                77,
                "Journal of Example Systems",
                metadata={"issn": ("1234-5678",), "issn_l": "1234-5678"},
            )
        ],
    )

    result = matcher.match(
        "JES",
        external_candidates=[
            ExternalSourceCandidate(
                source="openalex",
                display_name="Journal of Example Systems",
                alternate_titles=("JES",),
                abbreviated_title="JES",
                issn=("1234-5678",),
                issn_l="1234-5678",
                source_type="journal",
            )
        ],
    )

    assert result.status == "matched"
    assert result.venue_id == 77
    assert result.match_type == "openalex-issn"


def test_local_dblp_csv_candidate_matches_abbreviated_journal_query_to_full_title():
    matcher = VenueMatcher(
        venue_type="journal",
        records=[VenueRecord(91, "Literary and Linguistic Computing")],
    )

    result = matcher.match(
        "LLC",
        external_candidates=[
            ExternalSourceCandidate(
                source="dblp_local_csv",
                display_name="Literary and Linguistic Computing",
                alternate_titles=("LLC",),
                abbreviated_title="LLC",
                source_type="journal",
            )
        ],
    )

    assert result.status == "matched"
    assert result.venue_id == 91
    assert result.match_type == "dblp_local_csv-title"


def test_journal_matcher_matches_base_title_when_ranked_title_has_location_parenthetical():
    matcher = VenueMatcher(
        venue_type="journal",
        records=[VenueRecord(5111, "Computing (Vienna/New York)")],
    )

    result = matcher.match("Computing")

    assert result.status == "matched"
    assert result.venue_id == 5111


def test_dblp_snapshot_index_extracts_article_and_proceedings_aliases():
    snapshot_path = Path(".tmp") / f"dblp-sample-{uuid4().hex}.xml"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<dblp>
  <article key="journals/example/Smith24">
    <journal>J. Example Syst.</journal>
    <url>db/journals/example/example24.html#Smith24</url>
  </article>
  <proceedings key="conf/example/2024">
    <title>Proceedings of the Example Conference 2024</title>
    <booktitle>EXCONF</booktitle>
    <url>db/conf/example/example2024.html</url>
  </proceedings>
</dblp>
""",
        encoding="utf-8",
    )

    try:
        index = build_dblp_snapshot_index(snapshot_path)

        assert "j example syst" in index["journal_aliases"]
        assert "exconf" in index["conference_aliases"]
        assert index["conference_aliases"]["exconf"][0]["dblp_key"] == "conf/example/2024"
    finally:
        snapshot_path.unlink(missing_ok=True)


def test_stable_canonical_venue_id_uses_series_identity_not_row_order():
    assert stable_canonical_venue_id("conf/example") == stable_canonical_venue_id(
        "conf/example/2024"
    )
    assert stable_canonical_venue_id("streams/example") == "dblp-streams-example"


def test_dblp_snapshot_index_builds_registry_with_parent_series_and_stable_ids():
    snapshot_path = Path(".tmp") / f"dblp-registry-{uuid4().hex}.xml"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<dblp>
  <proceedings key="conf/example/2024">
    <title>Proceedings of the International Conference on Example Systems 2024</title>
    <booktitle>ICES 2024</booktitle>
    <url>db/conf/example/example2024.html</url>
  </proceedings>
  <proceedings key="conf/example/2025">
    <title>Adjunct Proceedings of the International Conference on Example Systems 2025</title>
    <booktitle>ICES 2025 Companion</booktitle>
    <url>db/conf/example/example2025.html</url>
  </proceedings>
  <inproceedings key="conf/example/ExampleWorkshop25">
    <title>Workshop Paper</title>
    <booktitle>International Conference on Example Systems Workshops</booktitle>
    <crossref>conf/example/2025</crossref>
    <url>db/conf/example/example2025w.html#ExampleWorkshop25</url>
  </inproceedings>
</dblp>
""",
        encoding="utf-8",
    )

    try:
        index = build_dblp_snapshot_index(snapshot_path)

        registry = index["conference_registry"]
        canonical_id = stable_canonical_venue_id("conf/example")
        assert canonical_id in registry
        assert registry[canonical_id]["series_key"] == "conf/example"
        assert "ices" in registry[canonical_id]["acronyms"]
        assert "international conference on example systems" in registry[canonical_id]["parent_aliases"]
        assert any(
            relation["relation"] == "parent-series"
            and relation["child"] == "international conference on example systems workshops"
            for relation in registry[canonical_id]["child_relationships"]
        )
        assert any(
            item["canonical_venue_id"] == canonical_id
            for item in index["conference_aliases"]["ices 2024"]
        )
    finally:
        snapshot_path.unlink(missing_ok=True)


def test_detect_dblp_csv_inputs_discovers_article_and_inproceedings_files():
    dataset_dir = Path(".tmp") / f"dblp-csv-detect-{uuid4().hex}"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "input_article.csv").write_text(
        "id;journal;key;title\n1;Journal of Example;journals/example/A1;Paper\n",
        encoding="utf-8",
    )
    (dataset_dir / "input_inproceedings_main.csv").write_text(
        "id;booktitle;crossref;key;title\n1;EXCONF;conf/example/2024;conf/example/Paper1;Paper\n",
        encoding="utf-8",
    )

    detected = detect_dblp_csv_inputs(dataset_dir)

    assert detected["article"]["path"].name == "input_article.csv"
    assert detected["inproceedings"]["path"].name == "input_inproceedings_main.csv"
    assert detected["article"]["columns"] == ["id", "journal", "key", "title"]
    assert detected["inproceedings"]["columns"] == ["id", "booktitle", "crossref", "key", "title"]


def test_dblp_csv_index_extracts_local_conference_and_journal_registries():
    dataset_dir = Path(".tmp") / f"dblp-csv-index-{uuid4().hex}"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "input_article.csv").write_text(
        "\n".join(
            [
                "id;journal;key;publisher;title",
                "1;Literary and Linguistic Computing;journals/llc/A1;OUP;Paper A",
                "2;LLC;journals/llc/A2;OUP;Paper B",
                "3;IEEE Trans. Knowl. Data Eng.;journals/tkde/A3;IEEE;Paper C",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (dataset_dir / "input_inproceedings.csv").write_text(
        "\n".join(
            [
                "id;booktitle;crossref;key;title;url;year",
                "1;ECML/PKDD;conf/ecmlpkdd/2024;conf/ecmlpkdd/Paper1;Paper 1;db/conf/ecmlpkdd/2024.html#Paper1;2024",
                "2;Proceedings of the European Conference on Machine Learning and Principles and Practice of Knowledge Discovery in Databases Companion, Vol. I;conf/ecmlpkdd/2024;conf/ecmlpkdd/Paper2;Paper 2;db/conf/ecmlpkdd/2024.html#Paper2;2024",
                "3;CIKM-iNEWS;conf/cikm/2023;conf/cikm/News23;Paper 3;db/conf/cikm/2023.html#News23;2023",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    index = build_dblp_csv_index(dataset_dir=dataset_dir)

    assert index["inputs"]["article"]["columns"] == ["id", "journal", "key", "publisher", "title"]
    assert index["inputs"]["inproceedings"]["columns"] == [
        "id",
        "booktitle",
        "crossref",
        "key",
        "title",
        "url",
        "year",
    ]

    llc_registry = index["journal_registry"]["journals/llc"]
    assert llc_registry["canonical_venue_id"] == "dblp-journals-llc"
    assert llc_registry["dominant_title"] == "Literary and Linguistic Computing"
    assert "LLC" in llc_registry["abbreviated_titles"]
    assert any(item["journal_key"] == "journals/llc" for item in index["journal_aliases"]["llc"])

    ecml_registry = index["conference_series_registry"]["conf/ecmlpkdd"]
    assert ecml_registry["canonical_venue_id"] == stable_canonical_venue_id("conf/ecmlpkdd")
    assert ecml_registry["dominant_booktitle"] == "ECML/PKDD"
    assert "ecmlpkdd" in ecml_registry["acronyms"]
    assert (
        "european conference on machine learning and principles and practice of knowledge discovery in databases"
        in ecml_registry["parent_aliases"]
    )
    assert index["conference_series_by_booktitle"]["ECML/PKDD"] == "conf/ecmlpkdd"
    assert any(
        item["series_key"] == "conf/ecmlpkdd" for item in index["conference_aliases"]["ecml pkdd"]
    )


def test_dblp_csv_index_persists_registry_snapshots_when_requested():
    dataset_dir = Path(".tmp") / f"dblp-csv-persist-{uuid4().hex}"
    persist_dir = Path(".tmp") / f"dblp-csv-persist-out-{uuid4().hex}"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    persist_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "input_article.csv").write_text(
        "\n".join(
            [
                "id;journal;key;publisher;title",
                "1;Computing;journals/computing/A1;Springer;Paper A",
                "2;J. Comput. Physics;journals/jcphys/A2;Elsevier;Paper B",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (dataset_dir / "input_inproceedings.csv").write_text(
        "\n".join(
            [
                "id;booktitle;crossref;key;title;url;year",
                "1;ISQED;conf/isqed/2013;conf/isqed/Paper1;Paper 1;db/conf/isqed/isqed2013.html#Paper1;2013",
                "2;International Symposium on Quality Electronic Design;conf/isqed/2014;conf/isqed/Paper2;Paper 2;db/conf/isqed/isqed2014.html#Paper2;2014",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    index = build_dblp_csv_index(dataset_dir=dataset_dir, persist_dir=persist_dir)

    conference_snapshot = persist_dir / "dblp_conference_csv_registry.json"
    journal_snapshot = persist_dir / "dblp_journal_csv_registry.json"

    assert conference_snapshot.exists()
    assert journal_snapshot.exists()
    assert index["conference_series_registry"]["conf/isqed"]["representative_title"] == (
        "International Symposium on Quality Electronic Design"
    )

    conference_payload = json.loads(conference_snapshot.read_text(encoding="utf-8"))
    journal_payload = json.loads(journal_snapshot.read_text(encoding="utf-8"))

    assert conference_payload["inputs"]["inproceedings"]["columns"] == [
        "id",
        "booktitle",
        "crossref",
        "key",
        "title",
        "url",
        "year",
    ]
    assert "conf/isqed" in conference_payload["conference_series_registry"]
    assert journal_payload["journal_registry"]["journals/computing"]["dominant_title"] == "Computing"


def test_classify_match_stage_treats_legacy_snapshot_labels_as_local_dblp_csv():
    assert classify_match_stage("dblp-local-snapshot") == "DBLP local CSV registry"
    assert classify_match_stage("dblp-local-csv-registry") == "DBLP local CSV registry"
    assert classify_match_stage("dblp-local-csv-exact-series") == "DBLP local CSV registry"


def test_external_source_issn_match_beats_title_only_match():
    matcher = VenueMatcher(
        venue_type="journal",
        records=[
            VenueRecord(
                1,
                "Journal of Example Systems",
                metadata={"issn": ("1111-1111",), "issn_l": "1111-1111"},
            ),
            VenueRecord(
                2,
                "Journal of Example Studies",
                metadata={"issn": ("2222-2222",), "issn_l": "2222-2222"},
            ),
        ],
    )

    result = matcher.match(
        "J. Example Syst.",
        external_candidates=[
            ExternalSourceCandidate(
                source="openalex",
                display_name="Journal of Example Studies",
                alternate_titles=("Journal of Example Systems", "J. Example Syst."),
                issn=("1111-1111",),
                issn_l="1111-1111",
                source_type="journal",
            )
        ],
    )

    assert result.status == "matched"
    assert result.venue_id == 1
    assert result.match_type == "openalex-issn"


def test_json_api_client_stays_offline_when_disabled(monkeypatch):
    cache_dir = Path(".tmp") / f"offline-cache-{uuid4().hex}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = FileBackedJsonCache(cache_dir)
    client = JsonApiClient(
        cache=cache,
        namespace="test",
        base_url="https://example.test/api",
        enabled=False,
    )

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("network should stay disabled")

    monkeypatch.setattr("etl.venue_matching.request.urlopen", fail_urlopen)

    assert client.fetch_json("offline", {"q": "example"}) is None


def test_conference_query_does_not_derive_two_token_initialism_from_acronym_like_label():
    matcher = VenueMatcher(
        venue_type="conference",
        records=[
            VenueRecord(375, "Financial Cryptography and Data Security Conference", "FC"),
        ],
    )

    result = matcher.match("FLAIRS Conference")

    assert result.status == "unmatched"


def test_load_persistent_venue_aliases_reads_active_match_rows():
    alias_path = Path(".tmp") / f"venue-aliases-{uuid4().hex}.csv"
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    alias_path.write_text(
        "\n".join(
            [
                "id,venue_type,alias,normalized_alias,canonical_id,source,confidence,is_active",
                "1,conference,Australian Conference on Artificial Intelligence,australian conference on artificial intelligence,110,review-approved,high,1",
                "2,conference,Inactive Alias,inactive alias,999,review-approved,high,0",
                "3,journal,JCP,jcp,7928,manual,low,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        aliases = load_persistent_venue_aliases(alias_path, "conference")

        assert aliases["australianconferenceonartificialintelligence"].action == "match"
        assert aliases["australianconferenceonartificialintelligence"].venue_id == 110
        assert "inactivealias" not in aliases
        assert "jcp" not in aliases
    finally:
        alias_path.unlink(missing_ok=True)


def test_build_blocked_reason_highlights_ambiguous_acronym_reviews():
    result = MatchResult(
        "review",
        None,
        "ambiguous-acronym",
        0.99,
        "review",
        "local",
        (
            MatchCandidate(
                421,
                "International Symposium on Information Assurance and Security",
                "exact-acronym",
                0.99,
                "local",
            ),
        ),
    )

    reason = build_blocked_reason(
        raw_name="IAS",
        venue_type="conference",
        result=result,
        score_gap=0.0,
        series_key="conf/IEEEias",
    )

    assert "acronym collision" in reason
    assert "constrained" in reason


def test_conference_bucket_distinguishes_high_confidence_collision_and_absent_series():
    bucket_a = classify_conference_review_bucket(
        raw_name="CARS",
        result=MatchResult(
            "review",
            None,
            "metadata-alias",
            0.96,
            "review",
            "local",
            (
                MatchCandidate(
                    373,
                    "European Dependable Computing Conference",
                    "metadata-alias",
                    0.96,
                    "local",
                ),
            ),
        ),
    )
    bucket_b = classify_conference_review_bucket(
        raw_name="ICEC",
        result=MatchResult(
            "review",
            None,
            "ambiguous-acronym",
            0.99,
            "review",
            "local",
            (
                MatchCandidate(
                    325,
                    "International Conference on Entertainment Computing",
                    "exact-acronym",
                    0.99,
                    "local",
                ),
                MatchCandidate(
                    139,
                    "IEEE Congress on Evolutionary Computation",
                    "metadata-alias",
                    0.96,
                    "local",
                ),
            ),
        ),
    )
    bucket_c = classify_conference_review_bucket(
        raw_name="Bildverarbeitung für die Medizin",
        result=MatchResult("unmatched", None, "unmatched", 0.0, "unmatched", "none", ()),
    )

    assert bucket_a == "A"
    assert bucket_b == "B"
    assert bucket_c == "C"


def test_conference_bucket_treats_absent_series_acronyms_as_lookup_enrichment():
    bucket = classify_conference_review_bucket(
        raw_name="EGC",
        result=MatchResult("unmatched", None, "unmatched", 0.0, "unmatched", "none", ()),
        series_key="conf/f-egc",
    )

    assert bucket == "C"


def test_journal_suggested_action_flags_non_rankable_titles():
    action = classify_journal_suggested_action(
        raw_name="ACM Crossroads",
        result=MatchResult("non_venue", None, "manual-non-venue", 1.0, "non_venue", "manual", ()),
    )

    assert action == "intentional null / likely not rankable"


def test_build_blocked_reason_uses_risky_abbreviation_reason_for_short_near_tie_journal():
    result = MatchResult(
        "review",
        None,
        "derived-acronym",
        0.90,
        "review",
        "local",
        (
            MatchCandidate(9178, "Journal of Cardiovascular Medicine", "derived-acronym", 0.90, "local"),
            MatchCandidate(
                4867,
                "Journal of Change Management",
                "derived-acronym",
                0.88,
                "local",
            ),
        ),
    )

    reason = build_blocked_reason(
        raw_name="JCM",
        venue_type="journal",
        result=result,
        score_gap=0.02,
    )

    assert reason == "risky-abbreviation-near-tie"


@pytest.mark.parametrize(
    ("query", "title", "acronym"),
    [
        ("IAS", "International Symposium on Information Assurance and Security", "IAS"),
        ("ICEC", "International Conference on Entertainment Computing", "ICEC"),
        ("ICIC", "International Conference on Intelligent Computing", "ICIC"),
        ("ICEIS", "International Conference on Enterprise Information Systems", "ICEIS"),
        ("ICCA", "International Conference on Computers and Their Applications", "ICCA"),
        ("DFT", "International Symposium on Defect and Fault Tolerance in VLSI Systems", "DFT"),
        ("NEMS", "IEEE International Conference on Nano/Micro Engineered and Molecular Systems", "NEMS"),
        ("TSD", "International Conference on Text, Speech and Dialogue", "TSD"),
    ],
)
def test_frozen_conference_acronyms_stay_in_review_without_deterministic_evidence(
    query, title, acronym
):
    matcher = VenueMatcher(
        venue_type="conference",
        conflict_acronyms={query},
        records=[VenueRecord(1, title, acronym)],
    )

    result = matcher.match(query)

    assert result.status == "review"
    assert result.match_type == "ambiguous-acronym"


def test_frozen_conference_acronym_family_stays_review_only_without_conflict_file():
    matcher = VenueMatcher(
        venue_type="conference",
        records=[VenueRecord(1396, "International Conference on Internet Computing", "ICIC")],
    )

    result = matcher.match("ICIC (1)")

    assert result.status == "review"
    assert result.match_type == "ambiguous-acronym"


def test_sync_alias_review_statuses_marks_alias_memory_matches_as_approved():
    tmp_dir = Path(".tmp") / f"alias-review-sync-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"
    alias_path = tmp_dir / "venue_aliases.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,conference,ISGT Europe,isgt europe,2001,IEEE PES Innovative Smart Grid Technologies Europe,0.95,metadata-alias,"{""source"": ""review""}",pending,,',
                '2,journal,JCP,jcp,7928,Journal of Cancer Policy,0.90,derived-acronym,"{""source"": ""review""}",rejected,analyst,2026-03-29',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    alias_path.write_text(
        "\n".join(
            [
                "id,venue_type,alias,normalized_alias,canonical_id,source,confidence,is_active",
                "conference-isgt-europe,conference,ISGT Europe,isgt europe,2001,review-approved,high,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    sync_alias_review_statuses(review_path, alias_path, reviewed_at="2026-03-30")

    rows = review_path.read_text(encoding="utf-8").splitlines()
    assert any(",conference,ISGT Europe,isgt europe,2001," in row and ",approved," in row for row in rows)
    assert any(",journal,JCP,jcp,7928," in row and ",rejected,analyst,2026-03-29" in row for row in rows)


def test_sync_review_statuses_from_resolved_rows_marks_batch_2_seed_and_alias_approvals():
    tmp_dir = Path(".tmp") / f"resolved-review-sync-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,conference,ISGT Europe,isgt europe,,,0.00,unmatched,"{""series_key"": ""conf/isgteurope""}",pending,,',
                '2,journal,Software - Concepts and Tools,software concepts and tools,10723,IET Software,1.00,manual-match,"{""source"": ""manual""}",pending,,',
                '3,journal,JCP,jcp,7928,Journal of Cancer Policy,0.90,derived-acronym,"{""source"": ""review""}",pending,,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    updated = sync_review_statuses_from_resolved_rows(
        review_path,
        [
            {
                "venue_type": "conference",
                "raw_name": "ISGT Europe",
                "normalized_name": "isgt europe",
                "proposed_canonical_id": "2001",
                "proposed_canonical_title": "IEEE PES Innovative Smart Grid Technologies Conference Europe",
            },
            {
                "venue_type": "journal",
                "raw_name": "Software - Concepts and Tools",
                "normalized_name": "software concepts and tools",
                "proposed_canonical_id": "10723",
                "proposed_canonical_title": "Software - Concepts and Tools",
            },
        ],
        reviewed_by="manual-reviewed-seed-batch-2",
        reviewed_at="2026-03-30",
    )

    assert updated == 2

    with review_path.open("r", encoding="utf-8", newline="") as handle:
        rows = {
            (row["venue_type"], row["raw_name"]): row
            for row in csv.DictReader(handle)
        }

    isgt_row = rows[("conference", "ISGT Europe")]
    assert isgt_row["status"] == "approved"
    assert isgt_row["reviewed_by"] == "manual-reviewed-seed-batch-2"
    assert isgt_row["reviewed_at"] == "2026-03-30"
    assert isgt_row["proposed_canonical_id"] == "2001"
    assert (
        isgt_row["proposed_canonical_title"]
        == "IEEE PES Innovative Smart Grid Technologies Conference Europe"
    )

    software_row = rows[("journal", "Software - Concepts and Tools")]
    assert software_row["status"] == "approved"
    assert software_row["reviewed_by"] == "manual-reviewed-seed-batch-2"
    assert software_row["reviewed_at"] == "2026-03-30"
    assert software_row["proposed_canonical_id"] == "10723"
    assert software_row["proposed_canonical_title"] == "Software - Concepts and Tools"

    jcp_row = rows[("journal", "JCP")]
    assert jcp_row["status"] == "pending"
    assert jcp_row["reviewed_by"] == ""
    assert jcp_row["reviewed_at"] == ""


def test_sync_review_statuses_from_resolved_rows_prefers_row_level_seed_review_metadata():
    tmp_dir = Path(".tmp") / f"resolved-review-metadata-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,conference,ICNSC,icnsc,,,0.00,unmatched,"{""series_key"": ""conf/icnsc""}",pending,,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    updated = sync_review_statuses_from_resolved_rows(
        review_path,
        [
            {
                "venue_type": "conference",
                "raw_name": "ICNSC",
                "normalized_name": "icnsc",
                "proposed_canonical_id": "2002",
                "proposed_canonical_title": "ICNSC",
                "reviewed_by": "manual-reviewed-seed-batch-2",
                "reviewed_at": "2026-03-31",
            }
        ],
        reviewed_by="mapping-sync",
        reviewed_at="2026-03-31",
    )

    assert updated == 1

    with review_path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["status"] == "approved"
    assert row["reviewed_by"] == "manual-reviewed-seed-batch-2"
    assert row["reviewed_at"] == "2026-03-31"


def test_sync_review_statuses_from_resolved_rows_supports_batch_3_seed_review_metadata():
    tmp_dir = Path(".tmp") / f"resolved-review-batch3-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,conference,EUROMICRO,euromicro,,,0.00,unmatched,"{""series_key"": ""conf/euromicro""}",pending,,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    updated = sync_review_statuses_from_resolved_rows(
        review_path,
        [
            {
                "venue_type": "conference",
                "raw_name": "EUROMICRO",
                "normalized_name": "euromicro",
                "proposed_canonical_id": "3001",
                "proposed_canonical_title": "EUROMICRO",
                "reviewed_by": "manual-reviewed-seed-batch-3",
                "reviewed_at": "2026-03-31",
            }
        ],
        reviewed_by="mapping-sync",
        reviewed_at="2026-03-31",
    )

    assert updated == 1

    with review_path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["status"] == "approved"
    assert row["reviewed_by"] == "manual-reviewed-seed-batch-3"
    assert row["reviewed_at"] == "2026-03-31"


def test_sync_review_statuses_from_resolved_rows_supports_manual_reviewed_journal_batch_metadata():
    tmp_dir = Path(".tmp") / f"resolved-review-journal-batch-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,journal,iJET,ijet,14995,International Journal of Economic Theory,0.90,derived-acronym,"{""source"": ""review""}",pending,,',
                '2,journal,JCP,jcp,7928,Journal of Cancer Policy,0.90,derived-acronym,"{""source"": ""review""}",pending,,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    updated = sync_review_statuses_from_resolved_rows(
        review_path,
        [
            {
                "venue_type": "journal",
                "raw_name": "iJET",
                "normalized_name": "ijet",
                "proposed_canonical_id": "8537",
                "proposed_canonical_title": "International Journal of Emerging Technologies in Learning",
                "reviewed_by": "manual-reviewed-journal-batch-1",
                "reviewed_at": "2026-03-31",
            }
        ],
        reviewed_by="mapping-sync",
        reviewed_at="2026-03-31",
    )

    assert updated == 1

    with review_path.open("r", encoding="utf-8", newline="") as handle:
        rows = {
            (row["venue_type"], row["raw_name"]): row
            for row in csv.DictReader(handle)
        }

    ijet_row = rows[("journal", "iJET")]
    assert ijet_row["status"] == "approved"
    assert ijet_row["proposed_canonical_id"] == "8537"
    assert (
        ijet_row["proposed_canonical_title"]
        == "International Journal of Emerging Technologies in Learning"
    )
    assert ijet_row["reviewed_by"] == "manual-reviewed-journal-batch-1"
    assert ijet_row["reviewed_at"] == "2026-03-31"

    jcp_row = rows[("journal", "JCP")]
    assert jcp_row["status"] == "pending"
    assert jcp_row["reviewed_by"] == ""
    assert jcp_row["reviewed_at"] == ""


def test_sync_review_statuses_from_resolved_rows_updates_manual_batch_2_journal_evidence():
    tmp_dir = Path(".tmp") / f"resolved-review-journal-batch-2-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,journal,IJDMB,ijdmb,16531,International Journal of Data Mining and Bioinformatics,0.90,derived-acronym,"{""blocked_reason"": ""risky-abbreviation-near-tie"", ""source"": ""review""}",pending,,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    updated = sync_review_statuses_from_resolved_rows(
        review_path,
        [
            {
                "venue_type": "journal",
                "raw_name": "IJDMB",
                "normalized_name": "ijdmb",
                "proposed_canonical_id": "16531",
                "proposed_canonical_title": "International Journal of Data Mining and Bioinformatics",
                "reviewed_by": "manual-reviewed-journal-batch-2",
                "reviewed_at": "2026-03-31",
                "evidence_json": json.dumps(
                    {
                        "source": "manual-reviewed-alias",
                        "evidence_summary": [
                            "Exact DBLP series path is journals/ijdmb.",
                            "Sample titles align with data mining and bioinformatics, not digital multimedia broadcasting.",
                        ],
                    },
                    sort_keys=True,
                ),
            }
        ],
        reviewed_by="mapping-sync",
        reviewed_at="2026-03-31",
    )

    assert updated == 1

    with review_path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert row["status"] == "approved"
    assert row["reviewed_by"] == "manual-reviewed-journal-batch-2"
    assert row["reviewed_at"] == "2026-03-31"
    assert json.loads(row["evidence_json"]) == {
        "evidence_summary": [
            "Exact DBLP series path is journals/ijdmb.",
            "Sample titles align with data mining and bioinformatics, not digital multimedia broadcasting.",
        ],
        "source": "manual-reviewed-alias",
    }


def test_sync_review_statuses_from_resolved_rows_updates_manual_batch_3_journal_evidence():
    tmp_dir = Path(".tmp") / f"resolved-review-journal-batch-3-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,journal,JTAER,jtaer,,,0.00,unmatched,"{""blocked_reason"": ""no conservative candidate found"", ""source"": ""review""}",pending,,',
                '2,journal,JCP,jcp,7928,Journal of Cancer Policy,0.90,derived-acronym,"{""blocked_reason"": ""risky-abbreviation-near-tie"", ""source"": ""review""}",pending,,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    updated = sync_review_statuses_from_resolved_rows(
        review_path,
        [
            {
                "venue_type": "journal",
                "raw_name": "JTAER",
                "normalized_name": "jtaer",
                "proposed_canonical_id": "5637",
                "proposed_canonical_title": "Journal of Theoretical and Applied Electronic Commerce Research",
                "reviewed_by": "manual-reviewed-journal-batch-3",
                "reviewed_at": "2026-03-31",
                "evidence_json": json.dumps(
                    {
                        "source": "manual-reviewed-alias",
                        "evidence_summary": [
                            "Exact DBLP series path is journals/jtaer.",
                            "Sample titles align with electronic commerce research.",
                        ],
                    },
                    sort_keys=True,
                ),
            }
        ],
        reviewed_by="mapping-sync",
        reviewed_at="2026-03-31",
    )

    assert updated == 1

    with review_path.open("r", encoding="utf-8", newline="") as handle:
        rows = {
            (row["venue_type"], row["raw_name"]): row
            for row in csv.DictReader(handle)
        }

    jtaer_row = rows[("journal", "JTAER")]
    assert jtaer_row["status"] == "approved"
    assert jtaer_row["proposed_canonical_id"] == "5637"
    assert (
        jtaer_row["proposed_canonical_title"]
        == "Journal of Theoretical and Applied Electronic Commerce Research"
    )
    assert jtaer_row["reviewed_by"] == "manual-reviewed-journal-batch-3"
    assert jtaer_row["reviewed_at"] == "2026-03-31"
    assert json.loads(jtaer_row["evidence_json"]) == {
        "evidence_summary": [
            "Exact DBLP series path is journals/jtaer.",
            "Sample titles align with electronic commerce research.",
        ],
        "source": "manual-reviewed-alias",
    }

    jcp_row = rows[("journal", "JCP")]
    assert jcp_row["status"] == "pending"
    assert jcp_row["reviewed_by"] == ""
    assert jcp_row["reviewed_at"] == ""


def test_sync_review_statuses_from_resolved_rows_supports_manual_journal_catalog_batch_metadata():
    tmp_dir = Path(".tmp") / f"resolved-review-journal-catalog-batch-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    review_path = tmp_dir / "venue_alias_review.csv"

    review_path.write_text(
        "\n".join(
            [
                "id,venue_type,raw_name,normalized_name,proposed_canonical_id,proposed_canonical_title,score,match_type,evidence_json,status,reviewed_by,reviewed_at",
                '1,journal,Intelligent Tutoring Media,intelligent tutoring media,13242,Digital Creativity,0.95,dblp-local-csv-registry,"{""source"": ""review""}",pending,,',
                '2,journal,JCP,jcp,7928,Journal of Cancer Policy,0.90,derived-acronym,"{""blocked_reason"": ""exact-series-collision"", ""source"": ""review""}",pending,,',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    updated = sync_review_statuses_from_resolved_rows(
        review_path,
        [
            {
                "venue_type": "journal",
                "raw_name": "Intelligent Tutoring Media",
                "normalized_name": "intelligent tutoring media",
                "proposed_canonical_id": "13242",
                "proposed_canonical_title": "Digital Creativity",
                "reviewed_by": "manual-reviewed-journal-catalog-batch-1",
                "reviewed_at": "2026-03-31",
            }
        ],
        reviewed_by="mapping-sync",
        reviewed_at="2026-03-31",
    )

    assert updated == 1

    with review_path.open("r", encoding="utf-8", newline="") as handle:
        rows = {
            (row["venue_type"], row["raw_name"]): row
            for row in csv.DictReader(handle)
        }

    itm_row = rows[("journal", "Intelligent Tutoring Media")]
    assert itm_row["status"] == "approved"
    assert itm_row["reviewed_by"] == "manual-reviewed-journal-catalog-batch-1"
    assert itm_row["reviewed_at"] == "2026-03-31"

    jcp_row = rows[("journal", "JCP")]
    assert jcp_row["status"] == "pending"
    assert jcp_row["reviewed_by"] == ""
    assert jcp_row["reviewed_at"] == ""


@pytest.mark.parametrize("query", ["ICNSC", "ICUMT", "ISVLSI", "CLEF"])
def test_absent_target_series_stay_unmatched_until_seeded(query):
    matcher = VenueMatcher(
        venue_type="conference",
        records=[],
    )

    result = matcher.match(query)

    assert result.status == "unmatched"
    assert result.venue_id is None

def test_split_review_rows_by_action_separates_conference_and_journal_outputs():
    conference_rows = [
        {
            "booktitle": "ISGT Europe",
            "bucket": "A",
            "approval_ready": "yes",
            "candidate_1_conf_id": "2001",
        },
        {
            "booktitle": "EGC",
            "bucket": "C",
            "approval_ready": "no",
            "candidate_1_conf_id": "",
        },
        {
            "booktitle": "ICEC",
            "bucket": "B",
            "approval_ready": "no",
            "candidate_1_conf_id": "325",
        },
    ]
    journal_rows = [
        {
            "dblp_journal_name": "Software - Concepts and Tools",
            "approval_ready": "yes",
            "suggested_action": "approve alias",
        },
        {
            "dblp_journal_name": "ACM Crossroads",
            "approval_ready": "no",
            "suggested_action": "intentional null / likely not rankable",
        },
    ]

    conference_split = split_conference_review_rows_by_action(conference_rows)
    journal_split = split_journal_review_rows_by_action(journal_rows)

    assert [row["booktitle"] for row in conference_split["approve"]] == ["ISGT Europe"]
    assert [row["booktitle"] for row in conference_split["lookup_enrichment"]] == ["EGC"]
    assert [row["booktitle"] for row in conference_split["collision_review_only"]] == ["ICEC"]
    assert [row["dblp_journal_name"] for row in journal_split["approve"]] == [
        "Software - Concepts and Tools"
    ]
    assert [row["dblp_journal_name"] for row in journal_split["intentional_null"]] == [
        "ACM Crossroads"
    ]


def test_build_intentional_null_audit_sample_selects_high_mid_and_random_rows_without_duplicates():
    candidates = [
        {
            "raw_journal_name": f"Journal {index:02d}",
            "rows": 100 - index,
            "candidate_title": "",
            "score": "0.00",
            "current_reason": "likely non-rankable publication family",
        }
        for index in range(40)
    ]

    sample = build_intentional_null_audit_sample(candidates, random_seed=7)

    assert len(sample) == 40
    assert len({row["raw_journal_name"] for row in sample}) == 40
    assert all("audit_verdict" in row and "notes" in row for row in sample)


def test_explicit_non_rankable_journal_titles_flow_to_intentional_null():
    result = MatchResult(
        "non_venue",
        None,
        "manual-non-venue",
        1.0,
        "non_venue",
        "manual",
        (),
    )

    blocked_reason = build_blocked_reason(
        raw_name="Computerworld",
        venue_type="journal",
        result=result,
        score_gap=0.0,
    )

    assert blocked_reason == "explicitly marked non-rankable"
    assert classify_journal_suggested_action("Computerworld", result) == (
        "intentional null / likely not rankable"
    )


def test_transactions_of_sdps_stays_review_only_during_intentional_null_cleanup():
    result = MatchResult(
        "unmatched",
        None,
        "unmatched",
        0.0,
        "unmatched",
        "none",
        (),
    )

    blocked_reason = build_blocked_reason(
        raw_name="Transactions of the SDPS",
        venue_type="journal",
        result=result,
        score_gap=0.0,
    )

    assert blocked_reason == "no conservative candidate found"
    assert classify_journal_suggested_action("Transactions of the SDPS", result) == (
        "review manually"
    )
