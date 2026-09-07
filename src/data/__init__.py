"""
Data fetching modules for the NFL Fantasy Football Agent.
Provides utilities for fetching injuries, trending players, Vegas odds,
advanced stats, and game-day weather.
"""

from src.data.injuries import (
    PlayerInjuryInfo,
    fetch_all_players,
    get_injury_report,
    get_team_injuries,
    normalize_name,
)
from src.data.stats import (
    PlayerStats,
    PlayerTrend,
    fetch_player_stats,
    fetch_snap_counts,
    get_player_trends,
)
from src.data.trending import (
    TrendingPlayer,
    fetch_trending_adds,
    fetch_trending_drops,
)
from src.data.vegas import (
    GameOdds,
    fetch_week_odds,
    get_player_game_odds,
)
from src.data.weather import (
    GameWeather,
    classify_conditions,
    fetch_game_weather,
    load_stadiums,
)

__all__ = [
    # Injuries
    "PlayerInjuryInfo",
    "fetch_all_players",
    "get_injury_report",
    "get_team_injuries",
    "normalize_name",
    # Trending
    "TrendingPlayer",
    "fetch_trending_adds",
    "fetch_trending_drops",
    # Vegas
    "GameOdds",
    "fetch_week_odds",
    "get_player_game_odds",
    # Stats
    "PlayerStats",
    "PlayerTrend",
    "fetch_snap_counts",
    "fetch_player_stats",
    "get_player_trends",
    # Weather
    "GameWeather",
    "classify_conditions",
    "fetch_game_weather",
    "load_stadiums",
]
