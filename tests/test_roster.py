from unittest.mock import MagicMock

from src.config import PNA_2026
from src.espn.roster import parse_roster


def test_parse_roster_player_has_played():
    mock_player = MagicMock()
    mock_player.name = "Brock Purdy"
    mock_player.position = "QB"
    mock_player.proTeam = "SF"
    mock_player.lineupSlot = "QB"
    mock_player.points = None
    mock_player.projected_points = None
    mock_player.injuryStatus = "NORMAL"
    mock_player.bye_week = 9
    mock_player.percent_owned = 95.0
    mock_player.stats = {
        1: {
            "points": 21.1,
            "projected_points": 15.48,
        }
    }

    mock_team = MagicMock()
    mock_team.team_name = "Mad Dawg"
    mock_team.roster = [mock_player]

    parsed = parse_roster(mock_team, PNA_2026, week=1)
    assert len(parsed.players) == 1
    rp = parsed.players[0]
    assert rp.name == "Brock Purdy"
    assert rp.actual_points == 21.1
    assert rp.projected_points == 15.48
    assert rp.has_played is True
    assert len(parsed.starters) == 1
    assert len(parsed.bench) == 0


def test_parse_roster_player_upcoming():
    mock_player = MagicMock()
    mock_player.name = "Derrick Henry"
    mock_player.position = "RB"
    mock_player.proTeam = "BAL"
    mock_player.lineupSlot = "RB"
    mock_player.points = None
    mock_player.projected_points = None
    mock_player.injuryStatus = "NORMAL"
    mock_player.bye_week = 14
    mock_player.percent_owned = 99.0
    mock_player.stats = {
        1: {
            "points": None,
            "projected_points": 16.37,
        }
    }

    mock_team = MagicMock()
    mock_team.team_name = "Mad Dawg"
    mock_team.roster = [mock_player]

    parsed = parse_roster(mock_team, PNA_2026, week=1)
    assert len(parsed.players) == 1
    rp = parsed.players[0]
    assert rp.name == "Derrick Henry"
    assert rp.actual_points == 0.0
    assert rp.projected_points == 16.37
    assert rp.has_played is False


def test_parse_roster_box_player():
    mock_box_player = MagicMock()
    mock_box_player.name = "Puka Nacua"
    mock_box_player.position = "WR"
    mock_box_player.proTeam = "LAR"
    mock_box_player.slot_position = "WR"
    mock_box_player.points = 12.4
    mock_box_player.projected_points = 21.04
    mock_box_player.game_played = 100
    mock_box_player.injuryStatus = "NORMAL"
    mock_box_player.bye_week = 6
    mock_box_player.percent_owned = 99.0
    mock_box_player.stats = {}

    parsed = parse_roster([mock_box_player], PNA_2026, week=1)
    assert len(parsed.players) == 1
    rp = parsed.players[0]
    assert rp.name == "Puka Nacua"
    assert rp.actual_points == 12.4
    assert rp.projected_points == 21.04
    assert rp.has_played is True
    assert rp.slot == "WR"
    assert len(parsed.starters) == 1


def test_parse_roster_bench_and_ir_slots():
    bench_p = MagicMock()
    bench_p.name = "Tyjae Spears"
    bench_p.position = "RB"
    bench_p.proTeam = "TEN"
    bench_p.lineupSlot = "BE"
    bench_p.points = None
    bench_p.projected_points = 9.36
    bench_p.injuryStatus = "NORMAL"
    bench_p.stats = {}

    ir_p = MagicMock()
    ir_p.name = "Injured Star"
    ir_p.position = "WR"
    ir_p.proTeam = "MIN"
    ir_p.lineupSlot = "IR"
    ir_p.points = None
    ir_p.projected_points = 0.0
    ir_p.injuryStatus = "IR"
    ir_p.stats = {}

    mock_team = MagicMock()
    mock_team.team_name = "Mad Dawg"
    mock_team.roster = [bench_p, ir_p]

    parsed = parse_roster(mock_team, PNA_2026, week=1)
    assert len(parsed.bench) == 1
    assert parsed.bench[0].name == "Tyjae Spears"
    assert len(parsed.ir) == 1
    assert parsed.ir[0].name == "Injured Star"
    assert len(parsed.starters) == 0
