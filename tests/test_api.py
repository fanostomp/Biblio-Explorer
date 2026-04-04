import importlib
from pathlib import Path
import re
import sys

import pytest


class DummyConnection:
    def close(self):
        return None

    def ping(self, reconnect=True):
        return None


@pytest.fixture()
def client(monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    backend_path = str(backend_dir)
    if backend_path in sys.path:
        sys.path.remove(backend_path)
    sys.path.insert(0, backend_path)

    for module_name in (
        "config",
        "db",
        "extensions",
        "app",
        "routes.conferences",
        "routes.journals",
        "routes.authors",
        "routes.years",
        "routes.charts",
    ):
        sys.modules.pop(module_name, None)

    backend_app = importlib.import_module("app")
    conferences = importlib.import_module("routes.conferences")
    journals = importlib.import_module("routes.journals")
    authors = importlib.import_module("routes.authors")
    years = importlib.import_module("routes.years")
    charts = importlib.import_module("routes.charts")

    def fake_init_pool(config, pool_name="mypool", pool_size=5):
        return None

    def fake_get_db_connection():
        return DummyConnection()

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())

        if "COUNT(*) as total FROM conferences" in compact_query:
            return {"total": 1}
        if "SELECT conf_id, title, acronym, `rank` FROM conferences" in compact_query:
            return [{"conf_id": 1, "title": "Conf A", "acronym": "CA", "rank": "A"}]
        if "MATCH(title, acronym)" in compact_query or "acronym LIKE" in compact_query:
            return [{"conf_id": 1, "title": "Conf A", "acronym": "CA"}]
        if "SELECT COUNT(*) as total FROM journals" in compact_query:
            return {"total": 1}
        if "SELECT journal_id, title, publisher, best_quartile, sjr_index FROM journals ORDER BY sjr_index DESC" in compact_query:
            return [
                {
                    "journal_id": 1,
                    "title": "Journal A",
                    "publisher": "Publisher A",
                    "best_quartile": "Q1",
                    "sjr_index": 1.23,
                }
            ]
        if (
            "SELECT j.journal_id, j.title, j.publisher, j.best_quartile, j.sjr_index" in compact_query
            or "MATCH(j.title) AGAINST" in compact_query
            or "FROM journals j WHERE j.title LIKE" in compact_query
        ):
            return [
                {
                    "journal_id": 1,
                    "title": "Journal A",
                    "publisher": "Publisher A",
                    "best_quartile": "Q1",
                    "sjr_index": 1.23,
                    "has_dblp_coverage": True,
                }
            ]
        if "FROM authors WHERE name LIKE" in compact_query or "MATCH(name) AGAINST" in compact_query:
            return [{"author_id": 1, "name": "Ada Lovelace"}]
        if "SELECT year, COUNT(*) AS num_papers FROM papers" in compact_query:
            return [{"year": 2024, "num_papers": 10}]
        if "FROM vw_journal_profile" in compact_query:
            return None if fetchone else []
        if "FROM vw_conf_profile" in compact_query:
            return None if fetchone else []
        if "FROM vw_year_profile" in compact_query:
            return None if fetchone else []
        if "FROM vw_author_profile" in compact_query:
            return None if fetchone else []

        return None if fetchone else []

    monkeypatch.setattr(backend_app, "init_pool", fake_init_pool)
    monkeypatch.setattr(backend_app, "get_db_connection", fake_get_db_connection)

    for module in (conferences, journals, authors, years, charts):
        monkeypatch.setattr(module, "get_db_connection", fake_get_db_connection)
        monkeypatch.setattr(module, "execute_query", fake_execute_query)

    app = backend_app.create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health_endpoint_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "db": "connected"}
    assert response.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


