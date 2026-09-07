"""
Trade Value and VORP (Value Over Replacement Player) Engine.
Evaluates proposed trades based on net impact to starting lineup and ROS/playoff strength.
"""

import logging
import zoneinfo
from datetime import datetime
from typing import Optional

from src.config import LeagueConfig
from src.espn.roster import ParsedRoster
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_trade_prompt
from src.intelligence.schemas import TradeEvaluation

logger = logging.getLogger(__name__)

# Positional replacement baselines (approximate weekly points of top free agent)
# Adjusted for league size and PPR scoring
REPLACEMENT_BASELINES_12_TEAM = {
    "QB": 15.0,
    "RB": 8.5,
    "WR": 9.5,
    "TE": 6.5,
    "DST": 5.0,
    "K": 6.0,
}

REPLACEMENT_BASELINES_10_TEAM = {
    "QB": 16.5,
    "RB": 9.5,
    "WR": 10.5,
    "TE": 7.0,
    "DST": 5.5,
    "K": 6.5,
}


def calculate_vorp(projected_ppg: float, position: str, num_teams: int = 12) -> float:
    """Calculate weekly Value Over Replacement Player (VORP).

    Args:
        projected_ppg: Player's projected points per game.
        position: Player position (QB, RB, WR, TE, etc.).
        num_teams: League size (10 or 12).

    Returns:
        Weekly VORP float (positive = above replacement).
    """
    baselines = REPLACEMENT_BASELINES_10_TEAM if num_teams <= 10 else REPLACEMENT_BASELINES_12_TEAM
    baseline = baselines.get(position.upper(), 8.0)
    return round(projected_ppg - baseline, 2)


def evaluate_trade(
    league: LeagueConfig,
    roster: ParsedRoster,
    giving_players: list[str],
    receiving_players: list[str],
    player_projections: Optional[dict[str, tuple[str, float]]] = None,
    opponent_roster: Optional[ParsedRoster] = None,
    client: Optional[GeminiIntelligenceClient] = None,
) -> TradeEvaluation:
    """Evaluate a prospective fantasy trade based on starting lineup impact and VORP.

    Args:
        league: League configuration.
        roster: Current user roster.
        giving_players: List of player names being sent away.
        receiving_players: List of player names being received.
        player_projections: Dict of {player_name: (position, projected_ppg)} for VORP math.
        opponent_roster: Optional opponent roster to evaluate trade partner leverage.
        client: Gemini intelligence client (optional).

    Returns:
        TradeEvaluation containing verdict, net VORP change, and analysis.
    """
    # If Gemini client provided, use Gemini Pro for deep contextual reasoning
    if client is not None:
        roster_data = [
            {"name": p.name, "pos": p.position, "pts": p.projected_points, "slot": p.slot}
            for p in roster.players
        ]
        opp_data = None
        if opponent_roster:
            opp_data = [
                {"name": p.name, "pos": p.position, "pts": p.projected_points}
                for p in opponent_roster.players
            ]

        prompt = format_trade_prompt(
            league=league,
            your_roster={"players": roster_data},
            giving_players=giving_players,
            receiving_players=receiving_players,
            opponent_roster={"players": opp_data} if opp_data else None,
        )

        try:
            verdict_obj = client.generate_structured(prompt=prompt, response_schema=TradeEvaluation)
            eastern = zoneinfo.ZoneInfo("America/New_York")
            verdict_obj.generated_at = datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z")
            return verdict_obj
        except Exception as e:
            logger.warning(
                "Gemini trade evaluation call failed (%s). Falling back to deterministic VORP evaluation.",
                e,
            )

    # Deterministic mathematical VORP calculation
    proj_map = player_projections or {}

    giving_vorp = 0.0
    for name in giving_players:
        if name in proj_map:
            pos, ppg = proj_map[name]
            giving_vorp += calculate_vorp(ppg, pos, league.num_teams)

    receiving_vorp = 0.0
    for name in receiving_players:
        if name in proj_map:
            pos, ppg = proj_map[name]
            receiving_vorp += calculate_vorp(ppg, pos, league.num_teams)

    net_vorp = round(receiving_vorp - giving_vorp, 2)

    # Determine verdict
    if net_vorp >= 2.0:
        verdict = "ACCEPT"
        reasoning = (
            f"Net positive starting value (+{net_vorp:.1f} weekly VORP). "
            f"Receiving {', '.join(receiving_players)} significantly upgrades starting lineup caliber."
        )
        counter = None
    elif net_vorp <= -2.0:
        verdict = "REJECT"
        reasoning = (
            f"Net negative value ({net_vorp:.1f} weekly VORP). "
            f"Giving up {', '.join(giving_players)} strips away more starting power than is returned."
        )
        counter = "Request an additional flex-tier piece or a higher draft/waiver asset."
    else:
        verdict = "COUNTER"
        reasoning = (
            f"Even value proposition ({net_vorp:+.1f} weekly VORP). "
            "Consider whether this trade solves a specific positional surplus/need mismatch."
        )
        counter = "Offer a lower-tier bench asset instead of your primary starter."

    eastern = zoneinfo.ZoneInfo("America/New_York")
    return TradeEvaluation(
        verdict=verdict,
        your_vorp_change=net_vorp,
        starting_lineup_impact=(
            f"Exchanges {len(giving_players)} players for {len(receiving_players)} players. "
            f"Net weekly impact is estimated at {net_vorp:+.1f} points over baseline."
        ),
        playoff_schedule_impact=(
            f"Target players ({', '.join(receiving_players)}) should be cross-referenced with "
            f"Weeks 15-17 opposing defenses."
        ),
        reasoning=reasoning,
        counter_suggestion=counter,
        generated_at=datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z"),
    )
