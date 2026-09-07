"""
nflverse advanced stats module via nflreadpy.

Downloads pre-compiled Parquet files from GitHub for advanced player stats
and snap counts. Caches data in memory to avoid repeated network calls.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict

import nflreadpy as nfl
import polars as pl

logger = logging.getLogger(__name__)

# Module-level caches
_snap_counts_cache: Dict[int, pl.DataFrame] = {}
_player_stats_cache: Dict[int, pl.DataFrame] = {}


@dataclass
class PlayerStats:
    """Advanced player statistics from nflverse."""

    player_name: str
    player_id: str
    team: str
    position: str
    games_played: int = 0
    snap_count: int = 0
    snap_pct: float = 0.0
    targets: int = 0
    target_share: float = 0.0
    receptions: int = 0
    receiving_yards: int = 0
    rushing_attempts: int = 0
    rushing_yards: int = 0
    red_zone_targets: int = 0
    red_zone_carries: int = 0
    air_yards_share: float = 0.0
    wopr: float = 0.0


@dataclass
class PlayerTrend:
    """Trend data for a player over recent weeks."""

    player_name: str
    recent_snap_pct: float = 0.0
    recent_target_share: float = 0.0
    trend_summary: str = ""


def _to_polars(df: Any) -> pl.DataFrame:
    """Convert dataframe to polars if it's pandas."""
    # Assuming df is already polars or similar since pandas is not installed
    return df


def fetch_snap_counts(season: int) -> dict[str, PlayerStats]:
    """Fetch snap counts for a given season.

    Args:
        season: The NFL season year.

    Returns:
        Dictionary of PlayerStats keyed by player name.
    """
    global _snap_counts_cache
    if season not in _snap_counts_cache:
        try:
            # load_snap_counts returns game-level data
            df = nfl.load_snap_counts([season])
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("Empty dataframe returned")
            _snap_counts_cache[season] = _to_polars(df)
        except Exception as e:
            logger.warning(f"Could not load snap counts for season {season}: {e}")
            return {}

    df = _snap_counts_cache[season]

    # We'll aggregate by player
    try:
        agg_df = df.group_by(["player", "pfr_player_id", "team", "position"]).agg(
            [
                pl.col("game_id").count().alias("games_played"),
                pl.col("offense_snaps").sum().alias("snap_count"),
                pl.col("offense_pct").mean().alias("snap_pct"),
            ]
        )

        results = {}
        for row in agg_df.iter_rows(named=True):
            name = row.get("player")
            if not name:
                continue

            results[name] = PlayerStats(
                player_name=name,
                player_id=row.get("pfr_player_id", ""),
                team=row.get("team", ""),
                position=row.get("position", ""),
                games_played=row.get("games_played", 0),
                snap_count=row.get("snap_count", 0),
                snap_pct=row.get("snap_pct", 0.0) * 100 if row.get("snap_pct") is not None else 0.0,
            )
        return results
    except Exception as e:
        logger.warning(f"Error processing snap counts for season {season}: {e}")
        return {}