def test_conference_list_endpoint_returns_json_shape(client):
    response = client.get("/api/conference/?page=1&per_page=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert "conferences" in payload
    assert "pagination" in payload


def test_journal_search_with_special_characters_does_not_crash(client):
    response = client.get("/api/journal/search?q=AI++&&")
    payload = response.get_json()

    assert response.status_code == 200
    assert "results" in payload
    assert "pagination" in payload


def test_journal_search_returns_dblp_coverage_flag(client, monkeypatch):
    import routes.journals as journals

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())

        if compact_query.startswith("SELECT COUNT(*) as total FROM journals j"):
            return {"total": 2}

        if "has_dblp_coverage" in compact_query and "EXISTS (SELECT 1 FROM papers p WHERE p.journal_id = j.journal_id)" in compact_query:
            return [
                {
                    "journal_id": 1,
                    "title": "Covered Journal",
                    "publisher": "DBLP Press",
                    "best_quartile": "Q1",
                    "sjr_index": 3.21,
                    "has_dblp_coverage": True,
                },
                {
                    "journal_id": 2,
                    "title": "Ranked Only Journal",
                    "publisher": "Metadata Press",
                    "best_quartile": "Q2",
                    "sjr_index": 2.1,
                    "has_dblp_coverage": False,
                },
            ]

        return None if fetchone else []

    monkeypatch.setattr(journals, "execute_query", fake_execute_query)

    response = client.get("/api/journal/search?q=journal")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["results"][0]["has_dblp_coverage"] is True
    assert payload["results"][1]["has_dblp_coverage"] is False


def test_journal_search_can_filter_to_dblp_coverage_only(client, monkeypatch):
    import routes.journals as journals

    seen_queries = []

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())
        seen_queries.append(compact_query)

        if compact_query.startswith("SELECT COUNT(*) as total FROM journals j"):
            return {"total": 1}

        if "has_dblp_coverage" in compact_query and "EXISTS (SELECT 1 FROM papers p WHERE p.journal_id = j.journal_id)" in compact_query:
            return [
                {
                    "journal_id": 1,
                    "title": "Covered Journal",
                    "publisher": "DBLP Press",
                    "best_quartile": "Q1",
                    "sjr_index": 3.21,
                    "has_dblp_coverage": True,
                }
            ]

        return None if fetchone else []

    monkeypatch.setattr(journals, "execute_query", fake_execute_query)

    response = client.get("/api/journal/search?with_dblp_coverage=true")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["results"] == [
        {
            "journal_id": 1,
            "title": "Covered Journal",
            "publisher": "DBLP Press",
            "best_quartile": "Q1",
            "sjr_index": 3.21,
            "has_dblp_coverage": True,
        }
    ]
    assert any("EXISTS (SELECT 1 FROM papers p WHERE p.journal_id = j.journal_id)" in query for query in seen_queries)
    assert not any("paper_coverage" in query for query in seen_queries)


def test_journal_profile_includes_dblp_coverage_flag(client, monkeypatch):
    import routes.journals as journals

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())

        if "FROM vw_journal_profile" in compact_query:
            if params == (1,):
                return {
                    "journal_id": 1,
                    "title": "Journal With Coverage",
                    "total_papers": 12,
                    "distinct_authors": 8,
                }
            if params == (2,):
                return {
                    "journal_id": 2,
                    "title": "Journal Without Coverage",
                    "total_papers": 0,
                    "distinct_authors": 0,
                }
            return None

        if "FROM vw_journal_yearly_stats" in compact_query:
            if params == (1,):
                return [{"journal_id": 1, "year": 2024, "paper_count": 12, "distinct_authors": 8}]
            if params == (2,):
                return []

        return None if fetchone else []

    monkeypatch.setattr(journals, "execute_query", fake_execute_query)

    covered_response = client.get("/api/journal/1/profile")
    uncovered_response = client.get("/api/journal/2/profile")

    assert covered_response.status_code == 200
    assert covered_response.get_json()["has_dblp_coverage"] is True
    assert uncovered_response.status_code == 200
    assert uncovered_response.get_json()["has_dblp_coverage"] is False


def test_conference_search_with_only_special_characters_returns_empty_list(client):
    response = client.get("/api/conference/search?q=++&&")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["results"] == []
    assert "pagination" in payload


