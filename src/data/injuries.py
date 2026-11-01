import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Global cache for the full player database (15-minute TTL so pre-game inactives stay fresh)
_ALL_PLAYERS_CACHE: Optional[dict[str, "PlayerInjuryInfo"]] = None
_ALL_PLAYERS_CACHE_TS: float = 0.0
_CACHE_TTL_SECONDS: float = 900.0

_FANTASY_OFFENSIVE_POSITIONS = {"QB", "RB", "WR", "TE", "K", "PK"}
_TEAM_ABBR_EQUIVALENTS: dict[str, set[str]] = {
    "WAS": {"WAS", "WSH"},
    "WSH": {"WAS", "WSH"},
    "JAX": {"JAX", "JAC"},
    "JAC": {"JAX", "JAC"},
    "LAR": {"LAR", "LA"},
    "LA": {"LAR", "LA"},
}


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
    global _ALL_PLAYERS_CACHE, _ALL_PLAYERS_CACHE_TS

    now_ts = time.monotonic()
    if (
        _ALL_PLAYERS_CACHE is not None
        and not force_refresh
        and (now_ts - _ALL_PLAYERS_CACHE_TS) < _CACHE_TTL_SECONDS
    ):
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
            _ALL_PLAYERS_CACHE_TS = time.monotonic()
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

    def _candidate_rank(p: PlayerInjuryInfo) -> tuple[int, int, int]:
        pos_upper = (p.position or "").upper()
        is_offense = 1 if pos_upper in _FANTASY_OFFENSIVE_POSITIONS else 0
        is_active = 1 if (p.active and bool(p.team)) else (1 if p.active else 0)
        has_inj = 1 if p.injury_status else 0
        return (is_offense, is_active, has_inj)

    # Create mappings for exact case-insensitive names and normalized names
    exact_roster: dict[str, list[PlayerInjuryInfo]] = {}
    normalized_roster: dict[str, list[PlayerInjuryInfo]] = {}
    for p in all_players.values():
        if p.full_name:
            exact_key = p.full_name.strip().lower()
            exact_roster.setdefault(exact_key, []).append(p)
            norm = normalize_name(p.full_name)
            normalized_roster.setdefault(norm, []).append(p)

    results = []
    for name in player_names:
        exact_key = (name or "").strip().lower()
        norm_name = normalize_name(name)
        if exact_key in exact_roster:
            best = max(exact_roster[exact_key], key=_candidate_rank)
            results.append(best)
        elif norm_name in normalized_roster:
            best = max(normalized_roster[norm_name], key=_candidate_rank)
            results.append(best)
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

    team_upper = (team_abbr or "").strip().upper()
    matching_teams = _TEAM_ABBR_EQUIVALENTS.get(team_upper, {team_upper})

    injured_players = [
        player
        for player in all_players.values()
        if (player.team or "").upper() in matching_teams
        and (player.injury_status is not None or player.practice_participation is not None)
    ]

    return injured_players
