"""
Start/Sit Lineup Optimizer.
Applies game theory (floor vs ceiling) depending on matchup point margins,
cross-referencing injuries, Vegas totals, and weather conditions.
"""

from typing import Optional

from src.config import LeagueConfig
from src.data.injuries import PlayerInjuryInfo
from src.data.vegas import GameOdds, get_player_game_odds
from src.data.weather import GameWeather
from src.espn.matchup import MatchupData
from src.espn.roster import ParsedRoster
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_lineup_prompt
from src.intelligence.schemas import LineupRecommendation, StartSitDecision


def optimize_lineup(
    league: LeagueConfig,
    week: int,
    roster: ParsedRoster,
    matchup: Optional[MatchupData] = None,
    injuries: Optional[list[PlayerInjuryInfo]] = None,
    odds: Optional[list[GameOdds]] = None,
    weather_map: Optional[dict[str, GameWeather]] = None,
    client: Optional[GeminiIntelligenceClient] = None,
) -> LineupRecommendation:
    """Optimize starting lineup for a league and week using game-theory and Gemini Pro.

    Args:
        league: League configuration.
        week: Current NFL week.
        roster: Parsed roster for user team.
        matchup: Weekly matchup data (optional).
        injuries: List of relevant player injuries (optional).
        odds: List of week's game odds (optional).
        weather_map: Map of team abbr to GameWeather (optional).
        client: Gemini intelligence client (optional).

    Returns:
        LineupRecommendation with complete start/sit plan.
    """
    projected_margin = matchup.projected_margin if matchup else 0.0

    # Determine game-theory strategy
    if projected_margin > 12.0:
        strategy = "PROTECT_LEAD"
        strategy_reasoning = (
            f"Projected as heavy favorite (+{projected_margin:.1f} pts). "
            "Prioritizing high-floor, volume-guaranteed starters to minimize variance."
        )
    elif projected_margin < -12.0:
        strategy = "SEEK_VARIANCE"
        strategy_reasoning = (
            f"Projected as significant underdog ({projected_margin:.1f} pts). "
            "Prioritizing high-ceiling, explosive players and correlation stacks to maximize upside."
        )
    else:
        strategy = "BALANCED"
        strategy_reasoning = (
            f"Projected in a tight matchup ({projected_margin:+.1f} pts). "
            "Balancing floor and ceiling with priority on favorable red-zone and Vegas game scripts."
        )

    # If Gemini client provided, use Gemini Pro for reasoning
    if client is not None:
        your_roster_data = [
            {
                "name": p.name,
                "pos": p.position,
                "team": p.team,
                "current_slot": p.slot,
                "projected_pts": p.projected_points,
                "injury": p.injury_status,
            }
            for p in roster.players
        ]

        opp_roster_data = None
        if matchup and matchup.opponent_team:
            opp_roster_data = [
                {
                    "name": p.name,
                    "pos": p.position,
                    "team": p.team,
                    "slot": p.slot,
                    "projected_pts": p.projected_points,
                }
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
            for team_abbr, w in weather_map.items():
                weather_data.append(
                    {
                        "team": team_abbr,
                        "stadium": w.stadium_name,
                        "dome": w.is_dome,
                        "temp": w.temperature_f,
                        "wind": w.wind_speed_mph,
                        "conditions": w.conditions_summary,
                    }
                )

        injury_data = []
        if injuries:
            for inj in injuries:
                injury_data.append(
                    {
                        "name": inj.full_name,
                        "team": inj.team,
                        "status": inj.injury_status,
                        "practice": inj.practice_participation,
                        "body_part": inj.injury_body_part,
                        "notes": inj.injury_notes,
                    }
                )

        prompt = format_lineup_prompt(
            league=league,
            week=week,
            your_roster={"players": your_roster_data},
            opponent_roster={"starters": opp_roster_data} if opp_roster_data else None,
            projected_margin=projected_margin,
            vegas_odds=odds_data,
            weather_reports=weather_data,
            injuries=injury_data,
        )

        return client.generate_structured(prompt=prompt, response_schema=LineupRecommendation)

    # Deterministic fallback algorithm when LLM client is not passed (e.g. testing/offline)
    recommended_starters = []
    bench_players = []

    # Sort players by projected points
    sorted_players = sorted(roster.players, key=lambda p: p.projected_points, reverse=True)

    # Assign based on roster slots
    slots_needed = {
        "QB": league.roster.qb,
        "RB": league.roster.rb,
        "WR": league.roster.wr,
        "TE": league.roster.te,
        "DST": league.roster.dst,
        "K": league.roster.k,
        "FLEX": league.roster.flex,
    }

    slots_filled = {k: 0 for k in slots_needed}
    flex_eligible = ("RB", "WR", "TE")

    for p in sorted_players:
        pos = p.position.upper()
        is_injured = p.injury_status.upper() in ("OUT", "IR", "DOUBTFUL")

        action = "BENCH"
        if not is_injured:
            if pos in slots_filled and slots_filled[pos] < slots_needed[pos]:
                slots_filled[pos] += 1
                action = "START"
            elif pos in flex_eligible and slots_filled["FLEX"] < slots_needed["FLEX"]:
                slots_filled["FLEX"] += 1
                action = "START"

        # Game script note
        game_note = None
        if odds:
            p_odds = get_player_game_odds(p.team, odds)
            if p_odds:
                implied = (
                    p_odds.home_implied_total
                    if p.team == p_odds.home_team
                    else p_odds.away_implied_total
                )
                game_note = f"Vegas implied team total: {implied} pts (O/U: {p_odds.over_under})"

        decision = StartSitDecision(
            player_name=p.name,
            position=p.position,
            team=p.team,
            action=action,
            confidence=0.85 if action == "START" else 0.75,
            floor=round(p.projected_points * 0.7, 1),
            ceiling=round(p.projected_points * 1.3, 1),
            projected_points=p.projected_points,
            reasoning=(
                f"Ranked for starting role based on {p.projected_points} projected PPR points."
                if action == "START"
                else f"Reserve option; {p.injury_status if is_injured else 'out-projected by starters'}."
            ),
            game_script_note=game_note,
        )

        if action == "START":
            recommended_starters.append(decision)
        else:
            bench_players.append(decision)

    return LineupRecommendation(
        league_id=league.league_id,
        week=week,
        game_theory_strategy=strategy,
        strategy_reasoning=strategy_reasoning,
        recommended_starters=recommended_starters,
        bench_players=bench_players,
        key_flex_decisions=[
            f"Filled {slots_filled['FLEX']}/{slots_needed['FLEX']} flex slots with highest projection upside."
        ],
    )
