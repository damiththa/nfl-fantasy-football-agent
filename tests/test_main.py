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


@pytest.mark.anyio
async def test_stream_with_heartbeat_success():
    import json

    from src.main import stream_with_heartbeat

    def mock_calc():
        return {"status": "ok", "starters": ["Josh Allen"]}

    chunks = []
    async for chunk in stream_with_heartbeat(mock_calc, heartbeat_interval=0.05):
        chunks.append(chunk)

    assert chunks[0] == b" "
    full_body = b"".join(chunks).decode("utf-8")
    data = json.loads(full_body)
    assert data["status"] == "ok"
    assert data["starters"] == ["Josh Allen"]


@pytest.mark.anyio
async def test_stream_with_heartbeat_error_handling():
    import json

    from src.main import stream_with_heartbeat

    def mock_failing_calc():
        raise RuntimeError("Simulated upstream error")

    chunks = []
    async for chunk in stream_with_heartbeat(mock_failing_calc, heartbeat_interval=0.05):
        chunks.append(chunk)

    assert chunks[0] == b" "
    full_body = b"".join(chunks).decode("utf-8")
    data = json.loads(full_body)
    assert "error" in data
    assert "Simulated upstream error" in data["error"]


def test_root_dashboard_has_season_lessons_and_recap_selector(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Season Tape &amp; Lessons Summary" in response.text or "Season Tape & Lessons Summary" in response.text
    assert "recap-week-991059191" in response.text
    assert "recap-week-735288" in response.text
    assert "loadPastRecap" in response.text
    assert "Season Coaching Tape &amp; Strategic Memory" in response.text or "Season Coaching Tape" in response.text


def test_query_lessons_summary_endpoint(client):
    response = client.get("/query/lessons-summary?league_id=991059191")
    assert response.status_code == 200
    data = response.json()
    assert "season" in data
    assert "league_id" in data
    assert data["league_id"] == 991059191
    assert "record" in data
    assert "all_lessons" in data
    assert "league_name" in data


def test_weekly_analysis_triggers_espn_auth_alert_on_401(client, monkeypatch):
    from unittest.mock import MagicMock

    mock_alert = MagicMock()
    monkeypatch.setattr("src.main.send_error_alert_email", mock_alert)
    monkeypatch.setattr("src.main.send_digest_email", MagicMock())

    class FailingLeagueClient:
        def get_league(self, cfg):
            raise ConnectionError("ESPN API Authentication failed (401). Your ESPN_S2 or SWID cookies may be expired.")

    monkeypatch.setattr("src.main.LeagueClient", FailingLeagueClient)

    response = client.post("/run/weekly")
    assert response.status_code == 200

    # Ensure espn_auth alert was dispatched
    auth_alert_calls = [c for c in mock_alert.call_args_list if c[0][0] == "espn_auth"]
    assert len(auth_alert_calls) >= 1
    assert "cookies" in auth_alert_calls[0][0][1].lower()


def test_fetch_league_free_agents_augments_kickers_and_defenses():
    from unittest.mock import MagicMock
    from src.main import fetch_league_free_agents

    mock_league = MagicMock()
    kicker = MagicMock(name="Kicker", position="K", proTeam="SF", projected_points=8.0, percent_owned=30.0, injuryStatus="ACTIVE", injured=False)
    kicker.name = "Test Kicker"
    defense = MagicMock(name="Defense", position="D/ST", proTeam="DEN", projected_points=7.0, percent_owned=40.0, injuryStatus="NORMAL", injured=False)
    defense.name = "Test Defense"
    skill_p = MagicMock(name="Skill", position="WR", proTeam="KC", projected_points=12.0, percent_owned=70.0, injuryStatus="ACTIVE", injured=False)
    skill_p.name = "Test Skill"

    def mock_fa(week=None, size=50, position=None):
        if position == "K":
            return [kicker]
        if position == "D/ST":
            return [defense]
        return [skill_p]

    mock_league.free_agents.side_effect = mock_fa

    fa_list = fetch_league_free_agents(mock_league, week=5)
    names = [f["name"] for f in fa_list]
    assert "Test Skill" in names
    assert "Test Kicker" in names
    assert "Test Defense" in names


def test_query_roster_players_no_cache_and_load_roster_week(client, monkeypatch):
    from unittest.mock import MagicMock

    mock_league = MagicMock()
    mock_league.current_week = 6
    mock_team = MagicMock()
    mock_team.team_id = 3
    mock_team.team_name = "Mad Dawg"
    mock_player = MagicMock()
    mock_player.name = "Breece Hall"
    mock_player.position = "RB"
    mock_player.proTeam = "NYJ"
    mock_player.projected_points = 14.5
    mock_player.points = 0.0
    mock_player.game_played = 0
    mock_player.injuryStatus = "ACTIVE"
    mock_player.lineupLocked = False
    mock_player.lineupSlot = "RB"
    mock_player.stats = {6: {"projected_points": 14.5, "points": 0.0}}
    mock_team.roster = [mock_player]
    mock_league.teams = [mock_team]

    class MockLeagueClient:
        def get_league(self, cfg):
            return mock_league

    monkeypatch.setattr("src.main.LeagueClient", MockLeagueClient)

    response = client.get("/query/roster-players?league_id=991059191")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"

    mock_league.load_roster_week.assert_called_once_with(6)
    data = response.json()
    assert data["league_id"] == 991059191
    assert data["team_name"] == "Mad Dawg"
    assert len(data["players"]) == 1
    assert data["players"][0]["name"] == "Breece Hall"


def test_query_trade_calls_load_roster_week(client, monkeypatch):
    from unittest.mock import MagicMock
    from src.intelligence.schemas import TradeEvaluation

    mock_league = MagicMock()
    mock_league.current_week = 6
    mock_team = MagicMock()
    mock_team.team_id = 3
    mock_team.team_name = "Mad Dawg"
    mock_team.roster = []
    mock_league.teams = [mock_team]

    class MockLeagueClient:
        def get_league(self, cfg):
            return mock_league

    monkeypatch.setattr("src.main.LeagueClient", MockLeagueClient)

    dummy_verdict = TradeEvaluation(
        verdict="ACCEPT",
        is_valid_trade=True,
        roster_validation_errors=[],
        pre_trade_starting_points=115.0,
        post_trade_starting_points=117.5,
        net_starting_points_change=2.5,
        starting_lineup_changes=[],
        positional_depth_impact="Solid",
        your_vorp_change=2.5,
        starting_lineup_impact="+2.5 pts",
        time_horizon="LONG_TERM_DECISION",
        time_horizon_detail="Full season",
        coach_conviction="Strong upgrade at starting running back.",
        playoff_schedule_impact="Favorable",
        reasoning="Improves weekly starter floor.",
        generated_at="2026-10-08T12:00:00Z",
        intelligence_backend="Gemini Pro",
    )
    monkeypatch.setattr("src.main.evaluate_trade", MagicMock(return_value=dummy_verdict))

    response = client.post(
        "/query/trade",
        json={
            "league_id": 991059191,
            "giving_players": ["Player A"],
            "receiving_players": ["Player B"],
        },
    )
    assert response.status_code == 200
    mock_league.load_roster_week.assert_called_once_with(6)
    data = response.json()
    assert data["verdict"] == "ACCEPT"


def test_dashboard_contains_refresh_roster_and_ticker(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "🔄 Refresh Roster" in response.text
    assert "cache: 'no-store'" in response.text
    assert "_t=" in response.text
    assert "loadTradeRoster(true)" in response.text



