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
