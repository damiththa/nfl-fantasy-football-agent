"""Tests for starting lineup hole detection and 3-tier emergency recommendations."""

from unittest.mock import MagicMock

from src.analysis.lineup import (
    detect_lineup_holes_and_solutions,
    enrich_lineup_recommendation_with_espn_status,
    optimize_lineup,
)
from src.config import PNA_2026
from src.data.injuries import PlayerInjuryInfo
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.schemas import LineupRecommendation, StartSitDecision


class MockESPNPlayer:
    def __init__(
        self, name: str, position: str, pro_team: str = "FA", projected_points: float = 10.0
    ):
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
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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
    assert (
        "George Kittle" in alert.trade_recommendation
        or "Dallas Goedert" in alert.trade_recommendation
    )
    assert "Rival Manager" in alert.trade_recommendation


def test_detect_lineup_holes_injured_starter_out():
    """Test when a starter is marked OUT."""
    starters = _build_pna_starters(wr2_status="OUT")
    bench = _build_bench()
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

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


def test_detect_lineup_holes_locked_bench_not_promoted():
    """Test that bench players who have already played (locked on bench) are NEVER recommended for promotion."""
    # WR2 CeeDee Lamb is OUT
    starters = _build_pna_starters(wr2_status="OUT")

    # Bench has Drake London who played Thursday (has_played=True, is_locked=True) and no other WR/FLEX
    bench = [
        RosterPlayer(
            "Drake London",
            "WR",
            "ATL",
            "BE",
            14.5,
            12.0,
            "NORMAL",
            12,
            85.0,
            has_played=True,
            is_locked=True,
        ),
    ]
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

    fa_list = [MockESPNPlayer("Demarcus Robinson", "WR", "LAR", 8.5)]
    espn_league = MockESPNLeague(teams=[], free_agents=fa_list)

    alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=PNA_2026,
        current_week=1,
        espn_league=espn_league,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.current_player_name == "CeeDee Lamb"
    # Drake London must NOT be recommended for promotion!
    assert "Promote Drake London" not in alert.bench_recommendation
    # Must explicitly state that eligible bench options are locked from prior games
    assert "LOCKED from prior games" in alert.bench_recommendation
    assert "Drake London" in alert.bench_recommendation
    assert "Zero legal bench swaps exist" in alert.bench_recommendation
    # Must provide IR slot tip
    assert "move CeeDee Lamb directly to an IR slot on ESPN" in alert.waiver_recommendation
    assert "Demarcus Robinson" in alert.waiver_recommendation


def test_enrich_lineup_actionable_swaps_excludes_locked_bench():
    """Test actionable_swaps excludes locked bench players from promotion and triggers emergency Sunday advice."""
    starters = _build_pna_starters(wr2_status="OUT")
    # Mark CeeDee Lamb as needing to be moved to bench
    bench = [
        RosterPlayer(
            "Drake London",
            "WR",
            "ATL",
            "BE",
            14.5,
            12.0,
            "NORMAL",
            12,
            85.0,
            has_played=True,
            is_locked=True,
        ),
    ]
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

    rec = LineupRecommendation(
        league_id=PNA_2026.league_id,
        week=1,
        game_theory_strategy="BALANCED",
        strategy_reasoning="Test",
        recommended_starters=[],
        bench_players=[
            StartSitDecision(
                player_name="CeeDee Lamb",
                position="WR",
                team="DAL",
                action="BENCH",
                confidence=1.0,
                floor=0.0,
                ceiling=0.0,
                projected_points=0.0,
                reasoning="Injured",
            )
        ],
    )

    enriched = enrich_lineup_recommendation_with_espn_status(
        rec=rec,
        roster=roster,
        league=PNA_2026,
        current_week=1,
    )

    # Actionable swaps must NOT recommend starting Drake London
    for swap in enriched.actionable_swaps:
        assert "Start Drake London" not in swap
    # Must flag emergency notice
    assert any("NO UNLOCKED BENCH REPLACEMENT" in swap for swap in enriched.actionable_swaps)
    assert any(
        "Move CeeDee Lamb to IR and claim an emergency Sunday Free Agent streamer" in swap
        for swap in enriched.actionable_swaps
    )


