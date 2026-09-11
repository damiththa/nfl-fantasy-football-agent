import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["season"] == 2026
    assert data["model"] == "gemini-2.5-pro"
    assert len(data["leagues"]) == 2


def test_root_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Mad Dawg" in response.text
    assert "PNA 2026" in response.text
    assert "Chips Ahoy" in response.text
    assert 'rel="icon"' in response.text
    assert "Waiver Wire Intel" in response.text


def test_favicon(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert "🏈" in response.text


def test_query_lineup_invalid_league(client):
    response = client.post("/query/lineup?league_id=99999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_query_start_sit_invalid_league(client):
    response = client.post("/query/start-sit?league_id=99999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_query_trade_invalid_league(client):
    response = client.post(
        "/query/trade",
        json={
            "league_id": 99999999,
            "giving_players": ["Player A"],
            "receiving_players": ["Player B"],
        },
    )
    assert response.status_code == 404


def test_query_propose_trades_invalid_league(client):
    response = client.post("/query/propose-trades?league_id=99999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_query_roster_players_invalid_league(client):
    response = client.get("/query/roster-players?league_id=99999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_query_waivers_invalid_league(client):
    response = client.post("/query/waivers?league_id=99999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_query_weekly_recap_invalid_league(client):
    response = client.post("/query/weekly-recap?league_id=99999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


