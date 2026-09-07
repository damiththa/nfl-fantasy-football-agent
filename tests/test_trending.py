from src.data.injuries import PlayerInjuryInfo
from src.data.trending import (
    TrendingPlayer,
    fetch_trending_adds,
    fetch_trending_drops,
)


def test_trending_player_dataclass():
    tp = TrendingPlayer(
        player_id="4034",
        count=15234,
        rank=1,
        full_name="Christian McCaffrey",
        team="SF",
        position="RB",
    )
    assert tp.player_id == "4034"
    assert tp.count == 15234
    assert tp.rank == 1
    assert tp.full_name == "Christian McCaffrey"
    assert tp.team == "SF"
    assert tp.position == "RB"


def test_fetch_trending_resolution(monkeypatch):
    # Mock httpx client to return sample sleeper response
    sample_data = [
        {"player_id": "4034", "count": 1500},
        {"player_id": "8138", "count": 850},
    ]

    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return sample_data

    class MockClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            return MockResponse()

    monkeypatch.setattr("src.data.trending.httpx.Client", MockClient)

    # Without player database
    results_raw = fetch_trending_adds(lookback_hours=24, limit=2)
    assert len(results_raw) == 2
    assert results_raw[0].player_id == "4034"
    assert results_raw[0].full_name is None
    assert results_raw[0].rank == 1
    assert results_raw[1].rank == 2

    # With player database to resolve names
    all_players = {
        "4034": PlayerInjuryInfo(
            player_id="4034",
            full_name="Christian McCaffrey",
            team="SF",
            position="RB",
            injury_status=None,
            practice_participation=None,
            injury_body_part=None,
            injury_notes=None,
            sport="nfl",
            active=True,
        )
    }

    results_resolved = fetch_trending_adds(lookback_hours=24, limit=2, all_players=all_players)
    assert len(results_resolved) == 2
    assert results_resolved[0].full_name == "Christian McCaffrey"
    assert results_resolved[0].team == "SF"
    assert results_resolved[0].position == "RB"
    # Unresolved player
    assert results_resolved[1].full_name is None


def test_fetch_trending_error_handling(monkeypatch):
    class MockErrorClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            raise Exception("Sleeper trending API down")

    monkeypatch.setattr("src.data.trending.httpx.Client", MockErrorClient)

    adds = fetch_trending_adds(24, 10)
    assert adds == []

    drops = fetch_trending_drops(24, 10)
    assert drops == []
