import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from src.data.injuries import PlayerInjuryInfo

logger = logging.getLogger(__name__)


@dataclass
class TrendingPlayer:
    """Represents a trending player (add or drop) on Sleeper."""

    player_id: str
    count: int
    rank: int
    full_name: Optional[str] = None
    team: Optional[str] = None
    position: Optional[str] = None


def _fetch_trending(
    endpoint_type: str,
    lookback_hours: int,
    limit: int,
    all_players: Optional[dict[str, PlayerInjuryInfo]] = None,
) -> list[TrendingPlayer]:
    """Internal helper to fetch trending data from Sleeper."""
    url = f"https://api.sleeper.app/v1/players/nfl/trending/{endpoint_type}?lookback_hours={lookback_hours}&limit={limit}"

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()

            results = []
            for rank, item in enumerate(data, start=1):
                player_id = item.get("player_id")
                count = item.get("count", 0)

                player = TrendingPlayer(player_id=player_id, count=count, rank=rank)

                # Resolve player name if database is provided
                if all_players and player_id in all_players:
                    p_info = all_players[player_id]
                    player.full_name = p_info.full_name
                    player.team = p_info.team
                    player.position = p_info.position

                results.append(player)

            return results

    except Exception as e:
        logger.error(f"Failed to fetch trending {endpoint_type}s from Sleeper: {e}")
        return []


def fetch_trending_adds(
    lookback_hours: int = 24,
    limit: int = 25,
    all_players: Optional[dict[str, PlayerInjuryInfo]] = None,
) -> list[TrendingPlayer]:
    """Fetch the top trending player additions on Sleeper.

    Args:
        lookback_hours: Time window in hours (default: 24).
        limit: Number of results to return (default: 25).
        all_players: Optional full player dict to resolve names.

    Returns:
        A list of TrendingPlayer objects representing the most added players.
    """
    return _fetch_trending("add", lookback_hours, limit, all_players)


def fetch_trending_drops(
    lookback_hours: int = 24,
    limit: int = 25,
    all_players: Optional[dict[str, PlayerInjuryInfo]] = None,
) -> list[TrendingPlayer]:
    """Fetch the top trending player drops on Sleeper.

    Args:
        lookback_hours: Time window in hours (default: 24).
        limit: Number of results to return (default: 25).
        all_players: Optional full player dict to resolve names.

    Returns:
        A list of TrendingPlayer objects representing the most dropped players.
    """
    return _fetch_trending("drop", lookback_hours, limit, all_players)
