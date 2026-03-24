import importlib
from pathlib import Path
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
        if "SELECT conf_id, title, acronym, rank FROM conferences" in compact_query:
            return [{"conf_id": 1, "title": "Conf A", "acronym": "CA", "rank": "A"}]
        if "MATCH(title, acronym)" in compact_query or "acronym LIKE" in compact_query:
            return [{"conf_id": 1, "title": "Conf A", "acronym": "CA"}]
        if "SELECT COUNT(*) as total FROM journals" in compact_query:
            return {"total": 1}
        if "SELECT journal_id, title, publisher, best_quartile, sjr_index FROM journals" in compact_query:
            return [
                {
                    "journal_id": 1,
                    "title": "Journal A",
                    "publisher": "Publisher A",
                    "best_quartile": "Q1",
                    "sjr_index": 1.23,
                }
            ]
        if "MATCH(title) AGAINST" in compact_query or "FROM journals WHERE title LIKE" in compact_query:
            return [{"journal_id": 1, "title": "Journal A"}]
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
    assert isinstance(payload, list)


def test_conference_search_with_only_special_characters_returns_empty_list(client):
    response = client.get("/api/conference/search?q=++&&")

    assert response.status_code == 200
    assert response.get_json() == []


def test_author_search_with_special_characters_does_not_crash(client):
    response = client.get("/api/author/search?q=Ada++&&")
    payload = response.get_json()

    assert response.status_code == 200
    assert isinstance(payload, list)


def test_missing_journal_profile_returns_404(client):
    response = client.get("/api/journal/9999/profile")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Not found"


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
