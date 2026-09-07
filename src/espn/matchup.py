"""
Models and functions for retrieving and parsing ESPN matchup and box score data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.config import LeagueConfig
from src.espn.roster import ParsedRoster, parse_roster

@dataclass
class MatchupData:
    """Structured data for a weekly matchup."""
    week: int
    your_team: ParsedRoster
    opponent_team: Optional[ParsedRoster]
    your_projected: float
    opp_projected: float
    projected_margin: float
    is_favorite: bool

def get_current_week(league: Any) -> int:
    """Determine the current NFL week for the league.
    
    Args:
        league: The espn_api League object.
        
    Returns:
        The current week integer.
    """
    return league.current_week

def get_weekly_matchup(league: Any, team_id: int, week: int, league_config: LeagueConfig) -> Optional[MatchupData]:
    """Pull box scores for a given week and extract the matchup for the specified team.
    
    Args:
        league: The espn_api League object.
        team_id: The ID of the team to find the matchup for.
        week: The week number.
        league_config: The league configuration.
        
    Returns:
        MatchupData if found, else None.
    """
    # Fetch box scores from the league for the given week
    box_scores = league.box_scores(week)
    
    for matchup in box_scores:
        home_team = getattr(matchup, 'home_team', None)
        away_team = getattr(matchup, 'away_team', None)
        
        if not home_team:
            continue
            
        home_team_id = getattr(home_team, 'team_id', -1)
        away_team_id = getattr(away_team, 'team_id', -1) if away_team else -1
        
        if home_team_id == team_id or away_team_id == team_id:
            is_home = (home_team_id == team_id)
            
            your_team = home_team if is_home else away_team
            opp_team = away_team if is_home else home_team
            
            # Using home_projected / away_projected from box score matchup if available
            your_projected = getattr(matchup, 'home_projected', 0.0) if is_home else getattr(matchup, 'away_projected', 0.0)
            opp_projected = getattr(matchup, 'away_projected', 0.0) if is_home else getattr(matchup, 'home_projected', 0.0)
            
            your_roster = parse_roster(your_team, league_config)
            
            if opp_team:
                opp_roster = parse_roster(opp_team, league_config)
            else:
                opp_roster = None
                opp_projected = 0.0
                
            margin = your_projected - opp_projected
            is_fav = margin > 0
            
            return MatchupData(
                week=week,
                your_team=your_roster,
                opponent_team=opp_roster,
                your_projected=your_projected,
                opp_projected=opp_projected,
                projected_margin=margin,
                is_favorite=is_fav
            )
            
    return None
