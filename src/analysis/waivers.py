"""
Waiver Wire Engine.
Ranks waiver pickup targets and identifies declining roster drop candidates,
cross-referencing Sleeper trending surges and positional scarcity.
"""

import logging
from typing import Any, Optional

from src.config import LeagueConfig
from src.data.injuries import PlayerInjuryInfo
from src.data.trending import TrendingPlayer
from src.espn.roster import ParsedRoster
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_waiver_prompt
from src.intelligence.schemas import WaiverRecommendation, WaiverReport

logger = logging.getLogger(__name__)


def evaluate_waivers(
    league: LeagueConfig,
    week: int,
    roster: ParsedRoster,
    free_agents: list[dict[str, Any]],
    trending_adds: Optional[list[TrendingPlayer]] = None,
    injuries: Optional[list[PlayerInjuryInfo]] = None,
    client: Optional[GeminiIntelligenceClient] = None,
) -> WaiverReport:
    """Evaluate waiver wire targets and identify roster drop candidates.

    Args:
        league: League configuration.
        week: Current week.
        roster: Parsed roster of the user.
        free_agents: List of available free agent dicts (name, position, team, projected_points, percent_owned).
        trending_adds: List of Sleeper trending adds (optional).
        injuries: List of player injuries (optional).
        client: Gemini intelligence client (optional).

    Returns:
        WaiverReport with ranked targets, drop candidates, and strategy.
    """
    # If Gemini client provided, use Gemini Pro for reasoning
    if client is not None:
        roster_data = [
            {
                "name": p.name,
                "pos": p.position,
                "team": p.team,
                "slot": p.slot,
                "projected_pts": p.projected_points,
                "injury": p.injury_status,
            }
            for p in roster.players
        ]

        trending_data = []
        if trending_adds:
            for t in trending_adds:
                trending_data.append(
                    {
                        "rank": t.rank,
                        "name": t.full_name or f"ID:{t.player_id}",
                        "team": t.team,
                        "pos": t.position,
                        "add_count": t.count,
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
                        "notes": inj.injury_notes,
                    }
                )

        prompt = format_waiver_prompt(
            league=league,
            week=week,
            your_roster={"players": roster_data},
            available_players=free_agents[:20],
            trending_adds=trending_data[:15],
            injuries=injury_data[:20],
        )

        try:
            return client.generate_structured(prompt=prompt, response_schema=WaiverReport)
        except Exception as e:
            logger.warning(
                "Gemini waiver evaluation call failed (%s). Falling back to deterministic evaluation.",
                e,
            )

    # Deterministic fallback algorithm when LLM client is None
    drop_candidates = []
    # Identify potential drop candidates from bench
    for p in roster.bench:
        # Extra DST or Kicker on bench is almost always a drop candidate
        if p.position in ("DST", "D/ST", "K"):
            drop_candidates.append(f"{p.name} ({p.position} on bench - reserve kicker/defense)")
        elif p.projected_points < 4.0:
            drop_candidates.append(f"{p.name} (Low projection: {p.projected_points} pts)")

    # If no low projection, suggest the lowest projected bench player
    if not drop_candidates and roster.bench:
        lowest = min(roster.bench, key=lambda p: p.projected_points)
        drop_candidates.append(
            f"{lowest.name} ({lowest.position} - lowest bench projection: {lowest.projected_points} pts)"
        )

    # Rank available free agents by projected points and ownership
    sorted_fa = sorted(
        free_agents,
        key=lambda fa: (
            (fa.get("projected_points", 0.0) * 0.7) + (fa.get("percent_owned", 0.0) * 0.3)
        ),
        reverse=True,
    )

    targets = []
    for i, fa in enumerate(sorted_fa[:5]):
        proj = fa.get("projected_points", 0.0)
        pos = fa.get("position", "UNK")
        name = fa.get("name", "Unknown Player")
        team = fa.get("team", "UNK")

        priority = "MUST_ADD" if i == 0 and proj > 10.0 else ("HIGH" if proj > 8.0 else "MEDIUM")
        drop = drop_candidates[0].split(" (")[0] if drop_candidates else None

        targets.append(
            WaiverRecommendation(
                player_name=name,
                position=pos,
                team=team,
                priority=priority,
                recommended_drop=drop,
                reasoning=f"Top available waiver target at {pos} with {proj} projected points and high usage potential.",
                upside_summary="Immediate starting consideration or high-value depth stash.",
            )
        )

    return WaiverReport(
        league_id=league.league_id,
        week=week,
        targets=targets,
        roster_drop_candidates=drop_candidates,
        overall_waiver_strategy=(
            f"Focus on running back scarcity and high-target volume receivers. "
            f"League '{league.name}' uses traditional rolling waivers — save high priority for clear starters."
        ),
    )
