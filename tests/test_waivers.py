from src.analysis.waivers import evaluate_waivers
from src.config import PNA_2026
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.gemini_client import GeminiIntelligenceClient


def test_evaluate_waivers_identifies_drop_candidates():
    # Roster with a backup kicker on bench and a low projection player
    bench_players = [
        RosterPlayer("Reserve Kicker", "K", "NYJ", "Bench", 6.0, 0.0, "NORMAL", 12, 10.0),
        RosterPlayer("Low Upside WR", "WR", "NE", "Bench", 2.5, 0.0, "NORMAL", 14, 5.0),
        RosterPlayer("Good Handcuff RB", "RB", "LAR", "Bench", 8.0, 0.0, "NORMAL", 6, 45.0),
    ]
    roster = ParsedRoster(team_name="Mad Dawg Team", players=bench_players, bench=bench_players)

    free_agents = [
        {
            "name": "Breakout RB",
            "position": "RB",
            "team": "MIA",
            "projected_points": 14.5,
            "percent_owned": 40.0,
        },
        {
            "name": "Top WR Stash",
            "position": "WR",
            "team": "GB",
            "projected_points": 11.0,
            "percent_owned": 25.0,
        },
    ]

    report = evaluate_waivers(PNA_2026, 2, roster, free_agents)
    assert len(report.targets) == 2
    assert report.targets[0].player_name == "Breakout RB"
    assert report.targets[0].priority == "MUST_ADD"

    # Verify drop candidates flagged reserve kicker and low projection player
    drop_text = " ".join(report.roster_drop_candidates)
    assert "Reserve Kicker" in drop_text or "Low Upside WR" in drop_text


def test_evaluate_waivers_with_gemini_client():
    roster = ParsedRoster(team_name="Mad Dawg Team", players=[], bench=[])

    class MockModels:
        def generate_content(self, *args, **kwargs):
            class Response:
                text = (
                    '{"league_id": 991059191, "week": 2, '
                    '"targets": [{"player_name": "Waiver Star", "position": "RB", "team": "DET", "priority": "MUST_ADD", "recommended_drop": "Bench WR", "reasoning": "High upside", "upside_summary": "Top pickup"}], '
                    '"roster_drop_candidates": ["Bench WR"], "overall_waiver_strategy": "Aggressive spend"}'
                )

            return Response()

    class MockSdk:
        models = MockModels()

    client = GeminiIntelligenceClient(mock_client=MockSdk())
    report = evaluate_waivers(PNA_2026, 2, roster, free_agents=[], client=client)
    assert report.targets[0].player_name == "Waiver Star"
    assert report.overall_waiver_strategy == "Aggressive spend"