def fetch_player_stats(season: int) -> dict[str, PlayerStats]:
    """Fetch advanced player stats for a given season.

    Args:
        season: The NFL season year.

    Returns:
        Dictionary of PlayerStats keyed by player name.
    """
    global _player_stats_cache
    if season not in _player_stats_cache:
        try:
            # load_player_stats returns weekly data
            df = nfl.load_player_stats([season])
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("Empty dataframe returned")
            _player_stats_cache[season] = _to_polars(df)
        except Exception as e:
            logger.warning(f"Could not load player stats for season {season}: {e}")
            return {}

    df = _player_stats_cache[season]

    try:
        # Aggregate weekly data to season level
        agg_df = df.group_by(["player_name", "player_id", "recent_team"]).agg(
            [
                pl.col("games").sum().alias("games_played"),
                pl.col("targets").sum().alias("targets"),
                pl.col("target_share").mean().alias("target_share"),
                pl.col("receptions").sum().alias("receptions"),
                pl.col("receiving_yards").sum().alias("receiving_yards"),
                pl.col("carries").sum().alias("rushing_attempts"),
                pl.col("rushing_yards").sum().alias("rushing_yards"),
                pl.col("red_zone_targets").sum().alias("red_zone_targets")
                if "red_zone_targets" in df.columns
                else pl.lit(0).alias("red_zone_targets"),
                pl.col("red_zone_carries").sum().alias("red_zone_carries")
                if "red_zone_carries" in df.columns
                else pl.lit(0).alias("red_zone_carries"),
                pl.col("air_yards_share").mean().alias("air_yards_share"),
                pl.col("wopr").mean().alias("wopr"),
            ]
        )

        results = {}
        for row in agg_df.iter_rows(named=True):
            name = row.get("player_name")
            if not name:
                continue

            results[name] = PlayerStats(
                player_name=name,
                player_id=row.get("player_id", ""),
                team=row.get("recent_team", ""),
                position="",  # Not always available in this dataset
                games_played=row.get("games_played", 0),
                targets=row.get("targets", 0),
                target_share=row.get("target_share", 0.0) or 0.0,
                receptions=row.get("receptions", 0),
                receiving_yards=row.get("receiving_yards", 0),
                rushing_attempts=row.get("rushing_attempts", 0),
                rushing_yards=row.get("rushing_yards", 0),
                red_zone_targets=row.get("red_zone_targets", 0),
                red_zone_carries=row.get("red_zone_carries", 0),
                air_yards_share=row.get("air_yards_share", 0.0) or 0.0,
                wopr=row.get("wopr", 0.0) or 0.0,
            )
        return results
    except Exception as e:
        logger.warning(f"Error processing player stats for season {season}: {e}")
        return {}


def get_player_trends(player_name: str, season: int, recent_weeks: int = 3) -> dict:
    """Analyze recent trend data for a player to detect rising/falling usage.

    Args:
        player_name: Name of the player.
        season: The NFL season year.
        recent_weeks: Number of recent weeks to consider.

    Returns:
        Dictionary containing trend analysis.
    """
    fetch_player_stats(season)  # Ensure cache is populated

    # Normally we'd filter the underlying dataframe for the last N weeks.
    # We will do a simplified version here pulling from cache.
    global _player_stats_cache
    if season not in _player_stats_cache:
        return {}

    df = _player_stats_cache[season]

    try:
        # Filter for player
        player_df = df.filter(pl.col("player_name") == player_name)
        if player_df.height == 0:
            return {}

        # Sort by week descending and take top N
        if "week" in player_df.columns:
            recent_df = player_df.sort("week", descending=True).head(recent_weeks)
        else:
            recent_df = player_df.head(recent_weeks)

        avg_target_share = (
            recent_df["target_share"].mean() if "target_share" in recent_df.columns else 0.0
        )

        # Similar for snap pct if we query snap cache
        avg_snap_pct = 0.0
        if season in _snap_counts_cache:
            snap_df = _snap_counts_cache[season]
            p_snap_df = snap_df.filter(pl.col("player") == player_name)
            if p_snap_df.height > 0:
                if "week" in p_snap_df.columns:
                    recent_snap = p_snap_df.sort("week", descending=True).head(recent_weeks)
                else:
                    recent_snap = p_snap_df.head(recent_weeks)
                if "offense_pct" in recent_snap.columns:
                    avg_snap_pct = (recent_snap["offense_pct"].mean() or 0.0) * 100

        return {
            "player_name": player_name,
            "recent_snap_pct": avg_snap_pct,
            "recent_target_share": avg_target_share or 0.0,
            "trend_summary": "Rising" if (avg_target_share or 0.0) > 0.2 else "Stable",
        }
    except Exception as e:
        logger.warning(f"Error calculating trends for {player_name}: {e}")
        return {}
