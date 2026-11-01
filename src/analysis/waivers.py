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
from src.espn.roster import ParsedRoster, get_safe_drop_candidates
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


def _build_waiver_rules_context(league: LeagueConfig, espn_league: Optional[Any] = None) -> str:
    """Inspect live ESPN league settings for FAAB budget vs rolling waiver priority."""
    settings = getattr(espn_league, "settings", None) if espn_league is not None else None
    is_faab = getattr(settings, "faab", None) if settings is not None else None
    if not isinstance(is_faab, bool):
        is_faab = "FAAB" in (league.waiver_type or "").upper()

    my_team = None
    if espn_league is not None and hasattr(espn_league, "teams"):
        teams = getattr(espn_league, "teams", [])
        if isinstance(teams, (list, tuple)):
            my_team = next((t for t in teams if getattr(t, "team_id", None) == league.team_id), None)

    if is_faab:
        raw_budget = getattr(settings, "acquisition_budget", None) if settings is not None else None
        total_budget = int(raw_budget) if isinstance(raw_budget, (int, float)) and raw_budget > 0 else 100
        raw_spent = getattr(my_team, "acquisition_budget_spent", None) if my_team is not None else None
        spent = int(raw_spent) if isinstance(raw_spent, (int, float)) and raw_spent >= 0 else 0
        remaining = max(0, total_budget - spent)
        return f"FAAB Bidding (${remaining}/${total_budget} budget remaining)"

    raw_rank = getattr(my_team, "waiver_rank", None) if my_team is not None else None
    if isinstance(raw_rank, int) and not isinstance(raw_rank, bool) and raw_rank > 0:
        return f"Rolling Waiver Priority (Current Priority: #{raw_rank} of {league.num_teams}; Non-FAAB)"
    return "Rolling Waiver Priority (Non-FAAB)"


def _inspect_ir_slot_lifecycle(
    league: LeagueConfig,
    roster: ParsedRoster,
) -> tuple[Optional[str], Optional[str], list[Any]]:
    """Detect Tier-2 IR lifecycle alerts:
    1. Healthy/active player stuck in an IR slot (ESPN locks all waiver claims until activated!).
    2. Open IR slot(s) while an OUT/IR player sits on the active roster (free pickup without dropping anyone!).

    Returns:
        (ir_lock_warning, ir_open_opportunity, ir_storable_active_players)
    """
    ir_players = list(getattr(roster, "ir", []) or [])
    healthy_in_ir = [
        p
        for p in ir_players
        if (getattr(p, "injury_status", "") or "NORMAL").upper() in ("NORMAL", "ACTIVE", "")
    ]
    ir_lock_warning: Optional[str] = None
    if healthy_in_ir:
        names = ", ".join(f"{p.name} ({p.position})" for p in healthy_in_ir)
        ir_lock_warning = (
            f"🚨 ESPN IR ROSTER LOCK: {names} is healthy/active while still sitting in your IR slot. "
            "ESPN blocks ALL waiver claims and free-agent pickups until you activate or drop healthy players out of IR!"
        )

    max_ir = getattr(league.roster, "ir", 0) or 0
    open_ir_slots = max(0, max_ir - len(ir_players))
    active_roster = list(roster.starters) + list(roster.bench)
    ir_eligible_active = [
        p
        for p in active_roster
        if (getattr(p, "injury_status", "") or "").upper() in ("OUT", "IR", "INJURY_RESERVE")
    ]
    ir_open_opportunity: Optional[str] = None
    if open_ir_slots > 0 and ir_eligible_active:
        elig_names = ", ".join(
            f"{p.name} ({p.position}, {p.injury_status})" for p in ir_eligible_active[:2]
        )
        ir_open_opportunity = (
            f"🏥 FREE ROSTER SPOT AVAILABLE ({len(ir_players)}/{max_ir} IR used): Move {elig_names} "
            "into your open ESPN IR slot first to claim a waiver target with ZERO drop required!"
        )

    return ir_lock_warning, ir_open_opportunity, ir_eligible_active


