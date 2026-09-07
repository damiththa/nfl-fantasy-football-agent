from datetime import datetime
from unittest.mock import MagicMock

import httpx

from src.data.vegas import (
    GameOdds,
    _calculate_implied_totals,
    fetch_week_odds,
    get_player_game_odds,
    normalize_team_abbr,
)


def test_calculate_implied_totals_home_favorite():
    # Home favored by 6.5, O/U 48.5
    # Favorite: (48.5 + 6.5) / 2 = 27.5
    # Underdog: (48.5 - 6.5) / 2 = 21.0
    home, away = _calculate_implied_totals(-6.5, 48.5)
    assert home == 27.5
    assert away == 21.0


def test_calculate_implied_totals_away_favorite():
    # Away favored (spread is +3.0 from home perspective), O/U 47.0
    # Away total: (47.0 + 3.0) / 2 = 25.0
    # Home total: (47.0 - 3.0) / 2 = 22.0
    home, away = _calculate_implied_totals(3.0, 47.0)
    assert home == 22.0
    assert away == 25.0


def test_calculate_implied_totals_pickem():
    # Pick'em game: spread 0.0, O/U 44.0
    home, away = _calculate_implied_totals(0.0, 44.0)
    assert home == 22.0
    assert away == 22.0


def test_calculate_implied_totals_invalid():
    home, away = _calculate_implied_totals(-3.5, 0.0)
    assert home == 0.0
    assert away == 0.0

    home, away = _calculate_implied_totals(-3.5, -10.0)
    assert home == 0.0
    assert away == 0.0


def test_normalize_team_abbr():
    assert normalize_team_abbr("wsh") == "WAS"
    assert normalize_team_abbr("JAX") == "JAC"
    assert normalize_team_abbr("LA") == "LAR"
    assert normalize_team_abbr("KC") == "KC"


def test_get_player_game_odds():
    odds_list = [
        GameOdds(
            game_id="401671789",
            home_team="KC",
            away_team="BAL",
            spread=-3.0,
            over_under=46.5,
            home_implied_total=24.8,
            away_implied_total=21.8,
            game_time=datetime.now(),
            status="pre",
        ),
        GameOdds(
            game_id="401671790",
            home_team="PHI",
            away_team="GB",
            spread=-2.5,
            over_under=49.0,
            home_implied_total=25.8,
            away_implied_total=23.2,
            game_time=datetime.now(),
            status="pre",
        ),
    ]

    # Find home team
    kc_odds = get_player_game_odds("KC", odds_list)
    assert kc_odds is not None
    assert kc_odds.home_team == "KC"
    assert kc_odds.spread == -3.0

    # Find away team
    bal_odds = get_player_game_odds("BAL", odds_list)
    assert bal_odds is not None
    assert bal_odds.away_team == "BAL"

    # Team not playing this week
    mia_odds = get_player_game_odds("MIA", odds_list)
    assert mia_odds is None


def test_fetch_week_odds_with_empty_odds(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "events": [
            {
                "id": "401671800",
                "date": "2026-09-13T17:00Z",
                "status": {"type": {"state": "pre"}},
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"abbreviation": "DET"}},
                            {"homeAway": "away", "team": {"abbreviation": "LAR"}},
                        ],
                        "odds": [],  # Odds not yet published
                    }
                ],
            }
        ]
    }
    mock_resp.raise_for_status.return_value = None

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return mock_resp

    monkeypatch.setattr(httpx, "Client", MockClient)
    odds = fetch_week_odds()
    assert len(odds) == 1
    assert odds[0].home_team == "DET"
    assert odds[0].away_team == "LAR"
    assert odds[0].spread == 0.0
    assert odds[0].over_under == 0.0
