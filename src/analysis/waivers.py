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
            report = client.generate_structured(prompt=prompt, response_schema=WaiverReport)
            report.league_id = league.league_id
            report.week = week
            if report.coach_verdict == "STAND_PAT" or not report.targets:
                report.coach_verdict = "STAND_PAT"
                report.is_move_recommended = False
                report.targets = []
                report.roster_drop_candidates = []
                if not report.stand_pat_reasoning:
                    report.stand_pat_reasoning = (
                        "Your active starters are healthy and your bench provides crucial high-upside depth. "
                        "None of the available free agents represent a meaningful upgrade over your current assets. "
                        "Preserve your waiver priority and hold your bench."
                    )
            else:
                report.is_move_recommended = True
            return report
        except Exception as e:
            logger.warning(
                "Gemini waiver evaluation call failed (%s). Falling back to deterministic evaluation.",
                e,
            )

    # Deterministic fallback algorithm when LLM client is None
    # 1. Check for genuine droppable liabilities on the bench
    clear_drop_candidates = []
    marginal_drop_candidates = []

    for p in roster.bench:
        # Extra DST or Kicker on bench is a clear droppable roster clogger
        if p.position in ("DST", "D/ST", "K"):
            clear_drop_candidates.append(p)
        elif (p.injury_status or "").upper() in ("OUT", "IR"):
            # Injured bench players not in IR slot
            clear_drop_candidates.append(p)
        elif p.projected_points < 4.0:
            marginal_drop_candidates.append(p)

    # 2. Check if user has an urgent starting lineup hole with no bench replacement
    has_unfilled_starter_hole = False
    unplayable_slots: set[str] = set()
    for s in roster.starters:
        is_out = (s.injury_status or "").upper() in ("OUT", "IR", "SUS", "SUSPENSION", "SUSPENDED", "DOUBTFUL")
        is_bye = getattr(s, "bye_week", 0) == week and week > 0
        if is_out or is_bye:
            unplayable_slots.add((s.slot or s.position).upper())

    if unplayable_slots:
        for slot in unplayable_slots:
            bench_cover = [
                b for b in roster.bench
                if (b.injury_status or "").upper() not in ("OUT", "IR", "SUS", "SUSPENDED")
                and getattr(b, "bye_week", 0) != week
                and (b.position.upper() in slot or slot in ("FLEX", "RB/WR/TE"))
            ]
            if not bench_cover:
                has_unfilled_starter_hole = True
                break

    # 3. Filter and rank available free agents
    sorted_fa = sorted(
        free_agents,
        key=lambda fa: (
            (float(fa.get("projected_points", 0.0) or 0.0) * 0.7)
            + (float(fa.get("percent_owned", 0.0) or 0.0) * 0.3)
        ),
        reverse=True,
    )

    top_fa = sorted_fa[0] if sorted_fa else None
    top_fa_proj = float(top_fa.get("projected_points", 0.0) or 0.0) if top_fa else 0.0

    # 4. Coach decision: Is making a move genuinely warranted?
    # If no starting hole, no clear droppable players, and top FA has mediocre projection:
    best_bench_proj = max([p.projected_points for p in roster.bench], default=0.0)
    bench_is_strong = len(clear_drop_candidates) == 0 and (
        len(marginal_drop_candidates) == 0 or top_fa_proj < 8.0
    )

    if not has_unfilled_starter_hole and bench_is_strong and top_fa_proj <= best_bench_proj:
        return WaiverReport(
            league_id=league.league_id,
            week=week,
            is_move_recommended=False,
            coach_verdict="STAND_PAT",
            stand_pat_reasoning=(
                "🛡️ Roster depth is rock solid. Your active starters are locked in and healthy, and your bench "
                "is loaded with high-upside depth. None of the available waiver options offer a legitimate upgrade "
                "over your current stashes. Churning the roster now would forfeit valuable waiver priority and cost "
                "you valuable bench assets. Hold your depth and stand pat."
            ),
            targets=[],
            roster_drop_candidates=[],
            overall_waiver_strategy=(
                f"🛡️ COACH'S VERDICT: STAND PAT. League '{league.name}' uses traditional rolling waivers. "
                "Save your waiver priority for high-impact injury breakouts or bellcow promotions later in the season. "
                "Do not burn priority on lateral sidegrades."
            ),
        )

    # If an add is genuinely justified:
    drop_pool = clear_drop_candidates or marginal_drop_candidates or sorted(roster.bench, key=lambda p: p.projected_points)
    drop_p = drop_pool[0] if drop_pool else None
    drop_name = drop_p.name if drop_p else None
    drop_candidates_str = [f"{drop_p.name} ({drop_p.position} - {drop_p.projected_points:.1f} pts)"] if drop_p else []

    targets = []
    # Only suggest 1-2 top targets that are genuine improvements
    for i, fa in enumerate(sorted_fa[:3]):
        proj = float(fa.get("projected_points", 0.0) or 0.0)
        pos = fa.get("position", "UNK")
        name = fa.get("name", "Unknown Player")
        team = fa.get("team", "UNK")

        # Skip sub-replacement FA if user has no starting hole
        if not has_unfilled_starter_hole and proj < 7.0:
            continue

        priority = "MUST_ADD" if (has_unfilled_starter_hole and i == 0) or proj > 11.0 else ("HIGH" if proj > 8.5 else "MEDIUM")

        targets.append(
            WaiverRecommendation(
                player_name=name,
                position=pos,
                team=team,
                priority=priority,
                recommended_drop=drop_name,
                reasoning=f"High-value target at {pos} projecting {proj:.1f} points with immediate opportunity.",
                upside_summary="Immediate starting upgrade or high-leverage handcuff stash.",
            )
        )

    if not targets:
        return WaiverReport(
            league_id=league.league_id,
            week=week,
            is_move_recommended=False,
            coach_verdict="STAND_PAT",
            stand_pat_reasoning="No available free agents exceed the performance baseline of your current roster. Stand pat.",
            targets=[],
            roster_drop_candidates=[],
            overall_waiver_strategy="🛡️ COACH'S VERDICT: STAND PAT. Hold your roster and preserve waiver priority.",
        )

    return WaiverReport(
        league_id=league.league_id,
        week=week,
        is_move_recommended=True,
        coach_verdict="EXECUTE_CLAIMS",
        stand_pat_reasoning=None,
        targets=targets,
        roster_drop_candidates=drop_candidates_str,
        overall_waiver_strategy=(
            f"Target high-leverage opportunities at {targets[0].position}. "
            f"Execute targeted claim for {targets[0].player_name} while dropping expendable depth."
        ),
    )
