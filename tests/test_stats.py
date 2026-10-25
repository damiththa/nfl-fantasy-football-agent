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


def test_fetch_player_stats_2026_schema_with_team_and_display_name(monkeypatch):
    """Verify fetch_player_stats handles the 2026 nflreadpy schema ('team', 'player_display_name', no 'games' col)."""
    import polars as pl

    mock_df = pl.DataFrame(
        {
            "player_id": ["00-0039075", "00-0039075"],
            "player_name": ["P.Nacua", "P.Nacua"],
            "player_display_name": ["Puka Nacua", "Puka Nacua"],
            "team": ["LA", "LA"],
            "week": [1, 2],
            "targets": [11, 9],
            "target_share": [0.32, 0.28],
            "receptions": [8, 7],
            "receiving_yards": [110, 95],
            "carries": [1, 0],
            "rushing_yards": [7, 0],
            "air_yards_share": [0.40, 0.36],
            "wopr": [0.76, 0.68],
        }
    )

    monkeypatch.setattr("src.data.stats.nfl.load_player_stats", lambda seasons: mock_df)
    _player_stats_cache.pop(2026, None)

    stats = fetch_player_stats(2026)
    assert "Puka Nacua" in stats
    assert "P.Nacua" in stats
    puka = stats["Puka Nacua"]
    assert puka.games_played == 2
    assert puka.targets == 20
    assert puka.receptions == 15
    assert puka.team == "LA"
    assert round(puka.target_share, 2) == 0.30

    trends = get_player_trends("Puka Nacua", 2026, recent_weeks=2)
    assert trends["player_name"] == "Puka Nacua"
    assert round(trends["recent_target_share"], 2) == 0.30

