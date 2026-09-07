from src.analysis.lineup import optimize_lineup
from src.config import CHIPS_AHOY, PNA_2026
from src.espn.matchup import MatchupData
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.gemini_client import GeminiIntelligenceClient


def _create_mock_roster(num_rb: int = 4, num_wr: int = 5, injured_rb: bool = False) -> ParsedRoster:
    players = [
        RosterPlayer("Patrick Mahomes", "QB", "KC", "QB", 22.0, 0.0, "NORMAL", 10, 99.0),
        RosterPlayer("Travis Kelce", "TE", "KC", "TE", 14.0, 0.0, "NORMAL", 10, 98.0),
        RosterPlayer("SF Defense", "DST", "SF", "D/ST", 8.0, 0.0, "NORMAL", 9, 90.0),
        RosterPlayer("Harrison Butker", "K", "KC", "K", 9.0, 0.0, "NORMAL", 10, 85.0),
    ]

    for i in range(num_rb):
        status = "OUT" if injured_rb and i == 0 else "NORMAL"
        proj = 18.0 - (i * 3.0)
        players.append(RosterPlayer(f"RB_{i}", "RB", "SF", "RB", proj, 0.0, status, 9, 80.0))

    for i in range(num_wr):
        proj = 17.0 - (i * 2.5)
        players.append(RosterPlayer(f"WR_{i}", "WR", "DAL", "WR", proj, 0.0, "NORMAL", 7, 85.0))

    return ParsedRoster(team_name="Mad Dawg Team", players=players)


def test_game_theory_strategies():
    roster = _create_mock_roster()

    # Heavy Favorite (+15 margin)
    matchup_fav = MatchupData(
        week=1,
        your_team=roster,
        opponent_team=None,
        your_projected=135.0,
        opp_projected=115.0,
        projected_margin=20.0,
        is_favorite=True,
    )
    rec_fav = optimize_lineup(PNA_2026, 1, roster, matchup_fav)
    assert rec_fav.game_theory_strategy == "PROTECT_LEAD"

    # Heavy Underdog (-18 margin)
    matchup_dog = MatchupData(
        week=1,
        your_team=roster,
        opponent_team=None,
        your_projected=100.0,
        opp_projected=125.0,
        projected_margin=-25.0,
        is_favorite=False,
    )
    rec_dog = optimize_lineup(PNA_2026, 1, roster, matchup_dog)
    assert rec_dog.game_theory_strategy == "SEEK_VARIANCE"

    # Close contest (+3 margin)
    matchup_close = MatchupData(
        week=1,
        your_team=roster,
        opponent_team=None,
        your_projected=118.0,
        opp_projected=115.0,
        projected_margin=3.0,
        is_favorite=True,
    )
    rec_close = optimize_lineup(PNA_2026, 1, roster, matchup_close)
    assert rec_close.game_theory_strategy == "BALANCED"


def test_lineup_respects_roster_slots_pna_vs_chips():
    roster = _create_mock_roster()

    # PNA 2026 has 2 FLEX slots and NO Kicker (total starters = 9: 1 QB, 2 RB, 2 WR, 1 TE, 2 FLEX, 1 DST)
    rec_pna = optimize_lineup(PNA_2026, 1, roster)
    starters_pna = rec_pna.recommended_starters
    assert len(starters_pna) == PNA_2026.roster.total_starters
    assert not any(p.position == "K" for p in starters_pna)

    # Chips Ahoy has 1 FLEX slot and 1 Kicker (total starters = 9: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 DST, 1 K)
    rec_chips = optimize_lineup(CHIPS_AHOY, 1, roster)
    starters_chips = rec_chips.recommended_starters
    assert len(starters_chips) == CHIPS_AHOY.roster.total_starters
    assert any(p.position == "K" for p in starters_chips)


def test_starter_ordering_pna_and_chips():
    roster = _create_mock_roster()

    # PNA 2026: QB, RB, RB, WR, WR, TE, FLEX, FLEX, D/ST
    rec_pna = optimize_lineup(PNA_2026, 1, roster)
    pna_positions = [p.position for p in rec_pna.recommended_starters]
    assert pna_positions == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "D/ST"]

    # Chips Ahoy: QB, RB, RB, WR, WR, TE, FLEX, D/ST, K
    rec_chips = optimize_lineup(CHIPS_AHOY, 1, roster)
    chips_positions = [p.position for p in rec_chips.recommended_starters]
    assert chips_positions == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "D/ST", "K"]


def test_benches_injured_players():
    # RB_0 is marked OUT
    roster = _create_mock_roster(injured_rb=True)
    rec = optimize_lineup(PNA_2026, 1, roster)

    # RB_0 must not be in starters
    starter_names = {p.player_name for p in rec.recommended_starters}
    assert "RB_0" not in starter_names

    # RB_0 should be in bench
    bench_names = {p.player_name for p in rec.bench_players}
    assert "RB_0" in bench_names


def test_optimize_lineup_with_gemini_client():
    roster = _create_mock_roster()

    class MockModels:
        def generate_content(self, *args, **kwargs):
            class Response:
                text = (
                    '{"league_id": 991059191, "week": 1, "game_theory_strategy": "BALANCED", '
                    '"strategy_reasoning": "Tight projected matchup", '
                    '"recommended_starters": [{"player_name": "Patrick Mahomes", "position": "QB", "team": "KC", "action": "START", "confidence": 0.9, "floor": 18.0, "ceiling": 30.0, "projected_points": 22.0, "reasoning": "Elite QB"}], '
                    '"bench_players": [], "key_flex_decisions": []}'
                )

            return Response()

    class MockSdk:
        models = MockModels()

    client = GeminiIntelligenceClient(mock_client=MockSdk())
    rec = optimize_lineup(PNA_2026, 1, roster, client=client)
    assert rec.league_id == 991059191
    assert rec.recommended_starters[0].player_name == "Patrick Mahomes"