def evaluate_waivers(
    league: LeagueConfig,
    week: int,
    roster: ParsedRoster,
    free_agents: list[dict[str, Any]],
    trending_adds: Optional[list[TrendingPlayer]] = None,
    injuries: Optional[list[PlayerInjuryInfo]] = None,
    client: Optional[GeminiIntelligenceClient] = None,
    espn_league: Optional[Any] = None,
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
        espn_league: Optional connected ESPN League instance for live FAAB / waiver rank inspection.

    Returns:
        WaiverReport with ranked targets, drop candidates, and strategy.
    """
    fallback_err: Optional[str] = None
    waiver_rules_ctx = _build_waiver_rules_context(league, espn_league)
    ir_lock_warning, ir_open_opportunity, ir_eligible_active = _inspect_ir_slot_lifecycle(
        league, roster
    )
    ir_notes = [n for n in (ir_lock_warning, ir_open_opportunity) if n]

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
        is_bye = s.is_on_bye(week)
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
                and not b.is_on_bye(week)
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

    # Identify safe drop candidates up front (strictly excluding bye-week and protected core/star assets)
    safe_bench_drops = get_safe_drop_candidates(roster, current_week=week)
    protected_or_bye_names = {
        p.name.lower()
        for p in roster.players
        if p.is_on_bye(week) or p.is_protected_asset
    }

    # If Gemini client provided, use Gemini Pro for reasoning
    if client is not None:
        roster_data = [
            {
                "name": p.name,
                "pos": p.position,
                "team": p.team,
                "slot": p.slot,
                "projected_pts": p.projected_points,
                "ros_baseline_ppg": round(max(p.projected_points, getattr(p, "ros_baseline_ppg", 0.0)), 1),
                "percent_owned": round(getattr(p, "percent_owned", 0.0), 1),
                "is_on_bye": p.is_on_bye(week),
                "is_protected_do_not_drop": bool(p.is_on_bye(week) or p.is_protected_asset),
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
            waiver_context=waiver_rules_ctx,
            ir_lifecycle_notes=ir_notes or None,
        )

        try:
            report = client.generate_structured(prompt=prompt, response_schema=WaiverReport)
            report.league_id = league.league_id
            report.week = week
            report.intelligence_backend = client.model
            report.fallback_reason = None

            # Post-validation safety net:
            # 1. Reject any target that is on IR, Out, or injured with 0 projection
            # 2. Never allow dropping a bye-week player or protected core/star asset!
            fallback_safe_drop = (
                f"Move {ir_eligible_active[0].name} to IR (No Drop Needed)"
                if (ir_open_opportunity and ir_eligible_active)
                else (safe_bench_drops[0].name if safe_bench_drops else None)
            )
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

                if t.recommended_drop:
                    raw_drop_low = t.recommended_drop.lower()
                    is_ir_move_directive = "to ir" in raw_drop_low and "move" in raw_drop_low
                    rec_drop_clean = t.recommended_drop.split("(")[0].strip().lower()
                    if not is_ir_move_directive and any(
                        rec_drop_clean == pname or pname in rec_drop_clean
                        for pname in protected_or_bye_names
                    ):
                        logger.warning(
                            "Sanitizer blocked AI recommended_drop '%s' (bye-week or protected star asset). Replacing with '%s'.",
                            t.recommended_drop,
                            fallback_safe_drop,
                        )
                        t.recommended_drop = fallback_safe_drop

                sanitized_targets.append(t)
            report.targets = sanitized_targets

            # Sanitize roster_drop_candidates list against bye-week / protected players
            if report.roster_drop_candidates:
                report.roster_drop_candidates = [
                    d
                    for d in report.roster_drop_candidates
                    if ("to ir" in d.lower() and "move" in d.lower())
                    or not any(pname in d.lower() for pname in protected_or_bye_names)
                ]

            if ir_notes:
                existing_strat = report.overall_waiver_strategy or ""
                prefix_notes = [n for n in ir_notes if n[:24].lower() not in existing_strat.lower()]
                if prefix_notes:
                    report.overall_waiver_strategy = (
                        " ".join(prefix_notes) + (" " + existing_strat if existing_strat else "")
                    ).strip()

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
                    if ir_lock_warning and ir_lock_warning not in (report.stand_pat_reasoning or ""):
                        report.stand_pat_reasoning = f"{ir_lock_warning} {report.stand_pat_reasoning}"
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
    # 1. Check for genuine droppable liabilities on the bench (strictly excluding bye-week and protected assets)
    clear_drop_candidates = []
    marginal_drop_candidates = []

    for p in roster.bench:
        if p.position in ("DST", "D/ST", "K"):
            # Extra DST or Kicker on bench is a clear droppable roster clogger
            clear_drop_candidates.append(p)
        elif p.is_on_bye(week) or p.is_protected_asset:
            # NEVER drop a player on a bye week or a protected star/starter asset!
            continue
        elif (p.injury_status or "").upper() in ("OUT", "IR"):
            # Injured low-end bench players not in IR slot
            clear_drop_candidates.append(p)
        elif max(p.projected_points, getattr(p, "ros_baseline_ppg", 0.0)) < 6.5:
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
    # If an open IR slot allows stashing an OUT/IR active-roster player for FREE, we don't need to drop anyone!
    has_free_ir_stash_move = bool(ir_open_opportunity and ir_eligible_active and top_fa_proj >= 7.0)
    best_bench_proj = max([p.projected_points for p in roster.bench], default=0.0)
    bench_is_strong = len(clear_drop_candidates) == 0 and (
        len(marginal_drop_candidates) == 0 or top_fa_proj < 8.0
    )

    ir_prefix = (" ".join(ir_notes) + " ") if ir_notes else ""

    capital_term = "FAAB budget" if "FAAB" in waiver_rules_ctx and "Non-FAAB" not in waiver_rules_ctx else "waiver priority"

    if (
        not has_unfilled_starter_hole
        and not has_free_ir_stash_move
        and bench_is_strong
        and top_fa_proj <= best_bench_proj
    ):
        return WaiverReport(
            league_id=league.league_id,
            week=week,
            is_move_recommended=False,
            coach_verdict="STAND_PAT",
            stand_pat_reasoning=(
                f"{ir_lock_warning + ' ' if ir_lock_warning else ''}"
                "🛡️ Roster depth is rock solid. Your active starters are locked in and healthy, and your bench "
                "is loaded with high-upside depth. None of the available waiver options offer a legitimate upgrade "
                f"over your current stashes. Churning the roster now would forfeit valuable {capital_term} and cost "
                "you valuable bench assets. Hold your depth and stand pat."
            ),
            targets=[],
            roster_drop_candidates=[],
            overall_waiver_strategy=(
                f"{ir_prefix}🛡️ COACH'S VERDICT: STAND PAT. League '{league.name}' ({waiver_rules_ctx}). "
                f"Save your {capital_term} for high-impact injury breakouts or bellcow promotions later in the season. "
                f"Do not burn {capital_term} on lateral sidegrades."
            ),
        )

    # If an add is genuinely justified:
    if ir_open_opportunity and ir_eligible_active:
        ir_target_p = ir_eligible_active[0]
        drop_name = f"Move {ir_target_p.name} to IR (No Drop Needed)"
        drop_candidates_str = [
            f"🏥 Move {ir_target_p.name} ({ir_target_p.position}, {ir_target_p.injury_status}) to open IR slot — 0 drops required"
        ]
    else:
        drop_pool = clear_drop_candidates or marginal_drop_candidates or safe_bench_drops
        drop_p = drop_pool[0] if drop_pool else None
        drop_name = drop_p.name if drop_p else None
        drop_candidates_str = (
            [f"{drop_p.name} ({drop_p.position} - {drop_p.projected_points:.1f} pts)"]
            if drop_p
            else []
        )

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
            stand_pat_reasoning=(
                f"{ir_lock_warning + ' ' if ir_lock_warning else ''}"
                "No available free agents exceed the performance baseline of your current roster. Stand pat."
            ),
            targets=[],
            roster_drop_candidates=[],
            overall_waiver_strategy=(
                f"{ir_prefix}🛡️ COACH'S VERDICT: STAND PAT ({waiver_rules_ctx}). Hold your roster and preserve waiver capital."
            ),
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
            f"{ir_prefix}Target high-leverage opportunities at {targets[0].position} ({waiver_rules_ctx}). "
            f"Execute targeted claim for {targets[0].player_name} "
            + (
                f"after moving {ir_eligible_active[0].name} to your open IR slot."
                if (ir_open_opportunity and ir_eligible_active)
                else "while dropping expendable depth."
            )
        ),
        intelligence_backend="deterministic_fallback",
        fallback_reason=fallback_err or "Gemini client was not provided",
    )