def test_conference_search_returns_dblp_coverage_flag(client, monkeypatch):
    import routes.conferences as conferences

    seen_queries = []

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())
        seen_queries.append(compact_query)

        if compact_query.startswith("SELECT COUNT(*) as total FROM conferences c"):
            return {"total": 2}

        if "has_dblp_coverage" in compact_query and "EXISTS (SELECT 1 FROM papers p WHERE p.conf_id = c.conf_id)" in compact_query:
            return [
                {
                    "conf_id": 1,
                    "title": "Covered Conference",
                    "acronym": "CC",
                    "rank": "A*",
                    "primary_for": "4602",
                    "has_dblp_coverage": True,
                },
                {
                    "conf_id": 2,
                    "title": "Ranked Only Conference",
                    "acronym": "ROC",
                    "rank": "A",
                    "primary_for": "4603",
                    "has_dblp_coverage": False,
                },
            ]

        return None if fetchone else []

    monkeypatch.setattr(conferences, "execute_query", fake_execute_query)

    response = client.get("/api/conference/search?q=conference")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["results"][0]["has_dblp_coverage"] is True
    assert payload["results"][1]["has_dblp_coverage"] is False
    assert any("EXISTS (SELECT 1 FROM papers p WHERE p.conf_id = c.conf_id) AS has_dblp_coverage" in query for query in seen_queries)
    assert not any("LEFT JOIN (SELECT DISTINCT conf_id FROM papers) pc ON pc.conf_id = c.conf_id" in query for query in seen_queries)


def test_conference_search_can_filter_to_dblp_coverage_only(client, monkeypatch):
    import routes.conferences as conferences

    seen_queries = []

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())
        seen_queries.append(compact_query)

        if compact_query.startswith("SELECT COUNT(*) as total FROM conferences c"):
            return {"total": 1}

        if "has_dblp_coverage" in compact_query and "EXISTS (SELECT 1 FROM papers p WHERE p.conf_id = c.conf_id)" in compact_query:
            return [
                {
                    "conf_id": 1,
                    "title": "Covered Conference",
                    "acronym": "CC",
                    "rank": "A*",
                    "primary_for": "4602",
                    "has_dblp_coverage": True,
                }
            ]

        return None if fetchone else []

    monkeypatch.setattr(conferences, "execute_query", fake_execute_query)

    response = client.get("/api/conference/search?with_dblp_coverage=true")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["results"] == [
        {
            "conf_id": 1,
            "title": "Covered Conference",
            "acronym": "CC",
            "rank": "A*",
            "primary_for": "4602",
            "has_dblp_coverage": True,
        }
    ]
    assert any("EXISTS (SELECT 1 FROM papers p WHERE p.conf_id = c.conf_id)" in query for query in seen_queries)
    assert not any("paper_coverage" in query for query in seen_queries)


def test_conference_search_accepts_standard_truthy_values_for_dblp_filter(client, monkeypatch):
    import routes.conferences as conferences

    seen_queries = []

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())
        seen_queries.append(compact_query)
        return {"total": 0} if fetchone else []

    monkeypatch.setattr(conferences, "execute_query", fake_execute_query)

    response = client.get("/api/conference/search?with_dblp_coverage=1")

    assert response.status_code == 200
    assert any("EXISTS (SELECT 1 FROM papers p WHERE p.conf_id = c.conf_id)" in query for query in seen_queries)


def test_author_search_with_special_characters_does_not_crash(client):
    response = client.get("/api/author/search?q=Ada++&&")
    payload = response.get_json()

    assert response.status_code == 200
    assert isinstance(payload, list)


def test_missing_journal_profile_returns_404(client):
    response = client.get("/api/journal/9999/profile")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Not found"


