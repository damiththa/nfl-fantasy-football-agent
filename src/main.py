"""
FastAPI application serving the NFL Fantasy Football Agent on Google Cloud Run.
Exposes endpoints for Cloud Scheduler cron triggers and on-demand analysis queries.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from src.analysis.lineup import optimize_lineup
from src.analysis.matchup_preview import generate_matchup_preview
from src.analysis.trades import evaluate_trade
from src.analysis.waivers import evaluate_waivers
from src.config import ALL_LEAGUES, get_current_season, get_gemini_model
from src.data.injuries import get_injury_report
from src.data.trending import fetch_trending_adds
from src.data.vegas import fetch_week_odds
from src.data.weather import fetch_game_weather
from src.espn.client import LeagueClient
from src.espn.matchup import get_current_week, get_weekly_matchup
from src.espn.roster import parse_roster
from src.intelligence.gemini_client import GeminiIntelligenceClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fantasy_agent")

app = FastAPI(
    title="NFL Fantasy Football Agent",
    description="AI-powered Fantasy Football veteran analyst for ESPN leagues, powered by Gemini Pro.",
    version="1.0.0",
)


class TradeRequest(BaseModel):
    league_id: int
    giving_players: list[str]
    receiving_players: list[str]


@app.get("/")
@app.get("/health")
def health_check() -> dict[str, Any]:
    """Health check endpoint confirming service status and configuration."""
    return {
        "status": "healthy",
        "season": get_current_season(),
        "model": get_gemini_model(),
        "leagues": [
            {
                "id": cfg.league_id,
                "name": cfg.name,
                "teams": cfg.num_teams,
                "pass_td": cfg.scoring.pass_td,
            }
            for cfg in ALL_LEAGUES.values()
        ],
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/run/weekly")
def run_weekly_analysis() -> dict[str, Any]:
    """Automated weekday workflow triggered by Cloud Scheduler (Tue, Thu, Fri, Sat).
    Routes internally based on current weekday (America/New_York).
    """
    weekday = datetime.now().weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
        weekday
    ]

    logger.info(f"Running automated weekly job for {day_name}...")

    results = {}
    client = None
    try:
        client = GeminiIntelligenceClient()
    except Exception as e:
        logger.warning(
            f"Could not initialize GeminiIntelligenceClient: {e}. Falling back to deterministic mode."
        )

    for league_id, league_config in ALL_LEAGUES.items():
        try:
            espn = LeagueClient().get_league(league_config)
            current_week = get_current_week(espn)
            my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
            if not my_team:
                continue

            parsed_roster = parse_roster(my_team, league_config)
            matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)

            if weekday == 1:  # Tuesday: Waiver Wire Analysis
                free_agents = [
                    {
                        "name": p.name,
                        "position": p.position,
                        "team": p.proTeam,
                        "projected_points": getattr(p, "projected_points", 0.0),
                        "percent_owned": getattr(p, "percent_owned", 0.0),
                    }
                    for p in espn.free_agents(size=25)
                ]
                trending = fetch_trending_adds(lookback_hours=24, limit=20)
                report = evaluate_waivers(
                    league_config,
                    current_week,
                    parsed_roster,
                    free_agents,
                    trending_adds=trending,
                    client=client,
                )
                results[league_config.short_name] = report.model_dump()

            elif weekday in (3, 4):  # Thursday/Friday: Injury & TNF Check
                roster_names = [p.name for p in parsed_roster.players]
                injuries = get_injury_report(roster_names)
                odds = fetch_week_odds()
                lineup = optimize_lineup(
                    league_config,
                    current_week,
                    parsed_roster,
                    matchup=matchup,
                    injuries=injuries,
                    odds=odds,
                    client=client,
                )
                results[league_config.short_name] = lineup.model_dump()

            elif weekday == 5:  # Saturday: Full Matchup Preview & Scouting
                odds = fetch_week_odds()
                if matchup:
                    report = generate_matchup_preview(
                        league_config, current_week, matchup, odds=odds, client=client
                    )
                    results[league_config.short_name] = report.model_dump()

            else:
                results[league_config.short_name] = {
                    "message": f"No specific routine scheduled for {day_name}"
                }

        except Exception as e:
            logger.error(f"Error processing league {league_id}: {e}", exc_info=True)
            results[league_config.short_name] = {"error": str(e)}

    return {
        "job": "weekly_analysis",
        "day": day_name,
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/run/sunday-pregame")
def run_sunday_pregame() -> dict[str, Any]:
    """Sunday 90-minute pregame alert: checks active/inactive statuses, weather, and finalizes starters."""
    logger.info("Running Sunday pregame inactive & final lineup optimization...")

    results = {}
    client = None
    try:
        client = GeminiIntelligenceClient()
    except Exception as e:
        logger.warning(
            f"Could not initialize GeminiIntelligenceClient: {e}. Falling back to deterministic mode."
        )

    odds = fetch_week_odds()

    for league_id, league_config in ALL_LEAGUES.items():
        try:
            espn = LeagueClient().get_league(league_config)
            current_week = get_current_week(espn)
            my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
            if not my_team:
                continue

            parsed_roster = parse_roster(my_team, league_config)
            matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)

            # Live injury and weather check for roster
            roster_names = [p.name for p in parsed_roster.players]
            injuries = get_injury_report(roster_names)

            weather_map = {}
            for p in parsed_roster.starters:
                if p.team and p.team not in weather_map:
                    weather_map[p.team] = fetch_game_weather(p.team, datetime.now())

            lineup = optimize_lineup(
                league=league_config,
                week=current_week,
                roster=parsed_roster,
                matchup=matchup,
                injuries=injuries,
                odds=odds,
                weather_map=weather_map,
                client=client,
            )
            results[league_config.short_name] = lineup.model_dump()

        except Exception as e:
            logger.error(f"Error in Sunday pregame for league {league_id}: {e}", exc_info=True)
            results[league_config.short_name] = {"error": str(e)}

    return {
        "job": "sunday_pregame",
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/query/lineup")
def query_lineup(league_id: int = Query(..., description="ESPN League ID")) -> dict[str, Any]:
    """On-demand start/sit lineup optimization for a specific league."""
    league_config = ALL_LEAGUES.get(league_id)
    if not league_config:
        raise HTTPException(
            status_code=404, detail=f"League {league_id} not found in configuration."
        )

    try:
        espn = LeagueClient().get_league(league_config)
        current_week = get_current_week(espn)
        my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
        if not my_team:
            raise HTTPException(
                status_code=404, detail=f"Team {league_config.team_id} not found in league."
            )

        parsed_roster = parse_roster(my_team, league_config)
        matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)
        roster_names = [p.name for p in parsed_roster.players]
        injuries = get_injury_report(roster_names)
        odds = fetch_week_odds()

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception:
            pass

        lineup = optimize_lineup(
            league=league_config,
            week=current_week,
            roster=parsed_roster,
            matchup=matchup,
            injuries=injuries,
            odds=odds,
            client=client,
        )
        return lineup.model_dump()
    except Exception as e:
        logger.error(f"Error querying lineup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/trade")
def query_trade(req: TradeRequest) -> dict[str, Any]:
    """On-demand trade evaluation."""
    league_config = ALL_LEAGUES.get(req.league_id)
    if not league_config:
        raise HTTPException(status_code=404, detail=f"League {req.league_id} not found.")

    try:
        espn = LeagueClient().get_league(league_config)
        my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
        parsed_roster = parse_roster(my_team, league_config) if my_team else None

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception:
            pass

        verdict = evaluate_trade(
            league=league_config,
            roster=parsed_roster,
            giving_players=req.giving_players,
            receiving_players=req.receiving_players,
            client=client,
        )
        return verdict.model_dump()
    except Exception as e:
        logger.error(f"Error evaluating trade: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
