"""
Weather module for the NFL Fantasy Football Agent.

Uses Open-Meteo API to fetch game day weather conditions based on stadium coordinates.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

STADIUMS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "stadiums.json"


@dataclass
class GameWeather:
    """Weather conditions for an NFL game."""

    stadium_name: str
    city: str
    is_dome: bool
    temperature_f: Optional[float] = None
    wind_speed_mph: Optional[float] = None
    wind_gust_mph: Optional[float] = None
    precipitation_prob: Optional[float] = None
    conditions_summary: str = "Unknown"


def load_stadiums() -> Dict[str, Dict[str, Any]]:
    """Load stadium data from JSON file.

    Returns:
        Dictionary of stadium data keyed by team abbreviation.
    """
    try:
        with open(STADIUMS_FILE, "r") as f:
            data = json.load(f)

        stadiums = {}
        for stadium in data:
            for team in stadium.get("team", []):
                stadiums[team] = stadium
        return stadiums
    except Exception as e:
        logger.error(f"Failed to load stadiums.json: {e}")
        return {}


def classify_conditions(
    temp_f: Optional[float],
    wind_mph: Optional[float],
    wind_gust_mph: Optional[float],
    precip_prob: Optional[float],
) -> str:
    """Classify weather conditions into a human-readable summary."""
    conditions = []

    is_cold = temp_f is not None and temp_f < 32
    is_windy = wind_mph is not None and wind_mph > 20
    is_rain_risk = precip_prob is not None and precip_prob > 50

    if is_cold:
        conditions.append("Cold")
    if is_rain_risk:
        if is_cold:
            conditions.append("Rain/Snow Risk")
        else:
            conditions.append("Rain Risk")
    if is_windy:
        conditions.append("Windy")

    if not conditions:
        return "Clear"

    return " & ".join(conditions)


def fetch_game_weather(team_abbr: str, game_datetime: datetime) -> GameWeather:
    """Fetch weather for a game based on the home team's stadium.

    Args:
        team_abbr: Abbreviation of the home team.
        game_datetime: Datetime of the game.

    Returns:
        GameWeather object with forecast data.
    """
    stadiums = load_stadiums()
    stadium = stadiums.get(team_abbr)

    if not stadium:
        logger.warning(f"Stadium not found for team {team_abbr}")
        return GameWeather(
            stadium_name="Unknown", city="Unknown", is_dome=False, conditions_summary="Unknown"
        )

    name = stadium.get("stadium_name", "Unknown")
    city = stadium.get("city", "Unknown")
    is_dome = stadium.get("is_dome", False)

    if is_dome:
        return GameWeather(stadium_name=name, city=city, is_dome=True, conditions_summary="Indoor")

    lat = stadium.get("latitude")
    lon = stadium.get("longitude")

    if lat is None or lon is None:
        return GameWeather(
            stadium_name=name,
            city=city,
            is_dome=False,
            conditions_summary="Unknown (Missing Coordinates)",
        )

    # Fetch from Open-Meteo
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "America/New_York",
        "forecast_days": 7,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])

        # Match game_datetime to closest forecast hour
        target_idx = 0
        target_str = game_datetime.strftime("%Y-%m-%dT%H:00")
        if target_str in times:
            target_idx = times.index(target_str)
        elif times:
            try:
                target_dt = game_datetime.replace(minute=0, second=0, microsecond=0, tzinfo=None)
                diffs = [
                    abs((datetime.fromisoformat(t) - target_dt).total_seconds()) for t in times
                ]
                target_idx = diffs.index(min(diffs))
            except Exception:
                target_idx = 0

        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        gusts = hourly.get("wind_gusts_10m", [])
        precips = hourly.get("precipitation_probability", [])

        temp = temps[target_idx] if target_idx < len(temps) else None
        wind = winds[target_idx] if target_idx < len(winds) else None
        gust = gusts[target_idx] if target_idx < len(gusts) else None
        precip = precips[target_idx] if target_idx < len(precips) else None

        summary = classify_conditions(temp, wind, gust, precip)

        return GameWeather(
            stadium_name=name,
            city=city,
            is_dome=False,
            temperature_f=temp,
            wind_speed_mph=wind,
            wind_gust_mph=gust,
            precipitation_prob=precip,
            conditions_summary=summary,
        )

    except Exception as e:
        logger.warning(f"Failed to fetch weather for {team_abbr} at {name}: {e}")
        return GameWeather(
            stadium_name=name, city=city, is_dome=False, conditions_summary="API Error"
        )
