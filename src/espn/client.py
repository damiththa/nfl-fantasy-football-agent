"""
ESPN League Connection & Auth.
"""

from __future__ import annotations

import logging
from typing import Dict

from espn_api.football import League

from src.config import ALL_LEAGUES, LeagueConfig, get_espn_credentials

logger = logging.getLogger(__name__)

class LeagueClient:
    """Manages connections to ESPN fantasy football leagues."""
    
    def __init__(self) -> None:
        """Initialize the client with credentials from environment variables."""
        try:
            self.espn_s2, self.swid = get_espn_credentials()
        except EnvironmentError as e:
            logger.error("Failed to load ESPN credentials: %s", e)
            raise
            
        self._leagues: Dict[int, League] = {}
        
    def get_league(self, config: LeagueConfig) -> League:
        """Get the espn_api.football.League object for the given config.
        
        Args:
            config: The LeagueConfig object defining the league to connect to.
            
        Returns:
            The connected espn_api.football.League instance.
            
        Raises:
            ConnectionError: If authentication fails (e.g. expired cookies).
        """
        if config.league_id in self._leagues:
            return self._leagues[config.league_id]
            
        try:
            # We initialize the espn_api League which makes a request to fetch league data
            league = League(
                league_id=config.league_id,
                year=config.season,
                espn_s2=self.espn_s2,
                swid=self.swid
            )
            self._leagues[config.league_id] = league
            return league
        except Exception as e:
            error_msg = str(e).lower()
            if "401" in error_msg or "unauthorized" in error_msg or "cookie" in error_msg:
                raise ConnectionError(
                    "ESPN API Authentication failed (401). Your ESPN_S2 or SWID cookies "
                    "may be expired. Please log in to ESPN in your browser, grab new "
                    "cookies, and update your environment variables."
                ) from e
            raise ConnectionError(f"Failed to connect to league {config.name}: {e}") from e

def get_all_clients() -> dict[str, League]:
    """Convenience function to get connected clients for all configured leagues.
    
    Returns:
        A dictionary mapping the league short_name to the connected League object.
    """
    client = LeagueClient()
    connected_leagues = {}
    for league_id, config in ALL_LEAGUES.items():
        connected_leagues[config.short_name] = client.get_league(config)
    return connected_leagues