def test_journal_top_authors_endpoint_returns_expected_shape(client, monkeypatch):
    import routes.journals as journals

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())

        if "FROM authors a JOIN paper_authors pa ON a.author_id = pa.author_id JOIN papers p ON pa.paper_id = p.paper_id WHERE p.journal_id = %s GROUP BY a.author_id, a.name ORDER BY paper_count DESC LIMIT %s" in compact_query:
            assert params == (7, 10)
            return [
                {"author_id": 3, "name": "Ada Lovelace", "paper_count": 4},
                {"author_id": 4, "name": "Grace Hopper", "paper_count": 2},
            ]

        return None if fetchone else []

    monkeypatch.setattr(journals, "execute_query", fake_execute_query)

    response = client.get("/api/journal/7/top_authors?limit=10")

    assert response.status_code == 200
    assert response.get_json() == [
        {"author_id": 3, "name": "Ada Lovelace", "paper_count": 4},
        {"author_id": 4, "name": "Grace Hopper", "paper_count": 2},
    ]


def test_journal_top_authors_endpoint_bounds_limit(client, monkeypatch):
    import routes.journals as journals
    seen_params = []

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())

        if "FROM authors a JOIN paper_authors pa ON a.author_id = pa.author_id JOIN papers p ON pa.paper_id = p.paper_id WHERE p.journal_id = %s GROUP BY a.author_id, a.name ORDER BY paper_count DESC LIMIT %s" in compact_query:
            seen_params.append(params)
            return []

        return None if fetchone else []

    monkeypatch.setattr(journals, "execute_query", fake_execute_query)

    response = client.get("/api/journal/9/top_authors?limit=1000")

    assert response.status_code == 200
    assert response.get_json() == []
    assert seen_params == [(9, 100)]


def test_journal_top_authors_endpoint_hides_internal_errors(client, monkeypatch):
    import routes.journals as journals

    def fake_execute_query(conn, query, params=(), fetchone=False):
        raise RuntimeError("sensitive schema details")

    monkeypatch.setattr(journals, "execute_query", fake_execute_query)

    response = client.get("/api/journal/7/top_authors")

    assert response.status_code == 500
    assert response.get_json() == {"error": "Internal server error"}


def test_journal_page_includes_coverage_ui_and_top_authors_section(client):
    response = client.get("/journal")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="journal-title-search"' in html
    assert 'class="autocomplete-list journal-title-dropdown"' in html
    assert 'class="search-section full-width journal-search-section"' in html
    assert 'class="search-results-container journal-search-results"' in html
    assert 'class="interactive-table journal-results-table"' in html
    assert 'id="filterCoverageOnly"' in html
    assert "Only journals with DBLP stats" in html
    assert 'id="journalCoverageBadge"' in html
    assert 'id="topAuthorsTable"' in html
    assert 'id="journalNoCoverage"' in html
    assert "Ranking metadata is still available for this journal" in html
    assert "charts, article tables, and top-author stats require DBLP-linked papers" in html


def test_conference_page_includes_live_search_and_coverage_filter_ui(client):
    response = client.get("/conference")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="search-section full-width conference-search-section"' in html
    assert 'class="conference-title-search"' in html
    assert 'class="autocomplete-list conference-title-dropdown"' in html
    assert 'id="filterCoverageOnly"' in html
    assert "Only conferences with DBLP stats" in html
    assert 'class="search-results-container conference-search-results"' in html
    assert 'class="interactive-table conference-results-table"' in html


def test_journal_page_script_wires_live_search_for_both_text_inputs():
    script_path = Path(__file__).resolve().parents[1] / "frontend" / "static" / "js" / "app.js"
    script = script_path.read_text(encoding="utf-8")

    assert "setupLiveSearch" in script
    assert "setupLiveSearch(input, handleSearch);" in script
    assert "setupLiveSearch(pubInput, handleSearch);" in script


def test_conference_page_script_wires_live_search_and_coverage_filter():
    script_path = Path(__file__).resolve().parents[1] / "frontend" / "static" / "js" / "app.js"
    script = script_path.read_text(encoding="utf-8")

    assert "setupLiveSearch" in script
    assert "setupLiveSearch(input, handleSearch);" in script
    assert "if (coverageOnly) coverageOnly.addEventListener('change', handleSearch);" in script
    assert "if (isCoverageFilterEnabled()) url += '&with_dblp_coverage=true';" in script
    assert "β€”" not in script


