from datetime import datetime

from src.data.weather import classify_conditions, fetch_game_weather, load_stadiums


def test_classify_conditions_clear():
    assert classify_conditions(70.0, 10.0, 15.0, 10.0) == "Clear"


def test_classify_conditions_cold():
    assert classify_conditions(25.0, 10.0, 15.0, 10.0) == "Cold"


def test_classify_conditions_windy():
    assert classify_conditions(70.0, 25.0, 35.0, 10.0) == "Windy"


def test_classify_conditions_rain():
    assert classify_conditions(70.0, 10.0, 15.0, 80.0) == "Rain Risk"


def test_classify_conditions_combined():
    # Cold and windy
    assert classify_conditions(20.0, 25.0, 30.0, 10.0) == "Cold & Windy"
    # Cold and rain (snow)
    assert classify_conditions(20.0, 10.0, 15.0, 80.0) == "Cold & Rain/Snow Risk"


def test_load_stadiums():
    stadiums = load_stadiums()
    assert isinstance(stadiums, dict)
    assert len(stadiums) > 0

    # Check that required fields exist
    for team, data in stadiums.items():
        assert "stadium_name" in data
        assert "city" in data
        assert "latitude" in data
        assert "longitude" in data
        assert "is_dome" in data


def test_fetch_game_weather_dome(monkeypatch):
    # ATL plays in a dome
    weather = fetch_game_weather("ATL", datetime.now())
    assert weather.is_dome is True
    assert weather.conditions_summary == "Indoor"
    assert weather.stadium_name == "Mercedes-Benz Stadium"


def test_fetch_game_weather_api_error(monkeypatch):
    # Mock httpx.Client to raise an error
    class MockClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, *args, **kwargs):
            raise Exception("API Error")

    monkeypatch.setattr("src.data.weather.httpx.Client", MockClient)

    weather = fetch_game_weather("BAL", datetime.now())
    assert weather.is_dome is False
    assert weather.conditions_summary == "API Error"


def test_build_roster_weather_map_away_game_in_dome():
    from src.data.vegas import GameOdds
    from src.data.weather import build_roster_weather_map
    from src.espn.roster import RosterPlayer

    # GB (outdoor) plays away at DET (dome)
    gb_player = RosterPlayer("Jordan Love", "QB", "GB", "QB", 18.0)
    det_player = RosterPlayer("Amon-Ra St. Brown", "WR", "DET", "WR", 16.0)

    odds = [
        GameOdds(
            game_id="401",
            home_team="DET",
            away_team="GB",
            spread=-3.0,
            over_under=48.5,
            home_implied_total=25.8,
            away_implied_total=22.8,
            game_time=datetime(2026, 11, 26, 12, 30),
            status="pre",
        )
    ]

    weather_map = build_roster_weather_map([gb_player, det_player], odds=odds)

    # Both GB (away) and DET (home) should map to Ford Field (Indoor / Dome)
    assert "GB" in weather_map
    assert "DET" in weather_map
    assert weather_map["GB"].is_dome is True
    assert weather_map["GB"].stadium_name == "Ford Field"
    assert weather_map["GB"].conditions_summary == "Indoor"
    assert weather_map["DET"].is_dome is True
    assert weather_map["DET"].stadium_name == "Ford Field"


def test_build_roster_weather_map_no_odds_fallback():
    from src.data.weather import build_roster_weather_map
    from src.espn.roster import RosterPlayer

    # ATL is a dome team
    atl_player = RosterPlayer("Bijan Robinson", "RB", "ATL", "RB", 17.0)
    weather_map = build_roster_weather_map([atl_player], odds=None)

    assert "ATL" in weather_map
    assert weather_map["ATL"].is_dome is True
    assert weather_map["ATL"].stadium_name == "Mercedes-Benz Stadium"


def test_build_roster_weather_map_handles_empty_or_bye():
    from src.data.weather import build_roster_weather_map
    from src.espn.roster import RosterPlayer

    players = [
        RosterPlayer("Free Agent", "RB", "FA", "Bench", 0.0),
        RosterPlayer("Bye Player", "WR", "BYE", "Bench", 0.0),
        RosterPlayer("No Team", "TE", "", "Bench", 0.0),
    ]
    weather_map = build_roster_weather_map(players)
    assert len(weather_map) == 0

