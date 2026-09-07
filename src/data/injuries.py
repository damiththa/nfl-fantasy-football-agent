import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Global cache for the full player database
_ALL_PLAYERS_CACHE: Optional[dict[str, "PlayerInjuryInfo"]] = None


@dataclass
class PlayerInjuryInfo:
    """Represents a player's injury and practice status from Sleeper."""

    player_id: str
    full_name: str
    team: Optional[str]
    position: Optional[str]
    injury_status: Optional[str]  # 'Questionable', 'Doubtful', 'Out', 'IR', 'PUP', None
    practice_participation: Optional[
        str
    ]  # 'Full Participation', 'Limited Participation', 'Did Not Participate', None
    injury_body_part: Optional[str]
    injury_notes: Optional[str]
    sport: str
    active: bool


def normalize_name(name: str) -> str:
    """Normalize a player name for fuzzy matching.

    Strips suffixes like Jr., Sr., II, III, IV and converts to lowercase.
    """
    if not name:
        return ""

    # Remove punctuation
    name = re.sub(r"[.\']", "", name)
    # Convert to lowercase
    name = name.lower().strip()
    # Remove common suffixes
    suffixes = [r"\bjr\b", r"\bsr\b", r"\bii\b", r"\biii\b", r"\biv\b"]
    for suffix in suffixes:
        name = re.sub(suffix, "", name)
    # Replace multiple spaces with a single space
    name = re.sub(r"\s+", " ", name).strip()

    return name


def fetch_all_players(force_refresh: bool = False) -> dict[str, PlayerInjuryInfo]:
    """Fetch the full NFL player database from Sleeper.

    This payload is large (~5MB JSON, ~10K players). Results are cached in memory.

    Args:
        force_refresh: If True, bypass the cache and fetch fresh data.

    Returns:
        A dictionary mapping Sleeper player IDs to PlayerInjuryInfo objects.
    """
    global _ALL_PLAYERS_CACHE

    if _ALL_PLAYERS_CACHE is not None and not force_refresh:
        return _ALL_PLAYERS_CACHE

    url = "https://api.sleeper.app/v1/players/nfl"

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()

            players: dict[str, PlayerInjuryInfo] = {}
            for pid, pdata in data.items():
                if not isinstance(pdata, dict):
                    continue

                # Only include valid NFL players
                if pdata.get("sport") != "nfl":
                    continue

                full_name = (
                    pdata.get("full_name")
                    or f"{pdata.get('first_name', '')} {pdata.get('last_name', '')}".strip()
                )

                players[pid] = PlayerInjuryInfo(
                    player_id=pid,
                    full_name=full_name,
                    team=pdata.get("team"),
                    position=pdata.get("position"),
                    injury_status=pdata.get("injury_status"),
                    practice_participation=pdata.get("practice_participation"),
                    injury_body_part=pdata.get("injury_body_part"),
                    injury_notes=pdata.get("injury_notes"),
                    sport=pdata.get("sport", "nfl"),
                    active=pdata.get("active", False),
                )

            _ALL_PLAYERS_CACHE = players
            return players

    except Exception as e:
        logger.error(f"Failed to fetch player data from Sleeper: {e}")
        return _ALL_PLAYERS_CACHE or {}


def get_injury_report(
    player_names: list[str], all_players: Optional[dict[str, PlayerInjuryInfo]] = None
) -> list[PlayerInjuryInfo]:
    """Get injury information for a specific list of player names.

    Args:
        player_names: List of player names (e.g., from an ESPN roster).
        all_players: Optional full player dict. If None, it will be fetched.

    Returns:
        A list of PlayerInjuryInfo objects for the requested players.
    """
    if all_players is None:
        all_players = fetch_all_players()

    if not all_players:
        return []

    # Create a mapping of normalized names to player objects
    normalized_roster = {}
    for p in all_players.values():
        if p.full_name:
            norm = normalize_name(p.full_name)
            # Prefer active players or players with injury status if duplicate names exist
            if norm not in normalized_roster or (p.active or p.injury_status):
                normalized_roster[norm] = p

    results = []
    for name in player_names:
        norm_name = normalize_name(name)
        if norm_name in normalized_roster:
            results.append(normalized_roster[norm_name])
        else:
            logger.debug(f"Could not find Sleeper player info for: {name}")

    return results


def get_team_injuries(
    team_abbr: str, all_players: Optional[dict[str, PlayerInjuryInfo]] = None
) -> list[PlayerInjuryInfo]:
    """Get all currently injured players on a specific NFL team.

    Args:
        team_abbr: The abbreviation of the NFL team (e.g., 'KC', 'SF').
        all_players: Optional full player dict. If None, it will be fetched.

    Returns:
        A list of injured PlayerInjuryInfo objects on the given team.
    """
    if all_players is None:
        all_players = fetch_all_players()

    team_upper = team_abbr.upper()

    injured_players = [
        player
        for player in all_players.values()
        if player.team == team_upper
        and (player.injury_status is not None or player.practice_participation is not None)
    ]

    return injured_players
