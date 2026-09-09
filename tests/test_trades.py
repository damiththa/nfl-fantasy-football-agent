from src.analysis.trades import (
    calculate_vorp,
    compute_optimal_starters,
    evaluate_trade,
    normalize_player_name,
    validate_trade_roster_ownership,
)
from src.config import PNA_2026
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.gemini_client import GeminiIntelligenceClient


def _make_player(name: str, position: str, projected_points: float, slot: str = "Bench") -> RosterPlayer:
    return RosterPlayer(
        name=name,
        position=position,
        team="NFL",
        slot=slot,
        projected_points=projected_points,
        actual_points=0.0,
        injury_status="ACTIVE",
        bye_week=0,
        percent_owned=95.0,
    )


def test_normalize_player_name():
    assert normalize_player_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_player_name("Marvin Harrison Jr.") == "marvin harrison"
    assert normalize_player_name("D'Andre Swift") == "dandre swift"
    assert normalize_player_name("A.J. Brown") == "aj brown"
    assert normalize_player_name("Brock Purdy") == "brock purdy"


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


def test_validate_trade_ownership_giving_unowned_player():
    roster = ParsedRoster(
        team_name="Mad Dawg Team",
        players=[_make_player("Brock Purdy", "QB", 18.0), _make_player("Kyren Williams", "RB", 15.0)],
    )

    # Trying to give Christian McCaffrey (not on roster)
    is_valid, errors, matched = validate_trade_roster_ownership(
        roster=roster,
        giving_players=["Christian McCaffrey"],
        receiving_players=["Justin Jefferson"],
    )
    assert not is_valid
    assert len(errors) == 1
    assert "do not own 'Christian McCaffrey'" in errors[0]


def test_validate_trade_ownership_receiving_already_owned_player():
    roster = ParsedRoster(
        team_name="Mad Dawg Team",
        players=[_make_player("Brock Purdy", "QB", 18.0), _make_player("Kyren Williams", "RB", 15.0)],
    )

    # Trying to receive Brock Purdy (already on roster)
    is_valid, errors, matched = validate_trade_roster_ownership(
        roster=roster,
        giving_players=["Kyren Williams"],
        receiving_players=["Brock Purdy"],
    )
    assert not is_valid
    assert len(errors) == 1
    assert "already own 'Brock Purdy'" in errors[0]


def test_evaluate_trade_user_test_scenario_both_invalid():
    """Test user's exact scenario: receiving a player already owned, giving a player not owned."""
    roster = ParsedRoster(
        team_name="Mad Dawg Team",
        players=[
            _make_player("Brock Purdy", "QB", 18.0),
            _make_player("Kyren Williams", "RB", 15.0),
        ],
    )

    result = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["Derrick Henry"],  # NOT owned
        receiving_players=["Brock Purdy"],  # ALREADY owned
    )

    assert result.verdict == "INVALID"
    assert not result.is_valid_trade
    assert len(result.roster_validation_errors) == 2
    assert any("do not own 'Derrick Henry'" in e for e in result.roster_validation_errors)
    assert any("already own 'Brock Purdy'" in e for e in result.roster_validation_errors)


