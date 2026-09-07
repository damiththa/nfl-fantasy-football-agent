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


def test_query_lineup_invalid_league(client):
    response = client.post("/query/lineup?league_id=99999999")
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
