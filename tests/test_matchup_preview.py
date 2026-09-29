from src.analysis.matchup_preview import _calculate_win_probability, generate_matchup_preview
from src.config import PNA_2026
from src.espn.matchup import MatchupData
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.gemini_client import GeminiIntelligenceClient


def test_win_probability_calculation():
    # Large positive margin -> high win prob
    assert _calculate_win_probability(24.0) > 0.85

    # 0 margin -> 50%
    assert _calculate_win_probability(0.0) == 0.50

    # Large negative margin -> low win prob
    assert _calculate_win_probability(-24.0) < 0.15


def test_generate_matchup_preview_deterministic():
    user_starters = [
        RosterPlayer("Patrick Mahomes", "QB", "KC", "QB", 24.0, 0.0, "NORMAL", 10, 99.0),
        RosterPlayer("Saquon Barkley", "RB", "PHI", "RB", 18.0, 0.0, "NORMAL", 5, 99.0),
    ]
    opp_starters = [
        RosterPlayer("Daniel Jones", "QB", "NYG", "QB", 14.0, 0.0, "NORMAL", 11, 40.0),
        RosterPlayer("Kyren Williams", "RB", "LAR", "RB", 16.0, 0.0, "NORMAL", 6, 95.0),
    ]

    user_roster = ParsedRoster(
        team_name="Mad Dawg Team", players=user_starters, starters=user_starters
    )
    opp_roster = ParsedRoster(
        team_name="Opponent Team", players=opp_starters, starters=opp_starters
    )

    matchup = MatchupData(
        week=1,
        your_team=user_roster,
        opponent_team=opp_roster,
        your_projected=120.0,
        opp_projected=105.0,
        projected_margin=15.0,
        is_favorite=True,
    )

    report = generate_matchup_preview(PNA_2026, 1, matchup)
    assert report.league_id == PNA_2026.league_id
    assert report.opponent_name == "Opponent Team"
    assert report.projected_margin == 15.0
    assert report.win_probability > 0.70

    # Mahomes (+10 over Jones) should trigger QB positional advantage
    adv_text = " ".join(report.key_advantages)
    assert "QB" in adv_text


def test_generate_matchup_preview_with_gemini():
    user_roster = ParsedRoster(team_name="Mad Dawg Team", players=[], starters=[])
    matchup = MatchupData(
        week=1,
        your_team=user_roster,
        opponent_team=None,
        your_projected=115.0,
        opp_projected=110.0,
        projected_margin=5.0,
        is_favorite=True,
    )

    class MockModels:
        def generate_content(self, *args, **kwargs):
            class Response:
                text = (
                    '{"league_id": 991059191, "week": 1, "opponent_name": "Rival", '
                    '"projected_score_user": 115.0, "projected_score_opponent": 110.0, '
                    '"projected_margin": 5.0, "win_probability": 0.60, '
                    '"key_advantages": ["Higher WR floor"], '
                    '"key_vulnerabilities": ["Lower RB ceiling"], '
                    '"weather_and_vegas_factors": ["High Vegas total in Kansas City"], '
                    '"strategic_summary": "Slight favorite; maintain disciplined starting lineup."}'
                )

            return Response()

    class MockSdk:
        models = MockModels()

    client = GeminiIntelligenceClient(mock_client=MockSdk())
    report = generate_matchup_preview(PNA_2026, 1, matchup, client=client)
    assert report.opponent_name == "Rival"
    assert report.win_probability == 0.60


def test_matchup_data_lineup_properties():
    """Verify your_lineup and opponent_lineup convenience properties on MatchupData."""
    p1 = RosterPlayer("Player 1", "RB", "KC", "RB", 15.0, 18.2, "NORMAL", 10, 90.0)
    p2 = RosterPlayer("Player 2", "WR", "BUF", "WR", 12.0, 14.5, "NORMAL", 12, 85.0)
    b1 = RosterPlayer("Bench 1", "RB", "MIA", "Bench", 8.0, 0.0, "NORMAL", 6, 40.0)

    user_roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[p1, b1],
        starters=[p1],
        bench=[b1],
    )
    opp_roster = ParsedRoster(
        team_name="Opponent",
        players=[p2],
        starters=[p2],
        bench=[],
    )

    matchup = MatchupData(
        week=2,
        your_team=user_roster,
        opponent_team=opp_roster,
        your_projected=110.0,
        opp_projected=100.0,
        projected_margin=10.0,
        is_favorite=True,
    )

    # Property access must return starters list and not raise AttributeError
    assert hasattr(matchup, "your_lineup")
    assert hasattr(matchup, "opponent_lineup")
    assert matchup.your_lineup == [p1]
    assert matchup.opponent_lineup == [p2]

    # Test fallback when starters is empty but players exists
    empty_starters_roster = ParsedRoster(
        team_name="No Starters",
        players=[p1, b1],
        starters=[],
        bench=[b1],
    )
    matchup_fallback = MatchupData(
        week=2,
        your_team=empty_starters_roster,
        opponent_team=None,
        your_projected=100.0,
        opp_projected=100.0,
        projected_margin=0.0,
        is_favorite=False,
    )
    assert matchup_fallback.your_lineup == [p1, b1]
    assert matchup_fallback.opponent_lineup == []