def test_optimize_lineup_deterministic_handles_locked_starters_and_bench():
    """Test deterministic lineup optimizer preserves locked starters and never starts locked bench players."""
    thursday_starter = RosterPlayer(
        "Bijan Robinson",
        "RB",
        "ATL",
        "RB",
        16.0,
        18.2,
        "NORMAL",
        12,
        99.0,
        has_played=True,
        is_locked=True,
    )
    thursday_bench = RosterPlayer(
        "Drake London",
        "WR",
        "ATL",
        "BE",
        14.0,
        15.4,
        "NORMAL",
        12,
        85.0,
        has_played=True,
        is_locked=True,
    )
    sunday_starter_healthy = RosterPlayer(
        "Patrick Mahomes",
        "QB",
        "KC",
        "QB",
        22.0,
        0.0,
        "NORMAL",
        10,
        99.0,
        has_played=False,
        is_locked=False,
    )
    sunday_starter_injured = RosterPlayer(
        "DJ Moore", "WR", "CHI", "WR", 12.0, 0.0, "OUT", 7, 80.0, has_played=False, is_locked=False
    )
    sunday_bench_healthy = RosterPlayer(
        "Brian Thomas Jr.",
        "WR",
        "JAX",
        "BE",
        11.0,
        0.0,
        "NORMAL",
        12,
        75.0,
        has_played=False,
        is_locked=False,
    )

    # Build starters and bench for PNA roster
    starters = [
        sunday_starter_healthy,
        thursday_starter,
        RosterPlayer("Kyren Williams", "RB", "LAR", "RB", 15.0, 0.0, "NORMAL", 6, 95.0),
        sunday_starter_injured,
        RosterPlayer("Justin Jefferson", "WR", "MIN", "WR", 18.0, 0.0, "NORMAL", 6, 99.0),
        RosterPlayer("Travis Kelce", "TE", "KC", "TE", 13.0, 0.0, "NORMAL", 10, 95.0),
        RosterPlayer("James Conner", "RB", "ARI", "FLEX", 13.0, 0.0, "NORMAL", 11, 88.0),
        RosterPlayer("Deebo Samuel", "WR", "SF", "FLEX", 14.0, 0.0, "NORMAL", 9, 90.0),
        RosterPlayer("SF Defense", "DST", "SF", "DST", 8.0, 0.0, "NORMAL", 9, 85.0),
    ]
    bench = [thursday_bench, sunday_bench_healthy]
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

    rec = optimize_lineup(league=PNA_2026, week=1, roster=roster)

    rec_starter_names = [s.player_name for s in rec.recommended_starters]
    rec_bench_names = [b.player_name for b in rec.bench_players]

    # Locked starter MUST remain in starting lineup
    assert "Bijan Robinson" in rec_starter_names
    # Locked bench player MUST NOT be in starting lineup despite high points/projection
    assert "Drake London" not in rec_starter_names
    assert "Drake London" in rec_bench_names
    # Unlocked bench WR Brian Thomas Jr. should be promoted over OUT DJ Moore
    assert "Brian Thomas Jr." in rec_starter_names
    assert "DJ Moore" in rec_bench_names


def test_optimize_lineup_guardrail_demotes_hallucinated_locked_bench_starter():
    """Test post-LLM guardrails catch hallucinated promotions of locked bench players."""
    thursday_bench = RosterPlayer(
        "Drake London",
        "WR",
        "ATL",
        "BE",
        14.0,
        15.4,
        "NORMAL",
        12,
        85.0,
        has_played=True,
        is_locked=True,
    )
    thursday_starter = RosterPlayer(
        "Bijan Robinson",
        "RB",
        "ATL",
        "RB",
        16.0,
        18.2,
        "NORMAL",
        12,
        99.0,
        has_played=True,
        is_locked=True,
    )
    starters = _build_pna_starters()
    # Replace RB1 with Thursday starter
    starters[1] = thursday_starter
    bench = [thursday_bench]
    roster = ParsedRoster(
        team_name="Mad Dawg Team", players=starters + bench, starters=starters, bench=bench
    )

    # Mock Gemini returning Drake London in recommended_starters and omitting Bijan Robinson
    mock_client = MagicMock()
    mock_client.model = "gemini-3.1-pro"
    mock_rec = LineupRecommendation(
        league_id=PNA_2026.league_id,
        week=1,
        game_theory_strategy="BALANCED",
        strategy_reasoning="LLM Hallucination Test",
        recommended_starters=[
            StartSitDecision(
                player_name="Patrick Mahomes",
                position="QB",
                team="KC",
                action="START",
                confidence=1.0,
                floor=20.0,
                ceiling=25.0,
                projected_points=22.0,
                reasoning="Start QB",
            ),
            StartSitDecision(
                player_name="Drake London",
                position="WR",
                team="ATL",
                action="START",
                confidence=0.9,
                floor=10.0,
                ceiling=20.0,
                projected_points=14.0,
                reasoning="LLM says start Drake!",
            ),
        ],
        bench_players=[],
    )
    mock_client.generate_structured.return_value = mock_rec

    rec = optimize_lineup(league=PNA_2026, week=1, roster=roster, client=mock_client)

    rec_starter_names = [s.player_name for s in rec.recommended_starters]
    rec_bench_names = [b.player_name for b in rec.bench_players]

    # Guardrail 2: Drake London must be demoted back to bench!
    assert "Drake London" not in rec_starter_names
    assert "Drake London" in rec_bench_names
    drake_bench = next(b for b in rec.bench_players if b.player_name == "Drake London")
    assert "LOCKED ON BENCH" in drake_bench.reasoning

    # Guardrail 3: Locked starter Bijan Robinson must be re-inserted!
    assert "Bijan Robinson" in rec_starter_names
