"""Tests for starting lineup hole detection and 3-tier emergency recommendations."""

from src.analysis.lineup import (
    detect_lineup_holes_and_solutions,
    enrich_lineup_recommendation_with_espn_status,
    optimize_lineup,
)
from src.config import PNA_2026
from src.data.injuries import PlayerInjuryInfo
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.schemas import LineupRecommendation


class MockESPNPlayer:
    def __init__(self, name: str, position: str, pro_team: str = "FA", projected_points: float = 10.0):
        self.name = name
        self.position = position
        self.proTeam = pro_team
        self.projected_points = projected_points


class MockESPNTeam:
    def __init__(self, team_id: int, team_name: str, roster: list[MockESPNPlayer]):
        self.team_id = team_id
        self.team_name = team_name
        self.roster = roster


class MockESPNLeague:
    def __init__(self, teams: list[MockESPNTeam], free_agents: list[MockESPNPlayer]):
        self.teams = teams
        self._free_agents = free_agents

    def free_agents(self, size: int = 50):
        return self._free_agents[:size]


def _build_pna_starters(
    qb_status: str = "NORMAL",
    rb1_status: str = "NORMAL",
    rb2_status: str = "NORMAL",
    wr1_status: str = "NORMAL",
    wr2_status: str = "NORMAL",
    te_status: str = "NORMAL",
    flex1_status: str = "NORMAL",
    flex2_status: str = "NORMAL",
    dst_status: str = "NORMAL",
    wr1_bye: int = 0,
) -> list[RosterPlayer]:
    """Helper to build standard 9 PNA starters."""
    return [
        RosterPlayer("Patrick Mahomes", "QB", "KC", "QB", 22.0, 0.0, qb_status, 10, 99.0),
        RosterPlayer("Christian McCaffrey", "RB", "SF", "RB", 19.5, 0.0, rb1_status, 9, 99.0),
        RosterPlayer("Kyren Williams", "RB", "LAR", "RB", 16.0, 0.0, rb2_status, 6, 95.0),
        RosterPlayer("Justin Jefferson", "WR", "MIN", "WR", 18.0, 0.0, wr1_status, wr1_bye, 99.0),
        RosterPlayer("CeeDee Lamb", "WR", "DAL", "WR", 17.5, 0.0, wr2_status, 7, 99.0),
        RosterPlayer("Travis Kelce", "TE", "KC", "TE", 13.5, 0.0, te_status, 10, 95.0),
        RosterPlayer("Deebo Samuel", "WR", "SF", "FLEX", 14.0, 0.0, flex1_status, 9, 90.0),
        RosterPlayer("James Conner", "RB", "ARI", "FLEX", 13.0, 0.0, flex2_status, 11, 88.0),
        RosterPlayer("SF Defense", "DST", "SF", "DST", 8.0, 0.0, dst_status, 9, 85.0),
    ]


def _build_bench() -> list[RosterPlayer]:
    return [
        RosterPlayer("Jordan Mason", "RB", "SF", "BE", 11.5, 0.0, "NORMAL", 9, 70.0),
        RosterPlayer("Brian Thomas Jr.", "WR", "JAX", "BE", 11.0, 0.0, "NORMAL", 12, 75.0),
        RosterPlayer("Isaiah Likely", "TE", "BAL", "BE", 8.5, 0.0, "NORMAL", 14, 60.0),
        RosterPlayer("Tyler Allgeier", "RB", "ATL", "BE", 5.0, 0.0, "NORMAL", 12, 40.0),
    ]


def test_detect_lineup_holes_vacant_slot():
    """Test when an ESPN starting slot is physically vacant (e.g. only 8 starters)."""
    starters = _build_pna_starters()
    # Remove TE starter so TE is vacant
    starters = [p for p in starters if p.position != "TE"]
    bench = _build_bench()
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    fa_list = [MockESPNPlayer("Hunter Henry", "TE", "NE", 9.0)]
    opp_team = MockESPNTeam(
        team_id=99,
        team_name="Rival Manager",
        roster=[
            MockESPNPlayer("George Kittle", "TE", "SF", 12.0),
            MockESPNPlayer("Dallas Goedert", "TE", "PHI", 10.5),
        ],
    )
    espn_league = MockESPNLeague(teams=[opp_team], free_agents=fa_list)

    alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=PNA_2026,
        current_week=1,
        espn_league=espn_league,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.slot == "TE"
    assert "VACANT ON ESPN" in alert.current_status
    assert alert.current_player_name is None

    # Tier 1: Bench recommendation should pick Isaiah Likely
    assert "Isaiah Likely" in alert.bench_recommendation
    assert "Promote" in alert.bench_recommendation

    # Tier 2: Free agency claim should pick Hunter Henry and suggest Tyler Allgeier drop
    assert "Hunter Henry" in alert.waiver_recommendation
    assert "Tyler Allgeier" in alert.waiver_recommendation

    # Tier 3: Trade target should identify Rival Manager's TE surplus
    assert "George Kittle" in alert.trade_recommendation or "Dallas Goedert" in alert.trade_recommendation
    assert "Rival Manager" in alert.trade_recommendation


