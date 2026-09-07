import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ESPN sometimes uses different abbreviations than espn-api or sleeper.
# Map ESPN scoreboard abbreviations to standard if necessary.
_TEAM_ABBR_MAPPING = {
    "WSH": "WAS",
    "JAX": "JAC",
    "LA": "LAR",
}


@dataclass
class GameOdds:
    """Represents game odds and implied totals from the ESPN Scoreboard."""

    game_id: str
    home_team: str
    away_team: str
    spread: float  # Negative means home is favored
    over_under: float
    home_implied_total: float
    away_implied_total: float
    game_time: datetime
    status: str  # pre, in_progress, final


def normalize_team_abbr(abbr: str) -> str:
    """Normalize team abbreviation to standard format."""
    abbr = abbr.upper()
    return _TEAM_ABBR_MAPPING.get(abbr, abbr)


def _calculate_implied_totals(spread: float, over_under: float) -> tuple[float, float]:
    """Calculate implied totals for home and away teams.

    Returns:
        Tuple of (home_implied_total, away_implied_total)
    """
    if over_under <= 0:
        return 0.0, 0.0

    favorite_total = round((over_under + abs(spread)) / 2, 1)
    underdog_total = round((over_under - abs(spread)) / 2, 1)

    if spread <= 0:
        # Home team is favorite or pick'em
        return favorite_total, underdog_total
    else:
        # Away team is favorite
        return underdog_total, favorite_total


def fetch_week_odds() -> list[GameOdds]:
    """Fetch the current week's odds from the ESPN scoreboard.

    Returns:
        A list of GameOdds objects for all games with available odds data.
    """
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()

            events = data.get("events", [])
            odds_list = []

            for event in events:
                try:
                    game_id = event.get("id")

                    # Parse status
                    status_detail = event.get("status", {}).get("type", {})
                    state = status_detail.get("state", "pre")

                    # Parse game time
                    date_str = event.get("date")
                    game_time = (
                        datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        if date_str
                        else datetime.now()
                    )

                    # Parse teams
                    competitions = event.get("competitions", [])
                    if not competitions:
                        continue

                    comp = competitions[0]
                    competitors = comp.get("competitors", [])
                    home_team = ""
                    away_team = ""

                    for team in competitors:
                        abbr = normalize_team_abbr(team.get("team", {}).get("abbreviation", ""))
                        if team.get("homeAway") == "home":
                            home_team = abbr
                        else:
                            away_team = abbr

                    # Parse odds (default to 0.0 if not yet published)
                    odds_data = comp.get("odds", [])
                    spread = 0.0
                    over_under = 0.0
                    if odds_data:
                        primary_odds = odds_data[0]
                        spread = float(primary_odds.get("spread", 0.0))
                        over_under = float(primary_odds.get("overUnder", 0.0))

                    # Calculate implied totals
                    home_total, away_total = _calculate_implied_totals(spread, over_under)

                    odds_list.append(
                        GameOdds(
                            game_id=game_id,
                            home_team=home_team,
                            away_team=away_team,
                            spread=spread,
                            over_under=over_under,
                            home_implied_total=home_total,
                            away_implied_total=away_total,
                            game_time=game_time,
                            status=state,
                        )
                    )
                except (ValueError, TypeError, KeyError) as e:
                    logger.debug(f"Error parsing odds for event {event.get('id')}: {e}")
                    continue

            return odds_list

    except Exception as e:
        logger.error(f"Failed to fetch scoreboard odds from ESPN: {e}")
        return []


def get_player_game_odds(team_abbr: str, odds: list[GameOdds]) -> Optional[GameOdds]:
    """Find the game odds for a specific team.

    Args:
        team_abbr: The team abbreviation to find.
        odds: A list of GameOdds for the current week.

    Returns:
        The GameOdds for the specified team, or None if not found.
    """
    team_abbr = normalize_team_abbr(team_abbr)

    for game in odds:
        if game.home_team == team_abbr or game.away_team == team_abbr:
            return game

    return None
