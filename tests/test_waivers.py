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


def test_evaluate_waivers_stand_pat_when_bench_is_strong():
    # Healthy starters and solid bench with no dead weight
    starters = [
        RosterPlayer("Top QB", "QB", "BUF", "QB", 20.0, 0.0, "NORMAL", 7, 95.0),
        RosterPlayer("Top RB", "RB", "SF", "RB", 18.0, 0.0, "NORMAL", 9, 99.0),
    ]
    bench = [
        RosterPlayer("Solid Backup RB", "RB", "DET", "Bench", 11.5, 0.0, "NORMAL", 5, 80.0),
        RosterPlayer("Solid Backup WR", "WR", "HOU", "Bench", 10.0, 0.0, "NORMAL", 7, 75.0),
    ]
    roster = ParsedRoster(team_name="Mad Dawg Team", players=starters + bench, bench=bench)

    free_agents = [
        {
            "name": "Fringe WR",
            "position": "WR",
            "team": "CAR",
            "projected_points": 7.5,
            "percent_owned": 15.0,
        },
        {
            "name": "Backup RB3",
            "position": "RB",
            "team": "DEN",
            "projected_points": 6.0,
            "percent_owned": 10.0,
        },
    ]

    report = evaluate_waivers(PNA_2026, 2, roster, free_agents)
    assert report.coach_verdict == "STAND_PAT"
    assert report.is_move_recommended is False
    assert len(report.targets) == 0
    assert "STAND PAT" in report.overall_waiver_strategy
    assert "priority" in report.stand_pat_reasoning.lower()


def test_evaluate_waivers_gemini_stand_pat():
    roster = ParsedRoster(team_name="Mad Dawg Team", players=[], bench=[])

    class MockModels:
        def generate_content(self, *args, **kwargs):
            class Response:
                text = (
                    '{"league_id": 991059191, "week": 2, "is_move_recommended": false, '
                    '"coach_verdict": "STAND_PAT", '
                    '"stand_pat_reasoning": "Roster is stacked and firing on all cylinders. No need to burn waiver priority on JAGs.", '
                    '"targets": [], "roster_drop_candidates": [], '
                    '"overall_waiver_strategy": "Stand pat and maintain waiver discipline."}'
                )

            return Response()

    class MockSdk:
        models = MockModels()

    client = GeminiIntelligenceClient(mock_client=MockSdk())
    report = evaluate_waivers(PNA_2026, 2, roster, free_agents=[], client=client)
    assert report.coach_verdict == "STAND_PAT"
    assert report.is_move_recommended is False
    assert len(report.targets) == 0
    assert "burn waiver priority" in report.stand_pat_reasoning

