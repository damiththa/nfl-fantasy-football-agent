from unittest.mock import MagicMock

from src.analysis.recap import (
    _compute_optimal_points,
    _find_missed_opportunities,
    generate_weekly_recap,
)
from src.config import PNA_2026
from src.espn.matchup import MatchupData
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.schemas import WeeklyRecapReport


def _create_mock_player(name, pos, slot, act_pts, proj_pts, played=True):
    return RosterPlayer(
        name=name,
        position=pos,
        team="SF",
        slot=slot,
        projected_points=proj_pts,
        actual_points=act_pts,
        has_played=played,
    )


def test_compute_optimal_points():
    players = [
        _create_mock_player("Purdy", "QB", "QB", 20.0, 15.0),
        _create_mock_player("BackupQB", "QB", "BE", 12.0, 10.0),
        _create_mock_player("Henry", "RB", "RB", 25.0, 16.0),
        _create_mock_player("Swift", "RB", "RB", 10.0, 12.0),
        _create_mock_player("BenchRB", "RB", "BE", 18.0, 8.0),  # Beats Swift, should take flex or RB slot
        _create_mock_player("Nacua", "WR", "WR", 22.0, 18.0),
        _create_mock_player("Smith", "WR", "WR", 14.0, 14.0),
        _create_mock_player("BenchWR", "WR", "BE", 5.0, 6.0),
        _create_mock_player("Kittle", "TE", "TE", 12.0, 10.0),
        _create_mock_player("DST", "D/ST", "D/ST", 8.0, 7.0),
    ]
    # PNA 2026: 1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX, 1 DST, 0 K
    # QB: Purdy (20.0)
    # RB: Henry (25.0), BenchRB (18.0)
    # WR: Nacua (22.0), Smith (14.0)
    # TE: Kittle (12.0)
    # FLEX (2): Swift (10.0), BenchWR (5.0)
    # DST: 8.0
    # Total optimal: 20 + 25 + 18 + 22 + 14 + 12 + 10 + 5 + 8 = 134.0
    optimal = _compute_optimal_points(players, PNA_2026)
    assert optimal == 134.0


def test_find_missed_opportunities():
    starters = [
        _create_mock_player("Swift", "RB", "RB", 6.0, 12.0),
        _create_mock_player("Smith", "WR", "WR", 14.0, 14.0),
    ]
    bench = [
        _create_mock_player("BenchRB", "RB", "BE", 18.0, 8.0),
        _create_mock_player("BenchWR", "WR", "BE", 4.0, 6.0),
    ]
    misses = _find_missed_opportunities(starters, bench)
    assert len(misses) == 1
    assert misses[0].bench_player == "BenchRB"
    assert misses[0].started_player == "Swift"
    assert misses[0].points_differential == 12.0


def test_generate_weekly_recap_deterministic_win():
    starters = [
        _create_mock_player("Purdy", "QB", "QB", 21.1, 15.5, played=True),
        _create_mock_player("Henry", "RB", "RB", 18.4, 16.0, played=True),
        _create_mock_player("Swift", "RB", "RB", 12.0, 12.0, played=True),
        _create_mock_player("Nacua", "WR", "WR", 22.0, 18.0, played=True),
        _create_mock_player("Smith", "WR", "WR", 14.0, 14.0, played=True),
        _create_mock_player("Kittle", "TE", "TE", 10.0, 8.0, played=True),
        _create_mock_player("Flex1", "WR", "FLEX", 11.0, 10.0, played=True),
        _create_mock_player("Flex2", "RB", "FLEX", 9.0, 8.0, played=True),
        _create_mock_player("DST", "D/ST", "D/ST", 7.0, 7.0, played=True),
    ]
    bench = [
        _create_mock_player("Shakir", "WR", "BE", 6.0, 8.0, played=True),
    ]
    user_roster = ParsedRoster(
        team_name="Mad Dawg",
        players=starters + bench,
        starters=starters,
        bench=bench,
        ir=[],
    )

    opp_starters = [
        _create_mock_player("OppQB", "QB", "QB", 15.0, 16.0, played=True),
        _create_mock_player("OppRB", "RB", "RB", 12.0, 14.0, played=True),
        _create_mock_player("OppWR", "WR", "WR", 10.0, 12.0, played=True),
        _create_mock_player("OppTE", "TE", "TE", 8.0, 8.0, played=True),
    ]
    opp_roster = ParsedRoster(
        team_name="Rival Team",
        players=opp_starters,
        starters=opp_starters,
        bench=[],
        ir=[],
    )

    matchup = MatchupData(
        week=1,
        your_team=user_roster,
        opponent_team=opp_roster,
        your_projected=100.0,
        opp_projected=50.0,
        projected_margin=50.0,
        is_favorite=True,
        your_score=124.5,
        opp_score=45.0,
    )

    report = generate_weekly_recap(PNA_2026, 1, matchup, client=None)
    assert isinstance(report, WeeklyRecapReport)
    assert report.result == "WIN"
    assert report.matchup_status == "FINAL"
    assert report.user_score == 124.5
    assert report.opponent_score == 45.0
    assert report.score_margin == 79.5
    assert report.optimal_lineup_points == 124.5
    assert report.points_left_on_bench == 0.0  # Shakir had 6.0, lower than any starter
    assert len(report.game_balls) >= 1
    assert "Film Room" in report.coach_game_summary


def test_generate_weekly_recap_with_gemini_mock():
    mock_client = MagicMock()
    mock_report = WeeklyRecapReport(
        league_id=991059191,
        league_name="PNA 2026",
        week=1,
        matchup_status="FINAL",
        result="WIN",
        user_team_name="Mad Dawg",
        user_score=110.0,
        user_projected=105.0,
        opponent_team_name="Rival",
        opponent_score=95.0,
        opponent_projected=100.0,
        score_margin=15.0,
        optimal_lineup_points=120.0,
        points_left_on_bench=10.0,
        coach_game_summary="Masterclass execution across all skill positions.",
    )
    mock_client.generate_structured.return_value = mock_report

    starters = [_create_mock_player("Purdy", "QB", "QB", 20.0, 15.0)]
    roster = ParsedRoster(
        team_name="Mad Dawg",
        players=starters,
        starters=starters,
        bench=[],
        ir=[],
    )
    matchup = MatchupData(
        week=1,
        your_team=roster,
        opponent_team=None,
        your_projected=15.0,
        opp_projected=0.0,
        projected_margin=15.0,
        is_favorite=True,
        your_score=20.0,
        opp_score=0.0,
    )

    report = generate_weekly_recap(PNA_2026, 1, matchup, client=mock_client)
    assert mock_client.generate_structured.called
    assert report.result == "WIN"
    assert report.coach_game_summary == "Masterclass execution across all skill positions."
