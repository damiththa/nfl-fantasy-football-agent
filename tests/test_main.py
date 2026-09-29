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
    assert data["model"] in ("gemini-2.5-pro", "gemini-3.1-pro", "gemini-3.1-pro-preview")
    assert data["target_model"] == "gemini-3.1-pro-preview"
    assert data["auto_upgrade_enabled"] is True
    assert "gemini_status" in data
    assert len(data["leagues"]) == 2


def test_health_models(client):
    response = client.get("/health/models")
    assert response.status_code == 200
    data = response.json()
    assert "active_model" in data
    assert data["target_model"] == "gemini-3.1-pro-preview"
    assert data["auto_upgrade_enabled"] is True
    assert "candidates" in data


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


def test_root_dashboard_trade_horizon_and_conviction_markup(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "COACH'S CONVICTION" in response.text
    assert "LONG-TERM / ROS DECISION" in response.text
    assert "WEEKLY / MATCHUP PURPOSE" in response.text


def test_run_weekly_tuesday_recap_no_attribute_error(client):
    """Verify Tuesday weekly run processes MatchupData without 'your_lineup' AttributeError."""
    import zoneinfo
    from datetime import datetime
    from unittest.mock import MagicMock, patch

    from src.espn.matchup import MatchupData
    from src.espn.roster import ParsedRoster, RosterPlayer

    p1 = RosterPlayer("Player 1", "RB", "KC", "RB", 15.0, 0.0, "NORMAL", 10, 90.0)
    p1_played = RosterPlayer(
        "Player 1", "RB", "KC", "RB", 15.0, 18.2, "NORMAL", 10, 90.0, has_played=True
    )

    unplayed_roster = ParsedRoster(team_name="Mad Dawg", players=[p1], starters=[p1], bench=[])
    played_roster = ParsedRoster(
        team_name="Mad Dawg", players=[p1_played], starters=[p1_played], bench=[]
    )

    matchup_wk2_unplayed = MatchupData(
        week=2,
        your_team=unplayed_roster,
        opponent_team=None,
        your_projected=100.0,
        opp_projected=100.0,
        projected_margin=0.0,
        is_favorite=False,
        your_score=0.0,
        opp_score=0.0,
    )
    matchup_wk1_played = MatchupData(
        week=1,
        your_team=played_roster,
        opponent_team=None,
        your_projected=100.0,
        opp_projected=90.0,
        projected_margin=10.0,
        is_favorite=True,
        your_score=18.2,
        opp_score=10.0,
    )

    mock_team = MagicMock()
    mock_team.team_id = 991059191
    mock_team.team_name = "Mad Dawg"
    mock_team.roster = []

    mock_espn = MagicMock()
    mock_espn.teams = [mock_team]
    mock_espn.current_week = 2
    mock_espn.free_agents.return_value = []

    def mock_get_weekly_matchup(espn, team_id, week, league_config):
        if week == 2:
            return matchup_wk2_unplayed
        elif week == 1:
            return matchup_wk1_played
        return None

    # Tuesday datetime (weekday == 1)
    fake_tuesday = datetime(2026, 9, 29, 7, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York"))

    with patch("src.main.LeagueClient") as mock_lc, \
         patch("src.main.get_current_week", return_value=2), \
         patch("src.main.get_weekly_matchup", side_effect=mock_get_weekly_matchup), \
         patch("src.main.parse_roster", return_value=unplayed_roster), \
         patch("src.main.fetch_trending_adds", return_value=[]), \
         patch("src.main.evaluate_waivers") as mock_waivers, \
         patch("src.main.generate_weekly_recap") as mock_recap, \
         patch("src.main.send_digest_email") as mock_send_email:

        mock_lc.return_value.get_league.return_value = mock_espn

        recap_report = MagicMock()
        recap_report.model_dump.return_value = {"recap": "success"}
        mock_recap.return_value = recap_report

        waiver_report = MagicMock()
        waiver_report.model_dump.return_value = {"waivers": "success"}
        mock_waivers.return_value = waiver_report

        # Patch datetime in src.main
        with patch("src.main.datetime") as mock_dt:
            mock_dt.now.return_value = fake_tuesday
            mock_dt.strftime = datetime.strftime

            response = client.post("/run/weekly")
            assert response.status_code == 200
            data = response.json()
            assert data["job"] == "weekly_analysis"
            assert data["day"] == "Tuesday"
            assert mock_send_email.called
            # Results must NOT contain any 'your_lineup' error
            for k, v in data["results"].items():
                if isinstance(v, dict) and "error" in v:
                    assert "your_lineup" not in v["error"]



