"""
Weekly Matchup Preview and Scouting Report Builder.
Compiles user and opponent rosters, Vegas lines, and game weather into a full scouting report.
"""

import math
from typing import Optional

from src.config import LeagueConfig
from src.data.vegas import GameOdds
from src.data.weather import GameWeather
from src.espn.matchup import MatchupData
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_matchup_prompt
from src.intelligence.schemas import MatchupReport


def _calculate_win_probability(margin: float) -> float:
    """Calculate approximate win probability from projected point margin using logistic function."""
    # Standard deviation in fantasy football weekly matchups is ~16-18 points.
    # Sigmoid: 1 / (1 + exp(-margin / 12.0))
    prob = 1.0 / (1.0 + math.exp(-margin / 12.0))
    return round(prob, 2)


def generate_matchup_preview(
    league: LeagueConfig,
    week: int,
    matchup: MatchupData,
    odds: Optional[list[GameOdds]] = None,
    weather_map: Optional[dict[str, GameWeather]] = None,
    client: Optional[GeminiIntelligenceClient] = None,
) -> MatchupReport:
    """Generate a weekly matchup preview and scouting report.

    Args:
        league: League configuration.
        week: Current week.
        matchup: Parsed MatchupData with user and opponent rosters and scores.
        odds: List of game odds (optional).
        weather_map: Map of team to game weather (optional).
        client: Gemini intelligence client (optional).

    Returns:
        MatchupReport with comprehensive scouting breakdown.
    """
    user_proj = matchup.your_projected
    opp_proj = matchup.opp_projected
    margin = round(user_proj - opp_proj, 1)
    win_prob = _calculate_win_probability(margin)
    opp_name = matchup.opponent_team.team_name if matchup.opponent_team else "Opponent"

    # If Gemini client provided, use Gemini Pro for reasoning
    if client is not None:
        your_roster_data = [
            {"name": p.name, "pos": p.position, "pts": p.projected_points, "slot": p.slot}
            for p in matchup.your_team.starters
        ]
        opp_roster_data = []
        if matchup.opponent_team:
            opp_roster_data = [
                {"name": p.name, "pos": p.position, "pts": p.projected_points, "slot": p.slot}
                for p in matchup.opponent_team.starters
            ]

        odds_data = []
        if odds:
            for g in odds:
                odds_data.append(
                    {
                        "game": f"{g.away_team} @ {g.home_team}",
                        "spread": g.spread,
                        "over_under": g.over_under,
                        "implied": f"{g.away_team} {g.away_implied_total} - {g.home_team} {g.home_implied_total}",
                    }
                )

        weather_data = []
        if weather_map:
            for team, w in weather_map.items():
                weather_data.append(
                    {
                        "team": team,
                        "stadium": w.stadium_name,
                        "temp": w.temperature_f,
                        "wind": w.wind_speed_mph,
                        "conditions": w.conditions_summary,
                    }
                )

        prompt = format_matchup_prompt(
            league=league,
            week=week,
            your_roster={"starters": your_roster_data},
            opponent_roster={"starters": opp_roster_data},
            user_projected=user_proj,
            opp_projected=opp_proj,
            vegas_odds=odds_data,
            weather_reports=weather_data,
        )

        return client.generate_structured(prompt=prompt, response_schema=MatchupReport)

    # Deterministic fallback algorithm when LLM client is None
    advantages = []
    vulnerabilities = []

    # Compare position-by-position projected totals
    user_pos_pts: dict[str, float] = {}
    for p in matchup.your_team.starters:
        user_pos_pts[p.position] = user_pos_pts.get(p.position, 0.0) + p.projected_points

    opp_pos_pts: dict[str, float] = {}
    if matchup.opponent_team:
        for p in matchup.opponent_team.starters:
            opp_pos_pts[p.position] = opp_pos_pts.get(p.position, 0.0) + p.projected_points

    for pos, u_pts in user_pos_pts.items():
        o_pts = opp_pos_pts.get(pos, 0.0)
        diff = u_pts - o_pts
        if diff >= 3.0:
            advantages.append(f"Strong advantage at {pos} (+{diff:.1f} pts projected)")
        elif diff <= -3.0:
            vulnerabilities.append(f"Disadvantage at {pos} ({diff:.1f} pts projected)")

    weather_factors = []
    if weather_map:
        for team, w in weather_map.items():
            if w.conditions_summary not in ("Clear", "Indoor", "Unknown"):
                weather_factors.append(f"{team} ({w.stadium_name}): {w.conditions_summary}")

    summary = (
        f"Week {week} contest vs {opp_name}. "
        f"Projected {user_proj:.1f} vs {opp_proj:.1f} ({margin:+.1f} margin, {win_prob * 100:.0f}% win probability). "
        f"{'Solid favorite; maintain high floor.' if margin > 5 else ('Underdog; look for ceiling variance.' if margin < -5 else 'Even matchup; starting lineup execution will decide it.')}"
    )

    return MatchupReport(
        league_id=league.league_id,
        week=week,
        opponent_name=opp_name,
        projected_score_user=user_proj,
        projected_score_opponent=opp_proj,
        projected_margin=margin,
        win_probability=win_prob,
        key_advantages=advantages or ["Evenly matched across major positions"],
        key_vulnerabilities=vulnerabilities or ["No glaring single positional liability"],
        weather_and_vegas_factors=weather_factors or ["Standard dome/clear outdoor conditions"],
        strategic_summary=summary,
    )
