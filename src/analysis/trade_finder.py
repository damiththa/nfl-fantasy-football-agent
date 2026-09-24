"""
Proactive Trade Finder Engine.
Analyzes rosters across the entire ESPN fantasy league to identify mutually beneficial,
win-win trades that upgrade the user's starting lineup by leveraging bench depth and positional surplus.
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import datetime
from typing import Any, Optional

from src.analysis.trades import calculate_vorp, compute_optimal_starters
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
            opp_opt_starters, opp_opt_bench, opp_opt_pts = compute_optimal_starters(parsed.players, league)
            other_teams_data.append(
                {
                    "team_id": team_id,
                    "team_name": getattr(team, "team_name", "Unknown"),
                    "manager": manager,
                    "optimal_starters": [
                        {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
                        for p in opp_opt_starters
                    ],
                    "true_surplus_bench": [
                        {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
                        for p in opp_opt_bench
                    ],
                    "optimal_starting_points": opp_opt_pts,
                    "all_roster_players": [
                        {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
                        for p in parsed.players
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
    user_opt_starters, user_opt_bench, user_opt_pts = compute_optimal_starters(user_roster.players, league)
    user_roster_data = {
        "team_name": user_roster.team_name,
        "optimal_starters": [
            {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
            for p in user_opt_starters
        ],
        "true_surplus_bench": [
            {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
            for p in user_opt_bench
        ],
        "optimal_starting_points": user_opt_pts,
        "all_roster_players": [
            {"name": p.name, "pos": p.position, "pts": p.projected_points, "injury": p.injury_status}
            for p in user_roster.players
        ],
    }

    fallback_err: Optional[str] = None
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
            if report.coach_verdict == "HOLD_ROSTER" or not report.proposals:
                report.coach_verdict = "HOLD_ROSTER"
                report.is_trade_recommended = False
                report.proposals = []
                if not report.hold_roster_reasoning:
                    report.hold_roster_reasoning = (
                        "Your starting lineup is strong and your bench provides crucial positional depth. "
                        "No opposing teams currently offer a trade package that improves your starting lineup "
                        "without compromising essential depth. Hold your roster."
                    )
            else:
                report.is_trade_recommended = True
                for prop in report.proposals:
                    if not getattr(prop, "time_horizon", None):
                        prop.time_horizon = "LONG_TERM_DECISION"
                    if not getattr(prop, "coach_conviction", ""):
                        rec = ", ".join(prop.receiving_players)
                        giv = ", ".join(prop.giving_players)
                        prop.coach_conviction = (
                            f"Mad Dawg, here is why you make this move: acquiring {rec} injects high-impact "
                            f"starting equity while shipping away {giv} from our surplus depth. "
                            "This is how championships are engineered."
                        )
            eastern = zoneinfo.ZoneInfo("America/New_York")
            report.generated_at = datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z")
            report.intelligence_backend = client.model
            report.fallback_reason = None
            return report
        except Exception as e:
            fallback_err = str(e)
            logger.warning(
                "Gemini proactive trade generation failed (%s). Falling back to deterministic VORP trade scanner.",
                e,
            )

    # Deterministic Fallback: Scan teams for mutual surpluses and deficits based on overall optimal rosters
    proposals: list[TradeProposal] = []

    # Identify user's weakest starting slot among flex-eligible positions (RB, WR, TE)
    user_flex_starters = [
        p for p in user_opt_starters
        if (p.position or "").upper() in ("RB", "WR", "TE")
    ]
    user_flex_starters.sort(key=lambda p: p.projected_points)

    user_bench_assets = [
        p for p in user_opt_bench
        if (p.injury_status or "").upper() not in ("OUT", "IR", "SUSPENSION") and (p.position or "").upper() in ("RB", "WR", "TE", "QB")
    ]
    user_bench_assets.sort(key=lambda p: p.projected_points, reverse=True)

    # Candidate assets user can trade: true bench depth first, then startable assets if depth allows
    user_trade_candidates = user_bench_assets + [
        p for p in user_flex_starters
        if (p.injury_status or "").upper() not in ("OUT", "IR", "SUSPENSION") and getattr(p, "projected_points", 0.0) >= 8.0
    ]

    if user_flex_starters and user_trade_candidates and other_teams_parsed:
        user_weakest_starter = user_flex_starters[0]
        target_pos = user_weakest_starter.position

        for team, opp_roster in other_teams_parsed:
            opp_opt_starters, opp_opt_bench, opp_opt_pts = compute_optimal_starters(opp_roster.players, league)

            # Look across opponent's entire roster for healthy assets at target_pos
            # Prioritize true surplus bench depth first, then secondary starters (avoiding untradable alpha WR1/RB1s)
            opp_bench_cands = [
                p for p in opp_opt_bench
                if (p.position or "").upper() == target_pos and (p.injury_status or "").upper() not in ("OUT", "IR")
            ]
            opp_bench_cands.sort(key=lambda p: p.projected_points, reverse=True)

            opp_starter_cands = [
                p for p in opp_opt_starters
                if (p.position or "").upper() == target_pos and (p.injury_status or "").upper() not in ("OUT", "IR")
            ]
            opp_starter_cands.sort(key=lambda p: p.projected_points)

            opp_cands_at_target_pos = opp_bench_cands + opp_starter_cands

            opp_flex_starters = [
                p for p in opp_opt_starters
                if (p.position or "").upper() in ("RB", "WR", "TE")
            ]

            found_trade_for_team = False
            for user_asset in user_trade_candidates:
                if user_asset.position == target_pos:
                    continue  # We don't trade same position (e.g. WR for WR)

                opp_starters_at_user_pos = [
                    p for p in opp_flex_starters if (p.position or "").upper() == user_asset.position
                ]
                if not opp_starters_at_user_pos:
                    continue

                opp_weakest_at_pos = min(opp_starters_at_user_pos, key=lambda p: p.projected_points)
                opp_upgrade = user_asset.projected_points - opp_weakest_at_pos.projected_points

                for opp_asset in opp_cands_at_target_pos:
                    if opp_asset.name == user_asset.name:
                        continue

                    points_upgrade = opp_asset.projected_points - user_weakest_starter.projected_points

                    # The trade must be decisively more beneficial for US:
                    # 1. points_upgrade >= 1.5 (our starting lineup improves)
                    # 2. opp_upgrade >= 1.0 (opponent has incentive to accept)
                    # 3. Premium asset protection: If user gives up a top asset (>=13.0 pts),
                    #    we never accept a discounted return (opp_asset must be within 2.5 pts of user_asset)
                    is_star_trade = user_asset.projected_points >= 13.0
                    fair_value_return = not is_star_trade or (opp_asset.projected_points >= user_asset.projected_points - 2.5)

                    if points_upgrade >= 1.5 and opp_upgrade >= 1.0 and fair_value_return:
                        net_gain = round(
                            calculate_vorp(opp_asset.projected_points, target_pos, league.num_teams)
                            - calculate_vorp(user_weakest_starter.projected_points, target_pos, league.num_teams),
                            1,
                        )
                        manager_name = _extract_manager_name(team)
                        opp_team_name = getattr(team, "team_name", "Opponent")
                        opp_team_id = getattr(team, "team_id", 0)

                        proposal = TradeProposal(
                            target_team_id=opp_team_id,
                            target_team_name=opp_team_name,
                            target_manager=manager_name,
                            giving_players=[user_asset.name],
                            receiving_players=[opp_asset.name],
                            net_vorp_gain=max(net_gain, 1.5),
                            your_lineup_upgrade=(
                                f"Upgrades our starting {target_pos} from {user_weakest_starter.name} "
                                f"({user_weakest_starter.projected_points:.1f} pts) to {opp_asset.name} "
                                f"({opp_asset.projected_points:.1f} pts) by +{points_upgrade:.1f} pts/wk."
                            ),
                            why_target_accepts=(
                                f"Based on overall roster construction, {opp_team_name}'s weakest starting "
                                f"{user_asset.position} is {opp_weakest_at_pos.name} ({opp_weakest_at_pos.projected_points:.1f} pts). "
                                f"Adding {user_asset.name} ({user_asset.projected_points:.1f} pts) directly injects "
                                f"+{opp_upgrade:.1f} pts/wk into their starting lineup, while they trade from depth at {target_pos}."
                            ),
                            negotiation_pitch=(
                                f"Hey {manager_name}, looking at our overall rosters, {user_asset.name} provides an immediate +{opp_upgrade:.1f} pts/wk "
                                f"upgrade to your starting {user_asset.position} spot over {opp_weakest_at_pos.name}. I have depth at {user_asset.position} "
                                f"and could use help at {opp_asset.position} with {opp_asset.name}. Would you be open to this swap?"
                            ),
                            time_horizon="LONG_TERM_DECISION",
                            time_horizon_detail=(
                                f"Rest-of-season permanent starting upgrade (+{points_upgrade:.1f} pts/wk for us, "
                                f"+{opp_upgrade:.1f} pts/wk for {opp_team_name}): A balanced, mutually beneficial deal "
                                f"addressing structural needs on both rosters."
                            ),
                            coach_conviction=(
                                f"Mad Dawg, listen up: this is a textbook championship trade. We aren't making naive assumptions "
                                f"about unadjusted bench slots—looking at overall roster strength, {opp_asset.name} immediately upgrades "
                                f"our starting {target_pos} by +{points_upgrade:.1f} points every single week. Meanwhile, {opp_team_name} "
                                f"gains +{opp_upgrade:.1f} pts/wk at {user_asset.position} where their roster is thin. "
                                f"It's a genuine win-win deal that moves our championship needle. Send the offer today."
                            ),
                        )
                        proposals.append(proposal)
                        found_trade_for_team = True
                        break
                if found_trade_for_team:
                    break
            if len(proposals) >= 2:
                break

    eastern = zoneinfo.ZoneInfo("America/New_York")
    gen_time = datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z")

    if not proposals:
        return LeagueTradeReport(
            league_id=league.league_id,
            week=week,
            is_trade_recommended=False,
            coach_verdict="HOLD_ROSTER",
            hold_roster_reasoning=(
                "🛡️ Roster balance is optimal. Your current starting lineup is strong and no opposing managers "
                "possess the necessary surplus to offer a deal that genuinely improves your weekly scoring without "
                "compromising your bench depth. Stand pat and do not force trades for the sake of deal-making."
            ),
            proposals=[],
            market_overview=(
                "🛡️ COACH'S VERDICT: HOLD ROSTER. League-wide market scan found no high-leverage trade opportunities "
                "that warrant parting with your bench depth. Hold your current assets."
            ),
            generated_at=gen_time,
            intelligence_backend="deterministic_fallback",
            fallback_reason=fallback_err or "Gemini client was not provided",
        )

    return LeagueTradeReport(
        league_id=league.league_id,
        week=week,
        is_trade_recommended=True,
        coach_verdict="PROPOSE_TRADES",
        hold_roster_reasoning=None,
        proposals=proposals,
        market_overview=(
            f"High-conviction market scan identified {len(proposals)} targeted upgrade opportunity based on overall roster balance. "
            f"Trading from positional depth to improve optimal starting lineup output."
        ),
        generated_at=gen_time,
        intelligence_backend="deterministic_fallback",
        fallback_reason=fallback_err or "Gemini client was not provided",
    )