def test_detect_lineup_holes_injured_starter_out():
    """Test when a starter is marked OUT."""
    starters = _build_pna_starters(wr2_status="OUT")
    bench = _build_bench()
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=PNA_2026,
        current_week=1,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.current_player_name == "CeeDee Lamb"
    assert "OUT" in alert.current_status
    # Should recommend Brian Thomas Jr. (top eligible bench WR)
    assert "Brian Thomas Jr." in alert.bench_recommendation


def test_detect_lineup_holes_bye_week_starter():
    """Test when a starter is on a bye week."""
    # Week 5, Justin Jefferson has bye_week = 5
    starters = _build_pna_starters(wr1_bye=5)
    bench = _build_bench()
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=PNA_2026,
        current_week=5,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.current_player_name == "Justin Jefferson"
    assert "BYE WEEK (Week 5)" in alert.current_status
    assert "Brian Thomas Jr." in alert.bench_recommendation


def test_detect_lineup_holes_suspended_starter():
    """Test when a starter is SUSPENDED."""
    starters = _build_pna_starters(rb2_status="SUS")
    bench = _build_bench()
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=PNA_2026,
        current_week=1,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.current_player_name == "Kyren Williams"
    assert "SUSPENDED" in alert.current_status
    # Should promote Jordan Mason
    assert "Jordan Mason" in alert.bench_recommendation


def test_detect_lineup_holes_from_external_injuries_feed():
    """Test when starter is ACTIVE in ESPN but external injury feed reports OUT."""
    starters = _build_pna_starters()  # all normal in ESPN
    bench = _build_bench()
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    injuries = [
        PlayerInjuryInfo(
            player_id="1234",
            full_name="Christian McCaffrey",
            team="SF",
            position="RB",
            injury_status="OUT",
            practice_participation="Did Not Participate",
            injury_body_part="Calf",
            injury_notes="Calf strain, ruled out for Sunday",
            sport="nfl",
            active=False,
        )
    ]

    alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=PNA_2026,
        current_week=1,
        injuries=injuries,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.current_player_name == "Christian McCaffrey"
    assert "OUT" in alert.current_status
    assert "Jordan Mason" in alert.bench_recommendation


def test_detect_lineup_holes_bench_exhausted():
    """Test when bench has zero healthy/eligible players for that position."""
    # QB is OUT, but bench has no QB
    starters = _build_pna_starters(qb_status="OUT")
    bench = _build_bench()  # Bench has RB, WR, TE only
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    fa_list = [MockESPNPlayer("Geno Smith", "QB", "SEA", 16.5)]
    espn_league = MockESPNLeague(teams=[], free_agents=fa_list)

    alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=PNA_2026,
        current_week=1,
        espn_league=espn_league,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.current_player_name == "Patrick Mahomes"
    # Bench exhausted warning
    assert "No healthy, eligible bench replacement" in alert.bench_recommendation
    assert "exhausted" in alert.bench_recommendation.lower()
    # Waiver recommendation should still suggest Geno Smith
    assert "Geno Smith" in alert.waiver_recommendation


def test_enrich_lineup_recommendation_populates_hole_alerts():
    """Test enrich_lineup_recommendation_with_espn_status populates lineup_hole_alerts."""
    starters = _build_pna_starters(wr2_status="OUT")
    bench = _build_bench()
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    rec = LineupRecommendation(
        league_id=PNA_2026.league_id,
        week=1,
        game_theory_strategy="BALANCED",
        strategy_reasoning="Test strategy",
        recommended_starters=[],
        bench_players=[],
    )

    enriched = enrich_lineup_recommendation_with_espn_status(
        rec=rec,
        roster=roster,
        league=PNA_2026,
        current_week=1,
    )

    assert len(enriched.lineup_hole_alerts) == 1
    assert enriched.lineup_hole_alerts[0].current_player_name == "CeeDee Lamb"


def test_optimize_lineup_end_to_end_hole_detection():
    """Test optimize_lineup deterministic run with an OUT starter detects hole."""
    starters = _build_pna_starters(te_status="OUT")
    bench = _build_bench()
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench)

    fa_list = [MockESPNPlayer("Zach Ertz", "TE", "WAS", 8.0)]
    espn_league = MockESPNLeague(teams=[], free_agents=fa_list)

    rec = optimize_lineup(
        league=PNA_2026,
        week=1,
        roster=roster,
        espn_league=espn_league,
    )

    assert len(rec.lineup_hole_alerts) >= 1
    te_hole = next((h for h in rec.lineup_hole_alerts if h.slot == "TE"), None)
    assert te_hole is not None
    assert te_hole.current_player_name == "Travis Kelce"
    assert "Isaiah Likely" in te_hole.bench_recommendation
    assert "Zach Ertz" in te_hole.waiver_recommendation
