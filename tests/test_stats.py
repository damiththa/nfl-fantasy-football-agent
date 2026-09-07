from src.data.stats import (
    PlayerTrend,
    _player_stats_cache,
    _snap_counts_cache,
    fetch_player_stats,
    fetch_snap_counts,
    get_player_trends,
)


def test_player_trend_dataclass():
    tp = PlayerTrend(
        player_name="Justin Jefferson",
        recent_snap_pct=95.5,
        recent_target_share=0.35,
        trend_summary="Rising",
    )
    assert tp.player_name == "Justin Jefferson"
    assert tp.recent_snap_pct == 95.5
    assert tp.recent_target_share == 0.35
    assert tp.trend_summary == "Rising"


def test_fetch_snap_counts_empty_response(monkeypatch):
    def mock_load_snap_counts(*args, **kwargs):
        return None

    monkeypatch.setattr("src.data.stats.nfl.load_snap_counts", mock_load_snap_counts)

    # Ensure cache is clear
    if 2099 in _snap_counts_cache:
        del _snap_counts_cache[2099]

    stats = fetch_snap_counts(2099)
    assert stats == {}


def test_fetch_player_stats_empty_response(monkeypatch):
    def mock_load_player_stats(*args, **kwargs):
        return None

    monkeypatch.setattr("src.data.stats.nfl.load_player_stats", mock_load_player_stats)

    if 2099 in _player_stats_cache:
        del _player_stats_cache[2099]

    stats = fetch_player_stats(2099)
    assert stats == {}


def test_get_player_trends_no_data():
    if 2099 in _player_stats_cache:
        del _player_stats_cache[2099]
    if 2099 in _snap_counts_cache:
        del _snap_counts_cache[2099]

    trends = get_player_trends("Unknown Player", 2099)
    assert trends == {}
