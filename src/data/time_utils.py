"""
Time and Timezone Services for NFL Fantasy Football Agent.
Provides a unified, timezone-aware single source of truth across all data, analysis,
notification, and scheduling modules.

Ensures that Daylight Saving Time transitions (EDT -> EST, EST -> EDT), container system
timezones (UTC on Cloud Run), and local user clocks never cause off-by-one-hour or
off-by-five-hour discrepancies.
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Canonical timezones
EASTERN_TZ = zoneinfo.ZoneInfo("America/New_York")
UTC_TZ = timezone.utc


def now_utc() -> datetime:
    """Return the current timezone-aware datetime in UTC."""
    return datetime.now(UTC_TZ)


def now_et() -> datetime:
    """Return the current timezone-aware datetime in America/New_York (Eastern Time).
    Automatically reflects EST (-05:00) in winter and EDT (-04:00) in summer/fall.
    """
    return datetime.now(EASTERN_TZ)


def to_utc(val: datetime | str | int | float | None) -> Optional[datetime]:
    """Convert any datetime, ISO timestamp string, or epoch number to a timezone-aware UTC datetime.

    Handles:
    - None -> None
    - int / float: millisecond epoch (>1e11) or second epoch
    - ISO 8601 string: parses 'Z', '+00:00', '-05:00', or naive ISO
    - datetime:
        - if timezone-aware: converts to UTC
        - if naive: safely assumes UTC (standard for Cloud Run containers, ESPN scoreboard, and epoch timestamps)
    """
    if val is None:
        return None

    if isinstance(val, (int, float)):
        try:
            val_flt = float(val)
            ts = val_flt / 1000.0 if val_flt > 1e11 else val_flt
            return datetime.fromtimestamp(ts, tz=UTC_TZ)
        except (OverflowError, OSError, ValueError) as e:
            logger.debug("Failed converting timestamp %s to UTC: %s", val, e)
            return None

    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return None
        if val_str.endswith("Z") or val_str.endswith("z"):
            val_str = val_str[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(val_str)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC_TZ)
            return dt.astimezone(UTC_TZ)
        except ValueError:
            logger.debug("Failed parsing string %s as ISO datetime", val)
            return None

    if isinstance(val, datetime):
        if val.tzinfo is not None:
            return val.astimezone(UTC_TZ)
        return val.replace(tzinfo=UTC_TZ)

    return None


def to_et(val: datetime | str | int | float | None) -> Optional[datetime]:
    """Convert any datetime, ISO string, or epoch timestamp to timezone-aware Eastern Time (America/New_York).
    Properly determines EST vs EDT based on the target date.
    """
    utc_dt = to_utc(val)
    if utc_dt is None:
        return None
    return utc_dt.astimezone(EASTERN_TZ)


def format_et(
    val: datetime | str | int | float | None,
    fmt: str = "%a %-I:%M %p %Z",
    fallback: str = "TBD",
) -> str:
    """Format any datetime or timestamp in Eastern Time with its dynamic timezone abbreviation (EST or EDT).

    Examples:
        - "Sun 1:00 PM EST" (Nov 1)
        - "Sun 1:00 PM EDT" (Oct 25)
    """
    et_dt = to_et(val)
    if et_dt is None:
        return fallback
    try:
        return et_dt.strftime(fmt)
    except Exception:
        # Fallback for platforms without %-I (e.g. Windows)
        safe_fmt = fmt.replace("%-I", "%I").replace("%-d", "%d")
        return et_dt.strftime(safe_fmt).replace(" 0", " ")


def format_iso(val: datetime | str | int | float | None = None) -> str:
    """Return an ISO 8601 string with an explicit timezone offset (never naive).
    Defaults to current Eastern Time if val is None.
    """
    if val is None:
        return now_et().isoformat()
    et_dt = to_et(val)
    return et_dt.isoformat() if et_dt else now_et().isoformat()


def minutes_until(
    target: datetime | str | int | float | None,
    from_dt: Optional[datetime] = None,
) -> Optional[float]:
    """Calculate signed minutes from from_dt (defaults to now_utc) until target.
    Positive value means target is in the future.
    Negative value means target is in the past (kickoff has passed).
    """
    tgt_utc = to_utc(target)
    if tgt_utc is None:
        return None
    base_utc = to_utc(from_dt) if from_dt is not None else now_utc()
    return round((tgt_utc - base_utc).total_seconds() / 60.0, 1)


def is_kickoff_passed(
    game_datetime: datetime | str | int | float | None,
    buffer_minutes: float = 0.0,
    from_dt: Optional[datetime] = None,
) -> bool:
    """Return True if game kickoff has passed (or is within buffer_minutes of starting)."""
    mins = minutes_until(game_datetime, from_dt=from_dt)
    if mins is None:
        return False
    return mins <= buffer_minutes


def get_imminent_slate_info(
    odds: list[Any],
    window_minutes: float = 120.0,
    from_dt: Optional[datetime] = None,
) -> dict[str, Any]:
    """Categorize weekly game odds into imminent kickoff slates, locked games, and future games.

    Args:
        odds: List of GameOdds objects from fetch_week_odds().
        window_minutes: Window in minutes to consider a kickoff 'imminent' (default 120 mins).
        from_dt: Optional evaluation time (defaults to now_utc).

    Returns:
        dict with:
            - imminent_games: list of GameOdds starting in [0, window_minutes]
            - imminent_teams: set of team abbreviations playing in the imminent window
            - locked_games: list of GameOdds already underway or final (minutes_until <= 0 or status != 'pre')
            - locked_teams: set of team abbreviations whose games are locked
            - next_kickoff_dt: earliest upcoming kickoff datetime (Eastern)
            - minutes_to_next_kickoff: integer minutes until next kickoff
            - slate_label: human-readable slate banner (e.g. "1:00 PM EST Early Slate (in 75m)")
            - has_imminent_slate: bool True if any game starts in [0, window_minutes]
    """
    base_now = from_dt if from_dt is not None else now_utc()
    imminent_games: list[Any] = []
    imminent_teams: set[str] = set()
    locked_games: list[Any] = []
    locked_teams: set[str] = set()
    upcoming_games: list[Any] = []

    for game in odds:
        gt = getattr(game, "game_time", None)
        if gt is None:
            continue

        mins = minutes_until(gt, from_dt=base_now)
        state = str(getattr(game, "status", "pre") or "pre").lower()
        home = str(getattr(game, "home_team", "") or "").upper()
        away = str(getattr(game, "away_team", "") or "").upper()

        if mins is None:
            continue

        # Check if game is already locked or in-progress
        if mins <= 0 or state in ("in_progress", "final", "post"):
            locked_games.append(game)
            if home:
                locked_teams.add(home)
            if away:
                locked_teams.add(away)
        elif 0 < mins <= window_minutes:
            imminent_games.append(game)
            if home:
                imminent_teams.add(home)
            if away:
                imminent_teams.add(away)
            upcoming_games.append((mins, game))
        else:
            upcoming_games.append((mins, game))

    upcoming_games.sort(key=lambda item: item[0])

    next_kickoff_dt: Optional[datetime] = None
    minutes_to_next: Optional[int] = None
    slate_label = "No games kicking off within 2 hours"
    slate_title = "Game Day Alert"

    if upcoming_games:
        earliest_mins, earliest_game = upcoming_games[0]
        minutes_to_next = max(0, int(round(earliest_mins)))
        next_kickoff_dt = to_et(earliest_game.game_time)
        time_str = format_et(next_kickoff_dt, "%-I:%M %p %Z")

        count_in_slate = sum(
            1
            for m, g in upcoming_games
            if abs(m - earliest_mins) <= 45  # Group games starting within 45 mins of each other
        )
        games_term = f"{count_in_slate} game{'s' if count_in_slate > 1 else ''}"
        slate_title = f"{time_str} Slate"
        slate_label = f"{time_str} Kickoff (in {minutes_to_next} mins — {games_term})"

    return {
        "imminent_games": imminent_games,
        "imminent_teams": imminent_teams,
        "locked_games": locked_games,
        "locked_teams": locked_teams,
        "next_kickoff_dt": next_kickoff_dt,
        "minutes_to_next_kickoff": minutes_to_next,
        "slate_label": slate_label,
        "slate_title": slate_title,
        "has_imminent_slate": len(imminent_games) > 0,
    }
