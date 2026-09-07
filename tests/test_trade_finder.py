from unittest.mock import MagicMock

from src.analysis.trade_finder import propose_league_trades
from src.config import PNA_2026
from src.intelligence.gemini_client import GeminiIntelligenceClient


def _make_mock_player(name, pos, slot, pts, injury="NORMAL"):
    p = MagicMock()
    p.name = name
    p.position = pos
    p.lineupSlot = slot
    p.projected_points = pts
    p.points = 0.0
    p.injuryStatus = injury
    p.bye_week = 7
    p.percent_owned = 90.0
    p.proTeam = "KC"
    return p


def test_propose_league_trades_deterministic():
    # User team (team_id=1 in PNA_2026)
    user_team = MagicMock()
    user_team.team_id = PNA_2026.team_id
    user_team.team_name = "Mad Dawg"
    user_team.roster = [
        _make_mock_player("Josh Allen", "QB", "QB", 22.0),
        _make_mock_player("Bijan Robinson", "RB", "RB", 18.0),
        _make_mock_player("Weak Starter WR", "WR", "WR", 7.0),
        _make_mock_player("Trey McBride", "TE", "TE", 12.0),
        _make_mock_player("Bench RB Monster", "RB", "Bench", 15.0),
    ]

    # Opponent team (team_id=2)
    opp_team = MagicMock()
    opp_team.team_id = 2
    opp_team.team_name = "Rival Squad"
    opp_team.owners = [{"firstName": "John", "lastName": "Doe"}]
    opp_team.roster = [
        _make_mock_player("Lamar Jackson", "QB", "QB", 21.0),
        _make_mock_player("Opponent Weak RB", "RB", "RB", 6.0),
        _make_mock_player("CeeDee Lamb", "WR", "WR", 19.0),
        _make_mock_player("Opponent Bench WR Beast", "WR", "Bench", 14.0),
    ]

    mock_league = MagicMock()
    mock_league.teams = [user_team, opp_team]

    report = propose_league_trades(PNA_2026, 1, mock_league)

    assert report.league_id == PNA_2026.league_id
    assert report.week == 1
    assert len(report.proposals) >= 1
    prop = report.proposals[0]
    assert prop.target_team_id == 2
    assert "Bench RB Monster" in prop.giving_players
    assert "Opponent Bench WR Beast" in prop.receiving_players
    assert prop.net_vorp_gain > 0
    assert "John Doe" in prop.negotiation_pitch


def test_propose_league_trades_gemini():
    user_team = MagicMock()
    user_team.team_id = PNA_2026.team_id
    user_team.team_name = "Mad Dawg"
    user_team.roster = [_make_mock_player("Josh Allen", "QB", "QB", 22.0)]

    opp_team = MagicMock()
    opp_team.team_id = 2
    opp_team.team_name = "Opponent"
    opp_team.roster = [_make_mock_player("Lamar Jackson", "QB", "QB", 21.0)]

    mock_league = MagicMock()
    mock_league.teams = [user_team, opp_team]

    class MockModels:
        def generate_content(self, *args, **kwargs):
            class Response:
                text = (
                    '{"league_id": 991059191, "week": 1, "market_overview": "Strong RB market", '
                    '"proposals": [{"target_team_id": 2, "target_team_name": "Opponent", "target_manager": "Manager", '
                    '"giving_players": ["Bench RB"], "receiving_players": ["Target WR"], "net_vorp_gain": 2.5, '
                    '"your_lineup_upgrade": "Upgrades WR", "why_target_accepts": "Needs RB", '
                    '"negotiation_pitch": "Hey let us trade"}]}'
                )
            return Response()

    class MockSdk:
        models = MockModels()

    client = GeminiIntelligenceClient(mock_client=MockSdk())
    report = propose_league_trades(PNA_2026, 1, mock_league, client=client)
    assert len(report.proposals) == 1
    assert report.proposals[0].target_team_id == 2
    assert report.proposals[0].net_vorp_gain == 2.5
