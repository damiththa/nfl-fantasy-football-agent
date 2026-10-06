"""
Waiver Wire Engine.
Ranks waiver pickup targets and identifies declining roster drop candidates,
cross-referencing Sleeper trending surges and positional scarcity.
"""

import logging
from typing import Any, Optional

from src.config import LeagueConfig
from src.data.injuries import PlayerInjuryInfo, normalize_name
from src.data.trending import TrendingPlayer
from src.espn.roster import ParsedRoster
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_waiver_prompt
from src.intelligence.schemas import WaiverRecommendation, WaiverReport

logger = logging.getLogger(__name__)

INELIGIBLE_INJURY_STATUSES = {
    "INJURY_RESERVE",
    "IR",
    "OUT",
    "PUP",
    "SUS",
    "SUSPENSION",
    "SUSPENDED",
}


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
    fallback_err: Optional[str] = None

    # Build injury lookup map
    injuries_map: dict[str, PlayerInjuryInfo] = {}
    if injuries:
        for inj in injuries:
            if inj.full_name:
                injuries_map[normalize_name(inj.full_name)] = inj

    # Filter out ineligible, IR, Out, or season-ending injured players
    eligible_free_agents: list[dict[str, Any]] = []
    for fa in free_agents:
        fa_copy = dict(fa)
        name = fa_copy.get("name", "")
        espn_inj = str(fa_copy.get("injury_status") or "ACTIVE").upper().strip()
        is_injured = bool(fa_copy.get("injured", False))
        proj_pts = float(fa_copy.get("projected_points", 0.0) or 0.0)

        sl_info = injuries_map.get(normalize_name(name)) if injuries_map else None
        sl_status = str(getattr(sl_info, "injury_status", "") or fa_copy.get("sleeper_status") or "").upper().strip()
        sl_notes = str(getattr(sl_info, "injury_notes", "") or fa_copy.get("injury_notes") or "").lower()

        fa_copy["injury_status"] = espn_inj
        if sl_status:
            fa_copy["sleeper_status"] = sl_status
        if sl_notes:
            fa_copy["injury_notes"] = sl_notes

        # 1. Hard filter: Inactive/IR/Out/PUP/Suspended in ESPN or Sleeper
        if espn_inj in INELIGIBLE_INJURY_STATUSES or sl_status in INELIGIBLE_INJURY_STATUSES:
            logger.info("Excluding waiver candidate %s: inactive status (ESPN=%s, Sleeper=%s)", name, espn_inj, sl_status)
            continue

        # 2. Hard filter: marked injured with 0 projection
        if is_injured and proj_pts <= 0.0:
            logger.info("Excluding waiver candidate %s: marked injured with 0.0 projected points", name)
            continue

        # 3. Hard filter: severe injury notes with 0 projection
        if proj_pts <= 0.0 and any(kw in sl_notes for kw in ("surgery", "acl", "mcl", "achilles", "season-ending", "ir", "reserve")):
            logger.info("Excluding waiver candidate %s: severe injury notes (%s) with 0.0 projected points", name, sl_notes)
            continue

        eligible_free_agents.append(fa_copy)

    # 1. Analyze starting lineup for unplayable slots (bye weeks, 0.0 projection, injury)
    has_unfilled_starter_hole = False
    streaming_needs: list[str] = []
    starter_holes_detail: list[dict[str, Any]] = []

    for s in roster.starters:
        is_out = (s.injury_status or "").upper() in ("OUT", "IR", "SUS", "SUSPENSION", "SUSPENDED", "DOUBTFUL")
        is_bye = getattr(s, "bye_week", 0) == week and week > 0
        is_zero_proj = not getattr(s, "has_played", False) and getattr(s, "projected_points", 0.0) <= 0.0

        if is_out or is_bye or is_zero_proj:
            slot_norm = (s.slot or s.position).upper().replace("/", "")
            if slot_norm in ("DEF", "DST"):
                slot_norm = "DST"
            elif slot_norm in ("RBWRTE", "FLEX"):
                slot_norm = "FLEX"

            bench_cover = [
                b for b in roster.bench
                if (b.injury_status or "").upper() not in ("OUT", "IR", "SUS", "SUSPENDED", "DOUBTFUL")
                and getattr(b, "bye_week", 0) != week
                and not (not getattr(b, "has_played", False) and getattr(b, "projected_points", 0.0) <= 0.0)
                and (
                    b.position.upper() == slot_norm
                    or (b.position.upper() in ("DEF", "DST") and slot_norm == "DST")
                    or (slot_norm == "FLEX" and b.position.upper() in ("RB", "WR", "TE"))
                )
            ]

            has_cov = len(bench_cover) > 0
            pos_need = "DST" if slot_norm == "DST" else s.position.upper()
            if not has_cov:
                has_unfilled_starter_hole = True
                if pos_need not in streaming_needs:
                    streaming_needs.append(pos_need)

            starter_holes_detail.append({
                "starter_name": s.name,
                "slot": s.slot,
                "position": s.position,
                "team": s.team,
                "projected_points": s.projected_points,
                "injury_status": s.injury_status,
                "has_bench_cover": has_cov,
                "bench_cover_players": [b.name for b in bench_cover],
                "action_required": (
                    f"Lineup swap available on bench ({', '.join(b.name for b in bench_cover[:2])})"
                    if has_cov
                    else f"URGENT STREAMING WAIVER CLAIM REQUIRED (0 bench backups for {s.position})"
                ),
            })

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
                        "practice": inj.practice_participation,
                        "body_part": inj.injury_body_part,
                        "notes": inj.injury_notes,
                    }
                )

        # Ensure streaming positions (K, DST) have their top options in available_players_data
        streaming_fa_candidates = []
        for need in streaming_needs:
            need_pos = "DST" if need in ("DEF", "DST", "D/ST") else need
            pos_matches = [
                fa for fa in eligible_free_agents
                if fa.get("position", "").upper() == need_pos
                or (need_pos == "DST" and fa.get("position", "").upper() in ("DEF", "DST", "D/ST"))
            ]
            streaming_fa_candidates.extend(pos_matches[:3])

        seen_names = set()
        combined_fa_pool = []
        for fa in streaming_fa_candidates + eligible_free_agents:
            fa_name = (fa.get("name") or "").lower()
            if fa_name and fa_name not in seen_names:
                seen_names.add(fa_name)
                combined_fa_pool.append(fa)

        available_players_data = [
            {
                "name": fa["name"],
                "position": fa["position"],
                "team": fa.get("team") or fa.get("proTeam", "FA"),
                "projected_points": fa.get("projected_points", 0.0),
                "percent_owned": fa.get("percent_owned", 0.0),
                "injury_status": fa.get("injury_status", "ACTIVE"),
            }
            for fa in combined_fa_pool[:40]
        ]

        prompt = format_waiver_prompt(
            league=league,
            week=week,
            your_roster={"players": roster_data},
            available_players=available_players_data,
            trending_adds=trending_data[:20],
            injuries=injury_data[:20],
            starter_holes=starter_holes_detail,
        )

        try:
            report = client.generate_structured(prompt=prompt, response_schema=WaiverReport)
            report.league_id = league.league_id
            report.week = week
            report.intelligence_backend = client.model
            report.fallback_reason = None

            # Post-validation safety net: Reject any target that is on IR, Out, or injured with 0 projection
            sanitized_targets = []
            for t in report.targets:
                fa_match = next((fa for fa in free_agents if fa.get("name", "").lower() == t.player_name.lower()), None)
                if fa_match:
                    espn_inj = str(fa_match.get("injury_status") or "").upper().strip()
                    sl_info = injuries_map.get(normalize_name(fa_match.get("name", ""))) if injuries_map else None
                    sl_status = str(getattr(sl_info, "injury_status", "") or fa_match.get("sleeper_status") or "").upper().strip()
                    is_inj = bool(fa_match.get("injured", False))
                    proj = float(fa_match.get("projected_points", 0.0) or 0.0)
                    if (
                        espn_inj in INELIGIBLE_INJURY_STATUSES
                        or sl_status in INELIGIBLE_INJURY_STATUSES
                        or (is_inj and proj <= 0.0)
                    ):
                        logger.warning(
                            "Sanitizer rejected AI waiver target %s: player is on IR/injured (ESPN=%s, Sleeper=%s, proj=%.1f)",
                            t.player_name, espn_inj, sl_status, proj
                        )
                        continue
                sanitized_targets.append(t)
            report.targets = sanitized_targets

            if report.coach_verdict == "STAND_PAT" or not report.targets:
                if has_unfilled_starter_hole:
                    logger.warning(
                        "Gemini returned STAND_PAT or empty targets, but roster has unfilled starter hole (%s). Overriding with deterministic streaming targets.",
                        streaming_needs,
                    )
                else:
                    report.coach_verdict = "STAND_PAT"
                    report.is_move_recommended = False
                    report.targets = []
                    report.roster_drop_candidates = []
                    if not report.stand_pat_reasoning:
                        report.stand_pat_reasoning = (
                            "Your active starters are healthy and your bench provides crucial high-upside depth. "
                            "None of the available healthy free agents represent a meaningful upgrade over your current assets. "
                            "Preserve your waiver priority and hold your bench."
                        )
                    return report
            else:
                report.is_move_recommended = True
                return report
        except Exception as e:
            fallback_err = str(e)
            logger.warning(
                "Gemini waiver evaluation call failed (%s). Falling back to deterministic evaluation.",
                e,
            )

    # Deterministic fallback algorithm when LLM client is None or when AI STAND_PAT is overridden by unfilled starter hole
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

    # 2. Filter and rank available free agents
    sorted_fa = sorted(
        eligible_free_agents,
        key=lambda fa: (
            (float(fa.get("projected_points", 0.0) or 0.0) * 0.7)
            + (float(fa.get("percent_owned", 0.0) or 0.0) * 0.3)
        ),
        reverse=True,
    )

    top_fa = sorted_fa[0] if sorted_fa else None
    top_fa_proj = float(top_fa.get("projected_points", 0.0) or 0.0) if top_fa else 0.0

    # 3. Coach decision: Is making a move genuinely warranted?
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
    # If we have streaming needs (e.g. K, DST), first pick the best available free agent for each streaming need!
    if streaming_needs:
        for need in streaming_needs:
            need_norm = "DST" if need in ("DEF", "DST", "D/ST") else need
            pos_matches = [
                fa for fa in eligible_free_agents
                if (fa.get("position", "").upper() == need_norm)
                or (need_norm == "DST" and fa.get("position", "").upper() in ("DEF", "DST", "D/ST"))
                or (need_norm == "FLEX" and fa.get("position", "").upper() in ("RB", "WR", "TE"))
            ]
            if pos_matches:
                pos_matches.sort(
                    key=lambda fa: (
                        (float(fa.get("projected_points", 0.0) or 0.0) * 0.7)
                        + (float(fa.get("percent_owned", 0.0) or 0.0) * 0.3)
                    ),
                    reverse=True,
                )
                best_streaming_fa = pos_matches[0]
                s_name = best_streaming_fa.get("name", "Unknown")
                s_pos = best_streaming_fa.get("position", need)
                s_team = best_streaming_fa.get("team", "UNK")
                s_proj = float(best_streaming_fa.get("projected_points", 0.0) or 0.0)
                targets.append(
                    WaiverRecommendation(
                        player_name=s_name,
                        position=s_pos,
                        team=s_team,
                        priority="MUST_ADD",
                        recommended_drop=drop_name,
                        reasoning=(
                            f"🚨 Urgent streaming addition for starting {need} slot (Week {week} bye week / zero projection). "
                            f"Projecting {s_proj:.1f} points with immediate starting utility."
                        ),
                        upside_summary=f"Plugs critical starting hole at {need} to prevent taking an automatic 0.0 in matchup.",
                    )
                )

    # Then append other high-value upgrades if space permits
    for i, fa in enumerate(sorted_fa[:3]):
        if any(t.player_name.lower() == fa.get("name", "").lower() for t in targets):
            continue
        proj = float(fa.get("projected_points", 0.0) or 0.0)
        pos = fa.get("position", "UNK")
        name = fa.get("name", "Unknown Player")
        team = fa.get("team", "UNK")

        # Skip players with zero projection or sub-replacement FA if user has no starting hole
        if proj <= 0.0 or (not has_unfilled_starter_hole and proj < 7.0):
            continue

        priority = "MUST_ADD" if (has_unfilled_starter_hole and not targets) or proj > 11.0 else ("HIGH" if proj > 8.5 else "MEDIUM")

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
            intelligence_backend="deterministic_fallback",
            fallback_reason=fallback_err or "Gemini client was not provided",
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
        intelligence_backend="deterministic_fallback",
        fallback_reason=fallback_err or "Gemini client was not provided",
    )
