from src.analysis.trades import calculate_vorp, evaluate_trade
from src.config import PNA_2026
from src.espn.roster import ParsedRoster
from src.intelligence.gemini_client import GeminiIntelligenceClient


def test_calculate_vorp():
    # 12-team league: RB baseline is 8.5
    rb_vorp = calculate_vorp(projected_ppg=18.5, position="RB", num_teams=12)
    assert rb_vorp == 10.0

    # 10-team league: RB baseline is 9.5
    rb_vorp_10 = calculate_vorp(projected_ppg=18.5, position="RB", num_teams=10)
    assert rb_vorp_10 == 9.0

    # Sub-replacement player has negative VORP
    sub_vorp = calculate_vorp(projected_ppg=6.0, position="RB", num_teams=12)
    assert sub_vorp == -2.5


def test_evaluate_trade_accept_and_reject():
    roster = ParsedRoster(team_name="Mad Dawg Team", players=[])

    # Player projections: {name: (pos, ppg)}
    projections = {
        "Elite RB": ("RB", 20.0),  # VORP in 12-team: 20 - 8.5 = 11.5
        "Flex WR": ("WR", 11.0),  # VORP: 11 - 9.5 = 1.5
        "Bench RB": ("RB", 9.0),  # VORP: 9 - 8.5 = 0.5
    }

    # Giving Flex WR (1.5) + Bench RB (0.5) = 2.0 total VORP
    # Receiving Elite RB (11.5 VORP)
    # Net: +9.5 VORP -> Massive ACCEPT
    accept_eval = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["Flex WR", "Bench RB"],
        receiving_players=["Elite RB"],
        player_projections=projections,
    )
    assert accept_eval.verdict == "ACCEPT"
    assert accept_eval.your_vorp_change > 2.0

    # Reverse trade: Giving Elite RB for 2 bench pieces -> REJECT
    reject_eval = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["Elite RB"],
        receiving_players=["Flex WR", "Bench RB"],
        player_projections=projections,
    )
    assert reject_eval.verdict == "REJECT"
    assert reject_eval.your_vorp_change < -2.0


def test_evaluate_trade_with_gemini_client():
    roster = ParsedRoster(team_name="Mad Dawg Team", players=[])

    class MockModels:
        def generate_content(self, *args, **kwargs):
            class Response:
                text = (
                    '{"verdict": "ACCEPT", "your_vorp_change": 5.4, '
                    '"starting_lineup_impact": "Major starting RB upgrade", '
                    '"playoff_schedule_impact": "Favorable matchups in weeks 15-17", '
                    '"reasoning": "Clear upgrade to weekly starting scoring potential", '
                    '"counter_suggestion": null}'
                )

            return Response()

    class MockSdk:
        models = MockModels()

    client = GeminiIntelligenceClient(mock_client=MockSdk())
    trade_eval = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["Good WR"],
        receiving_players=["Superstar RB"],
        client=client,
    )
    assert trade_eval.verdict == "ACCEPT"
    assert trade_eval.your_vorp_change == 5.4
