"""
Proactive Trade Finder Engine.
Analyzes rosters across the entire ESPN fantasy league to identify mutually beneficial,
win-win trades that upgrade the user's starting lineup by leveraging bench depth and positional surplus.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.analysis.trades import calculate_vorp
from src.config import LeagueConfig
from src.espn.roster import ParsedRoster, parse_roster
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_league_trade_prompt
from src.intelligence.schemas import LeagueTradeReport, TradeProposal

logger = logging.getLogger(__name__)


def _extract_manager_name(team: Any) -> str:
    """Extract owner/manager display name from an ESPN team object."""
    owners = getattr(team, "owners", None)
    if owners and isinstance(owners, list) and len(owners) > 0:
        first = owners[0]
        if isinstance(first, dict):
            return first.get("firstName", "") + " " + first.get("lastName", "")
        return str(first)
    owner = getattr(team, "owner", None)
    if owner:
        return str(owner)
    return getattr(team, "team_name", "Manager")


def propose_league_trades(
    league: LeagueConfig,
    week: int,
    espn_league: Any,
    client: Optional[GeminiIntelligenceClient] = None,
) -> LeagueTradeReport:
    """Scan all teams in the league and discover high-value trades the user should initiate.

    Args:
        league: League configuration.
        week: Current NFL week.
        espn_league: Connected espn_api League instance.
        client: Gemini intelligence client (optional).

    Returns:
        LeagueTradeReport with recommended proactive trade proposals.
    """
    teams = getattr(espn_league, "teams", [])
    user_team = None
    other_teams_data = []
    other_teams_parsed: list[tuple[Any, ParsedRoster]] = []

    for team in teams:
        team_id = getattr(team, "team_id", None)
        if team_id == league.team_id:
            user_team = team
        else:
            parsed = parse_roster(team, league)
            manager = _extract_manager_name(team)
            other_teams_parsed.append((team, parsed))
            other_teams_data.append(
                {
                    "team_id": team_id,
                    "team_name": getattr(team, "team_name", "Unknown"),
                    "manager": manager,
                    "starters": [
                        {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
                        for p in parsed.starters
                    ],
                    "bench": [
                        {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
                        for p in parsed.bench
                    ],
                }
            )

    if not user_team:
        logger.warning("User team %s not found in league %s", league.team_id, league.name)
        return LeagueTradeReport(
            league_id=league.league_id,
            week=week,
            proposals=[],
            market_overview=f"User team {league.team_id} not found in league.",
        )

    user_roster = parse_roster(user_team, league)
    user_roster_data = {
        "team_name": user_roster.team_name,
        "starters": [
            {"name": p.name, "pos": p.position, "slot": p.slot, "pts": p.projected_points, "injury": p.injury_status}
            for p in user_roster.starters
        ],
        "bench": [
            {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
            for p in user_roster.bench
        ],
    }

    # If Gemini client provided, use AI reasoning to craft smart win-win packages
    if client is not None:
        prompt = format_league_trade_prompt(
            league=league,
            week=week,
            your_roster=user_roster_data,
            other_teams=other_teams_data,
        )
        try:
            report = client.generate_structured(prompt=prompt, response_schema=LeagueTradeReport)
            report.league_id = league.league_id
            report.week = week
            return report
        except Exception as e:
            logger.warning(
                "Gemini proactive trade generation failed (%s). Falling back to deterministic VORP trade scanner.",
                e,
            )

    # Deterministic Fallback: Scan teams for mutual surpluses and deficits
    proposals: list[TradeProposal] = []

    # Identify user's bench depth (healthy bench players with solid projected pts)
    viable_bench = [
        p for p in user_roster.bench
        if p.injury_status.upper() not in ("OUT", "IR", "SUSPENSION") and p.position in ("RB", "WR", "TE", "QB")
    ]
    viable_bench.sort(key=lambda p: p.projected_points, reverse=True)

    # Identify user's lowest projected starter (target for upgrade)
    upgradable_starters = [
        p for p in user_roster.starters
        if p.position in ("RB", "WR", "TE")
    ]
    upgradable_starters.sort(key=lambda p: p.projected_points)

    if viable_bench and upgradable_starters and other_teams_parsed:
        target_pos = upgradable_starters[0].position
        bench_asset = viable_bench[0]

        for team, opp_roster in other_teams_parsed:
            opp_starters_at_bench_pos = [
                p for p in opp_roster.starters if p.position == bench_asset.position
            ]
            opp_bench_at_target_pos = [
                p for p in opp_roster.bench
                if p.position == target_pos and p.injury_status.upper() not in ("OUT", "IR")
            ]

            # If opponent has a weak starter at bench_asset's position and bench surplus at target_pos
            if opp_starters_at_bench_pos and opp_bench_at_target_pos:
                weakest_opp_starter = min(opp_starters_at_bench_pos, key=lambda p: p.projected_points)
                best_opp_bench = max(opp_bench_at_target_pos, key=lambda p: p.projected_points)

                if (bench_asset.projected_points > weakest_opp_starter.projected_points and
                        best_opp_bench.projected_points > upgradable_starters[0].projected_points):
                    net_gain = round(
                        calculate_vorp(best_opp_bench.projected_points, target_pos, league.num_teams)
                        - calculate_vorp(upgradable_starters[0].projected_points, target_pos, league.num_teams),
                        1,
                    )
                    manager_name = _extract_manager_name(team)
                    opp_team_name = getattr(team, "team_name", "Opponent")
                    opp_team_id = getattr(team, "team_id", 0)

                    proposal = TradeProposal(
                        target_team_id=opp_team_id,
                        target_team_name=opp_team_name,
                        target_manager=manager_name,
                        giving_players=[bench_asset.name],
                        receiving_players=[best_opp_bench.name],
                        net_vorp_gain=max(net_gain, 1.2),
                        your_lineup_upgrade=f"Upgrades our starting {target_pos} from {upgradable_starters[0].name} ({upgradable_starters[0].projected_points:.1f} pts) to {best_opp_bench.name} ({best_opp_bench.projected_points:.1f} pts).",
                        why_target_accepts=f"{opp_team_name} is weak at {bench_asset.position} with {weakest_opp_starter.name} ({weakest_opp_starter.projected_points:.1f} pts); {bench_asset.name} ({bench_asset.projected_points:.1f} pts) steps right in as their starter.",
                        negotiation_pitch=f"Hey {manager_name}, noticed you're a bit thin at {bench_asset.position} starting {weakest_opp_starter.name}. I've got extra {bench_asset.position} depth and could use a {target_pos}. Would you do {bench_asset.name} for {best_opp_bench.name}?",
                    )
                    proposals.append(proposal)
                    if len(proposals) >= 3:
                        break

    return LeagueTradeReport(
        league_id=league.league_id,
        week=week,
        proposals=proposals,
        market_overview=(
            f"Roster depth analysis identified top surplus at {[p.name for p in viable_bench[:2]]}. "
            f"Primary upgrade target is starting {upgradable_starters[0].position if upgradable_starters else 'FLEX'}."
        ),
    )
