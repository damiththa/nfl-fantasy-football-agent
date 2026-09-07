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