def test_search_filter_css_uses_border_box_for_search_bar_layout():
    css_path = Path(__file__).resolve().parents[1] / "frontend" / "static" / "css" / "style.css"
    css = css_path.read_text(encoding="utf-8")

    search_input_block = re.search(r"\.search-section input \{([^}]*)\}", css, re.DOTALL)
    filter_bar_block = re.search(r"\.filter-bar input, \.filter-bar select \{([^}]*)\}", css, re.DOTALL)

    assert search_input_block is not None
    assert filter_bar_block is not None
    assert "box-sizing: border-box;" in search_input_block.group(1)
    assert "box-sizing: border-box;" in filter_bar_block.group(1)


def test_missing_year_profile_returns_404(client):
    response = client.get("/api/year/9999/profile")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Not found"


def test_charts_overview_returns_json_shape(client):
    response = client.get("/api/charts/overview")
    payload = response.get_json()

    assert response.status_code == 200
    assert "yearly_totals" in payload


def test_stats_overview_endpoint_returns_exact_counts(client, monkeypatch):
    import routes.charts as charts

    queries = []

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())
        queries.append(compact_query)

        results = {
            "SELECT COUNT(*) AS total_papers FROM papers": {"total_papers": 321},
            "SELECT COUNT(*) AS total_authors FROM authors": {"total_authors": 654},
            "SELECT COUNT(*) AS total_conferences FROM conferences": {"total_conferences": 12},
            "SELECT COUNT(*) AS total_journals FROM journals": {"total_journals": 34},
        }
        return results[compact_query]

    monkeypatch.setattr(charts, "execute_query", fake_execute_query)

    response = client.get("/api/stats/overview")

    assert response.status_code == 200
    assert response.get_json() == {
        "total_papers": 321,
        "total_authors": 654,
        "total_conferences": 12,
        "total_journals": 34,
    }
    assert queries == [
        "SELECT COUNT(*) AS total_papers FROM papers",
        "SELECT COUNT(*) AS total_authors FROM authors",
        "SELECT COUNT(*) AS total_conferences FROM conferences",
        "SELECT COUNT(*) AS total_journals FROM journals",
    ]


def test_year_papers_endpoint_returns_pagination_and_filters(client, monkeypatch):
    import routes.years as years

    calls = []

    def fake_execute_query(conn, query, params=(), fetchone=False):
        compact_query = " ".join(query.split())
        calls.append((compact_query, params))

        if compact_query.startswith("SELECT COUNT(DISTINCT p.paper_id) AS total FROM papers p"):
            return {"total": 3}

        if compact_query.startswith("SELECT DISTINCT p.paper_id, p.title, p.type, IFNULL(c.acronym, j.title) AS venue_name FROM papers p"):
            return [{"paper_id": 99, "title": "Filtered Paper", "type": "conference", "venue_name": "ICSE"}]

        return None if fetchone else []

    monkeypatch.setattr(years, "execute_query", fake_execute_query)

    response = client.get("/api/year/2024/papers?page=2&per_page=1&conf_id=7&journal_id=11&author_id=5")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["papers"] == [
        {"paper_id": 99, "title": "Filtered Paper", "type": "conference", "venue_name": "ICSE"}
    ]
    assert payload["pagination"] == {
        "page": 2,
        "per_page": 1,
        "total_records": 3,
        "total_pages": 3,
    }

    count_query, count_params = calls[0]
    data_query, data_params = calls[1]

    assert "JOIN paper_authors pa ON pa.paper_id = p.paper_id" in count_query
    assert "p.conf_id = %s" in count_query
    assert "p.journal_id = %s" in count_query
    assert "pa.author_id = %s" in count_query
    assert count_params == (2024, 7, 11, 5)
    assert data_params == (2024, 7, 11, 5, 1, 1)