def test_evaluate_trade_roster_context_vacuum_vs_lineup():
    """Demonstrates why 1-to-1 comparison fails and roster-contextual evaluation succeeds.

    Scenario:
    User is stacked at WR (Justin Jefferson 17.0, Amon-Ra 16.0, Malik Nabers 15.0, Zay Flowers 14.5).
    User has Starting RB Kyren Williams (15.0) and Bench RB Rico Dowdle (8.0).
    Trade proposal: Give Kyren Williams (15.0), Receive WR Tee Higgins (14.0).
    In 1-to-1 vacuum: -1.0 pt difference looks modest.
    In roster reality:
      - Tee Higgins (14.0) sits on the bench (behind Jefferson 17.0, Amon-Ra 16.0, Nabers 15.0, Flowers 14.5).
      - Rico Dowdle (8.0) is forced into starting RB slot.
      - Starting lineup drops by -7.0 points!
      - Result: Must REJECT.
    """
    roster = ParsedRoster(
        team_name="Mad Dawg Team",
        players=[
            _make_player("Patrick Mahomes", "QB", 20.0),
            _make_player("Kyren Williams", "RB", 15.0),
            _make_player("James Cook", "RB", 13.0),
            _make_player("Rico Dowdle", "RB", 8.0),
            _make_player("Justin Jefferson", "WR", 17.0),
            _make_player("Amon-Ra St. Brown", "WR", 16.0),
            _make_player("Malik Nabers", "WR", 15.0),
            _make_player("Zay Flowers", "WR", 14.5),
            _make_player("Trey McBride", "TE", 12.0),
            _make_player("49ers D/ST", "DST", 8.0),
        ],
    )

    projections = {"Tee Higgins": ("WR", 14.0)}

    result = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["Kyren Williams"],
        receiving_players=["Tee Higgins"],
        player_projections=projections,
    )

    assert result.is_valid_trade
    assert result.verdict == "REJECT"
    assert result.net_starting_points_change is not None
    assert result.net_starting_points_change <= -5.0
    assert any("does NOT crack your starting lineup" in c for c in result.starting_lineup_changes)


def test_evaluate_trade_consolidation_upgrade_accept():
    """Consolidation trade: 2 bench assets for 1 starting upgrade."""
    roster = ParsedRoster(
        team_name="Mad Dawg Team",
        players=[
            _make_player("Brock Purdy", "QB", 18.0),
            _make_player("Rico Dowdle", "RB", 9.0),
            _make_player("Chuba Hubbard", "RB", 10.0),
            _make_player("Jaylen Warren", "RB", 8.5),
            _make_player("CeeDee Lamb", "WR", 17.0),
            _make_player("Nico Collins", "WR", 15.0),
            _make_player("Brian Thomas Jr.", "WR", 13.0),
            _make_player("Flex WR Piece", "WR", 10.0),
            _make_player("George Kittle", "TE", 12.0),
            _make_player("Ravens D/ST", "DST", 7.0),
        ],
    )

    projections = {
        "Breece Hall": ("RB", 18.0),
    }

    result = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["Flex WR Piece", "Jaylen Warren"],
        receiving_players=["Breece Hall"],
        player_projections=projections,
    )

    assert result.is_valid_trade
    assert result.verdict == "ACCEPT"
    assert result.net_starting_points_change is not None
    assert result.net_starting_points_change >= 5.0
    assert any("Acquired Starter: Breece Hall" in c for c in result.starting_lineup_changes)


def test_compute_optimal_starters():
    players = [
        _make_player("QB1", "QB", 20.0),
        _make_player("QB2", "QB", 15.0),
        _make_player("RB1", "RB", 18.0),
        _make_player("RB2", "RB", 14.0),
        _make_player("RB3", "RB", 10.0),
        _make_player("WR1", "WR", 16.0),
        _make_player("WR2", "WR", 15.0),
        _make_player("WR3", "WR", 13.0),
        _make_player("TE1", "TE", 11.0),
        _make_player("DEF1", "DST", 8.0),
    ]

    starters, bench, total = compute_optimal_starters(players, PNA_2026)
    # PNA 2026: QB:1, RB:2, WR:2, TE:1, FLEX:2, DST:1 (total 9 starters, k=0)
    assert len(starters) == 9
    assert len(bench) == 1
    bench_names = {p.name for p in bench}
    assert "QB2" in bench_names


def test_evaluate_trade_with_gemini_client():
    roster = ParsedRoster(
        team_name="Mad Dawg Team",
        players=[
            _make_player("Good WR", "WR", 13.0),
            _make_player("Brock Purdy", "QB", 18.0),
        ],
    )

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
    assert trade_eval.is_valid_trade
