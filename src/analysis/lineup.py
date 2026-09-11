"""
Start/Sit Lineup Optimizer.
Applies game theory (floor vs ceiling) depending on matchup point margins,
cross-referencing injuries, Vegas totals, and weather conditions.
"""

import logging
import zoneinfo
from datetime import datetime
from typing import Any, Optional

from src.config import LeagueConfig
from src.data.injuries import PlayerInjuryInfo
from src.data.vegas import GameOdds, get_player_game_odds, normalize_team_abbr
from src.data.weather import GameWeather
from src.espn.matchup import MatchupData
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_lineup_prompt
from src.intelligence.schemas import (
    CurrentRosterPlayer,
    LineupHoleAlert,
    LineupRecommendation,
    StartSitDecision,
)

logger = logging.getLogger(__name__)


def sort_starters_by_lineup_order(
    starters: list[StartSitDecision], league: LeagueConfig
) -> list[StartSitDecision]:
    """Sort recommended starters strictly into standard fantasy roster order:
    QB, RB, RB, WR, WR, TE, FLEX, [FLEX], D/ST, [K].
    """
    qbs = [
        s for s in starters if s.position.upper() in ("QB", "TQB") or "QB" in s.position.upper()
    ]
    rbs = [s for s in starters if s.position.upper() == "RB"]
    wrs = [s for s in starters if s.position.upper() == "WR"]
    tes = [s for s in starters if s.position.upper() == "TE"]
    dsts = [
        s
        for s in starters
        if s.position.upper() in ("DST", "D/ST", "DEF") or "DEF" in s.position.upper()
    ]
    kickers = [s for s in starters if s.position.upper() in ("K", "PK")]

    others = [
        s
        for s in starters
        if s not in qbs
        and s not in rbs
        and s not in wrs
        and s not in tes
        and s not in dsts
        and s not in kickers
    ]

    ordered: list[StartSitDecision] = []

    # 1. QB
    ordered.extend(qbs[: league.roster.qb])
    remaining_qbs = qbs[league.roster.qb :]

    # 2. RB, RB
    ordered.extend(rbs[: league.roster.rb])
    remaining_rbs = rbs[league.roster.rb :]

    # 3. WR, WR
    ordered.extend(wrs[: league.roster.wr])
    remaining_wrs = wrs[league.roster.wr :]

    # 4. TE
    ordered.extend(tes[: league.roster.te])
    remaining_tes = tes[league.roster.te :]

    # 5. FLEX, FLEX (Remaining RBs, WRs, TEs, Others)
    flex_pool = remaining_rbs + remaining_wrs + remaining_tes + remaining_qbs + others
    for s in flex_pool[: league.roster.flex]:
        ordered.append(s.model_copy(update={"position": "FLEX"}))

    # 6. K (Chips Ahoy - placed before D/ST so D/ST is always the absolute last row)
    if league.roster.k:
        ordered.extend(kickers[: league.roster.k])

    # 7. D/ST (ALWAYS the absolute last row in every league)
    for s in dsts[: league.roster.dst]:
        ordered.append(s.model_copy(update={"position": "D/ST"}))

    # In case any player was missed, append
    added_names = {s.player_name.lower() for s in ordered}
    for s in starters:
        if s.player_name.lower() not in added_names:
            ordered.append(s)

    return ordered


def get_player_matchup_info(
    team: str,
    odds: Optional[list[GameOdds]] = None,
    bye_week: int = 0,
    current_week: int = 0,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Determine opponent, home_away, matchup_display, and game_time for a player.

    Returns:
        (opponent, home_away, matchup_display, game_time)
    """
    if not team or team.upper() in ("UNK", "FA", "FREE AGENT", "NONE"):
        return None, None, None, None

    if odds:
        game = get_player_game_odds(team, odds)
        if game:
            norm_team = normalize_team_abbr(team)
            norm_home = normalize_team_abbr(game.home_team)
            is_home = norm_team == norm_home
            home_away = "HOME" if is_home else "AWAY"
            opponent = game.away_team if is_home else game.home_team
            prefix = "vs." if is_home else "@"
            matchup_display = f"{prefix} {opponent}"

            # Format game_time in Eastern Time
            eastern = zoneinfo.ZoneInfo("America/New_York")
            if game.game_time.tzinfo is None:
                game_time_dt = game.game_time.replace(tzinfo=eastern)
            else:
                game_time_dt = game.game_time.astimezone(eastern)

            time_str = game_time_dt.strftime("%I:%M %p").lstrip("0")
            day_str = game_time_dt.strftime("%a")
            game_time_display = f"{day_str} {time_str} ET"

            return opponent, home_away, matchup_display, game_time_display

        # If odds list has full week schedule (>= 10 games) and team is not in it, it's on Bye
        if len(odds) >= 10:
            return None, None, "BYE", "Bye Week"

    if bye_week and current_week and bye_week == current_week:
        return None, None, "BYE", "Bye Week"

    return None, None, None, None


def detect_lineup_holes_and_solutions(
    roster: ParsedRoster,
    league: LeagueConfig,
    current_week: int = 0,
    espn_league: Optional[Any] = None,
    injuries: Optional[list[PlayerInjuryInfo]] = None,
    odds: Optional[list[GameOdds]] = None,
) -> list[LineupHoleAlert]:
    """Detect unplayable starters (OUT, IR, SUS, Bye) or vacant slots,
    and generate 3-tier solutions: 1. Bench Option, 2. Waiver Pickup, 3. Trade Target.
    """
    alerts: list[LineupHoleAlert] = []

    # Map injuries by player name
    injury_map = {}
    if injuries:
        for inj in injuries:
            injury_map[inj.full_name.lower()] = inj

    # 1. Inspect all current starters on ESPN for unplayable status
    unplayable_starters: list[tuple[str, str, Optional[str], str]] = []  # (slot, pos, name, reason)
    accounted_unplayable_slots: dict[str, int] = {}

    for p in roster.starters:
        slot_name = (p.slot or "").upper().replace("/", "")
        if slot_name in ("DEF", "DST"):
            slot_name = "DST"
        elif slot_name in ("RBWRTE", "FLEX"):
            slot_name = "FLEX"
        elif slot_name not in ("QB", "RB", "WR", "TE", "FLEX", "K", "DST"):
            slot_name = p.position.upper()

        is_out = (p.injury_status or "").upper() in ("OUT", "IR", "SUS", "SUSPENSION", "SUSPENDED", "DOUBTFUL")
        is_bye = getattr(p, "bye_week", 0) == current_week and current_week > 0

        inj_info = injury_map.get(p.name.lower())
        if inj_info and (inj_info.injury_status or "").upper() in ("OUT", "IR", "DOUBTFUL"):
            is_out = True

        if is_out or is_bye:
            if is_bye:
                reason = f"BYE WEEK (Week {current_week})"
            elif (p.injury_status or "").upper() in ("SUS", "SUSPENSION", "SUSPENDED"):
                reason = "SUSPENDED"
            elif inj_info and inj_info.injury_status:
                reason = f"{inj_info.injury_status}: {inj_info.injury_notes or 'Out for game'}"
            else:
                reason = f"{p.injury_status or 'OUT'}: Inactive / Injured"

            unplayable_starters.append((slot_name, p.position.upper(), p.name, reason))
            accounted_unplayable_slots[slot_name] = accounted_unplayable_slots.get(slot_name, 0) + 1

    # 2. Check for physically vacant slots on ESPN
    required_counts = {
        "QB": league.roster.qb,
        "RB": league.roster.rb,
        "WR": league.roster.wr,
        "TE": league.roster.te,
        "FLEX": league.roster.flex,
        "K": league.roster.k,
        "DST": league.roster.dst,
    }

    # Count how many slots are filled on ESPN
    total_filled_slots: dict[str, int] = {}
    for p in roster.starters:
        s_norm = (p.slot or "").upper().replace("/", "")
        if s_norm in ("DEF", "DST"):
            s_norm = "DST"
        elif s_norm in ("RBWRTE", "FLEX"):
            s_norm = "FLEX"
        elif s_norm not in required_counts:
            s_norm = p.position.upper()
        total_filled_slots[s_norm] = total_filled_slots.get(s_norm, 0) + 1

    vacant_holes: list[tuple[str, str, Optional[str], str]] = []
    for slot_name, req_count in required_counts.items():
        filled = total_filled_slots.get(slot_name, 0)
        if filled < req_count:
            for _ in range(req_count - filled):
                pos_target = "WR" if slot_name == "FLEX" else slot_name
                vacant_holes.append((slot_name, pos_target, None, "VACANT ON ESPN: Slot is currently unfilled"))

    all_holes = unplayable_starters + vacant_holes
    if not all_holes:
        return []

    # Build free agent lookup if espn_league available
    free_agents_by_pos: dict[str, list[Any]] = {}
    if espn_league and hasattr(espn_league, "free_agents"):
        try:
            fa_list = espn_league.free_agents(size=50)
            for fa in fa_list:
                pos = (getattr(fa, "position", "") or "FLEX").upper()
                free_agents_by_pos.setdefault(pos, []).append(fa)
        except Exception:
            pass

    # Build league teams for trade surplus lookup
    other_teams_by_surplus: dict[str, list[tuple[Any, Any]]] = {}  # pos -> [(team, player)]
    if espn_league and hasattr(espn_league, "teams"):
        try:
            for t in espn_league.teams:
                if getattr(t, "team_id", None) == league.team_id:
                    continue
                team_players_by_pos: dict[str, list[Any]] = {}
                for p in getattr(t, "roster", []):
                    pos = (getattr(p, "position", "") or "").upper()
                    pts = float(getattr(p, "projected_points", 0.0) or 0.0)
                    if pts >= 8.0:
                        team_players_by_pos.setdefault(pos, []).append(p)
                for pos, pl_list in team_players_by_pos.items():
                    if len(pl_list) >= 2:
                        for p in pl_list:
                            other_teams_by_surplus.setdefault(pos, []).append((t, p))
        except Exception:
            pass

    # Find candidate drop from user's bench (lowest projected healthy player not on IR)
    bench_candidates = [
        b for b in roster.bench
        if (b.injury_status or "").upper() not in ("IR",)
    ]
    bench_candidates.sort(key=lambda b: getattr(b, "projected_points", 0.0) or 0.0)
    drop_candidate = bench_candidates[0] if bench_candidates else None

    # Track already recommended bench promotions
    used_bench_names: set[str] = set()

    for slot_name, pos_target, starter_name, reason in all_holes:
        # Tier 1: Bench Promotion
        eligible_bench: list[RosterPlayer] = []
        for b in roster.bench:
            if b.name.lower() in used_bench_names:
                continue
            b_pos = (b.position or "").upper().replace("/", "")
            if b_pos in ("DEF", "DST"):
                b_pos = "DST"

            is_eligible = False
            if slot_name == "FLEX" and b_pos in ("RB", "WR", "TE"):
                is_eligible = True
            elif slot_name == b_pos:
                is_eligible = True
            elif pos_target == b_pos:
                is_eligible = True

            b_out = (b.injury_status or "").upper() in ("OUT", "IR", "SUS", "SUSPENSION", "SUSPENDED", "DOUBTFUL")
            b_bye = getattr(b, "bye_week", 0) == current_week and current_week > 0
            if is_eligible and not b_out and not b_bye:
                eligible_bench.append(b)

        eligible_bench.sort(key=lambda b: getattr(b, "projected_points", 0.0) or 0.0, reverse=True)

        if eligible_bench:
            best_bench = eligible_bench[0]
            used_bench_names.add(best_bench.name.lower())
            bench_rec = (
                f"⬆️ Promote {best_bench.name} ({best_bench.position}, {best_bench.projected_points:.1f} pts) "
                f"from your bench into starting {slot_name} slot."
            )
        else:
            bench_rec = (
                f"⚠️ No healthy, eligible bench replacement on your roster for {slot_name}. "
                "Bench depth is exhausted; external roster acquisition required."
            )

        # Tier 2: Waiver Wire Pickup
        pos_for_fa = ["RB", "WR", "TE"] if slot_name == "FLEX" else [pos_target]
        fa_options: list[Any] = []
        for p_fa in pos_for_fa:
            fa_options.extend(free_agents_by_pos.get(p_fa, []))
        fa_options.sort(key=lambda fa: float(getattr(fa, "projected_points", 0.0) or 0.0), reverse=True)

        if fa_options:
            top_fa = fa_options[0]
            top_fa_pts = float(getattr(top_fa, "projected_points", 0.0) or 0.0)
            top_fa_team = getattr(top_fa, "proTeam", "FA")
            top_fa_pos = getattr(top_fa, "position", pos_target)
            if drop_candidate and (starter_name is None or drop_candidate.name.lower() != starter_name.lower()):
                waiver_rec = (
                    f"🎯 Claim {top_fa.name} ({top_fa_pos} - {top_fa_team}, {top_fa_pts:.1f} pts). "
                    f"Suggested Drop: {drop_candidate.name} ({drop_candidate.position}, {drop_candidate.projected_points:.1f} pts)."
                )
            else:
                waiver_rec = f"🎯 Claim {top_fa.name} ({top_fa_pos} - {top_fa_team}, {top_fa_pts:.1f} pts) from free agency."
        else:
            if drop_candidate:
                waiver_rec = (
                    f"🎯 Scan waiver wire for top available {pos_target}. "
                    f"Suggested Drop: {drop_candidate.name} ({drop_candidate.position}, {drop_candidate.projected_points:.1f} pts)."
                )
            else:
                waiver_rec = f"🎯 Scan waiver wire for top available {pos_target} streaming starter."

        # Tier 3: Trade Target Solution
        trade_candidates = other_teams_by_surplus.get(pos_target, [])
        if slot_name == "FLEX" and not trade_candidates:
            trade_candidates = other_teams_by_surplus.get("RB", []) + other_teams_by_surplus.get("WR", [])

        if trade_candidates:
            target_team, target_player = trade_candidates[0]
            t_pts = float(getattr(target_player, "projected_points", 0.0) or 0.0)
            t_name = getattr(target_team, "team_name", "Manager")
            trade_rec = (
                f"🤝 Target {target_player.name} ({target_player.position}, {t_pts:.1f} pts) from '{t_name}' "
                f"(they carry surplus at {pos_target}); offer your bench depth to secure an immediate starter."
            )
        else:
            trade_rec = (
                f"🤝 Propose trade targeting rival managers carrying surplus {pos_target} depth "
                "in exchange for your bench assets."
            )

        alerts.append(
            LineupHoleAlert(
                slot=slot_name,
                current_status=reason,
                current_player_name=starter_name,
                bench_recommendation=bench_rec,
                waiver_recommendation=waiver_rec,
                trade_recommendation=trade_rec,
            )
        )

    return alerts


def enrich_lineup_recommendation_with_espn_status(
    rec: LineupRecommendation,
    roster: ParsedRoster,
    league: LeagueConfig,
    odds: Optional[list[GameOdds]] = None,
    current_week: int = 0,
    espn_league: Optional[Any] = None,
    injuries: Optional[list[PlayerInjuryInfo]] = None,
) -> LineupRecommendation:
    """Populate current ESPN slots, detect vacant starting slots, detect starting lineup holes (injuries/bye),
    generate actionable swap instructions, and populate game date, kickoff time, and home/away status.
    """
    player_current_slots = {p.name.lower(): p.slot for p in roster.players}
    roster_map = {p.name.lower(): p for p in roster.players}

    # Update recommended starters
    for s in rec.recommended_starters:
        s.current_slot = player_current_slots.get(s.player_name.lower(), "Bench")
        if s.current_slot in ("Bench", "BE"):
            s.alignment = "SWAP_TO_START"
        else:
            s.alignment = "ALIGNED"
        p_obj = roster_map.get(s.player_name.lower())
        bye = getattr(p_obj, "bye_week", 0) if p_obj else 0
        opp, ha, m_disp, g_time = get_player_matchup_info(s.team, odds, bye, current_week)
        s.opponent = opp
        s.home_away = ha
        s.matchup_display = m_disp
        s.game_time = g_time
        if p_obj:
            s.actual_points = getattr(p_obj, "actual_points", None)
            s.has_played = getattr(p_obj, "has_played", False)

    # Update bench players
    for b in rec.bench_players:
        b.current_slot = player_current_slots.get(b.player_name.lower(), "Bench")
        if b.current_slot not in ("Bench", "BE", "IR"):
            b.alignment = "MOVE_TO_BENCH"
        else:
            b.alignment = "ALIGNED"
        p_obj = roster_map.get(b.player_name.lower())
        bye = getattr(p_obj, "bye_week", 0) if p_obj else 0
        opp, ha, m_disp, g_time = get_player_matchup_info(b.team, odds, bye, current_week)
        b.opponent = opp
        b.home_away = ha
        b.matchup_display = m_disp
        b.game_time = g_time
        if p_obj:
            b.actual_points = getattr(p_obj, "actual_points", None)
            b.has_played = getattr(p_obj, "has_played", False)

    # Detect emergency lineup holes (injuries, OUT, IR, SUS, Bye weeks, Vacant)
    rec.lineup_hole_alerts = detect_lineup_holes_and_solutions(
        roster=roster,
        league=league,
        current_week=current_week,
        espn_league=espn_league,
        injuries=injuries,
        odds=odds,
    )

    # Detect vacant starting slots on ESPN
    current_starter_slots: dict[str, int] = {}
    for p in roster.starters:
        slot = (p.slot or "").upper().replace("/", "")
        if slot in ("DEF", "DST"):
            slot = "DST"
        elif slot in ("RBWRTE", "FLEX"):
            slot = "FLEX"
        current_starter_slots[slot] = current_starter_slots.get(slot, 0) + 1

    vacant = []
    if current_starter_slots.get("QB", 0) < league.roster.qb:
        vacant.append(f"QB ({league.roster.qb - current_starter_slots.get('QB', 0)} empty)")
    if current_starter_slots.get("RB", 0) < league.roster.rb:
        vacant.append(f"RB ({league.roster.rb - current_starter_slots.get('RB', 0)} empty)")
    if current_starter_slots.get("WR", 0) < league.roster.wr:
        vacant.append(f"WR ({league.roster.wr - current_starter_slots.get('WR', 0)} empty)")
    if current_starter_slots.get("TE", 0) < league.roster.te:
        vacant.append(f"TE ({league.roster.te - current_starter_slots.get('TE', 0)} empty)")
    if current_starter_slots.get("FLEX", 0) < league.roster.flex:
        vacant.append(f"FLEX ({league.roster.flex - current_starter_slots.get('FLEX', 0)} empty)")
    if league.roster.k and current_starter_slots.get("K", 0) < league.roster.k:
        vacant.append(f"Kicker ({league.roster.k - current_starter_slots.get('K', 0)} empty)")
    if current_starter_slots.get("DST", 0) < league.roster.dst:
        vacant.append(f"D/ST ({league.roster.dst - current_starter_slots.get('DST', 0)} empty)")

    rec.vacant_slots = vacant

    # Detect suboptimal starters currently started on ESPN
    suboptimal = []
    for b in rec.bench_players:
        if b.current_slot not in ("Bench", "BE", "IR"):
            suboptimal.append(
                f"{b.player_name} ({b.position}) — currently in ESPN '{b.current_slot}' slot, but should BENCH: {b.reasoning}"
            )
    rec.suboptimal_starters = suboptimal

    # Generate explicit actionable swap instructions (filter out players whose games are locked)
    locked_starter_names = {
        p.name.lower() for p in roster.starters if getattr(p, "has_played", False)
    }
    needs_bench = [
        b for b in rec.bench_players
        if b.alignment == "MOVE_TO_BENCH" and b.player_name.lower() not in locked_starter_names
    ]
    needs_start = [s for s in rec.recommended_starters if s.alignment == "SWAP_TO_START"]

    swaps = []
    for ns, nb in zip(needs_start, needs_bench):
        swaps.append(
            f"⬇️ Bench {nb.player_name} ({nb.current_slot}) → ⬆️ Start {ns.player_name} ({ns.position})"
        )

    if len(needs_start) > len(needs_bench):
        for ns in needs_start[len(needs_bench) :]:
            swaps.append(f"⬆️ Insert {ns.player_name} ({ns.position}) into vacant starting slot")
    elif len(needs_bench) > len(needs_start):
        for nb in needs_bench[len(needs_start) :]:
            swaps.append(f"⬇️ Move {nb.player_name} ({nb.current_slot}) to Bench")

    rec.actionable_swaps = swaps

    rec_starters_map = {s.player_name.lower(): s for s in rec.recommended_starters}
    rec_bench_map = {b.player_name.lower(): b for b in rec.bench_players}

    # Build current_lineup from roster.starters
    current_lineup_items: list[CurrentRosterPlayer] = []
    for p in roster.starters:
        p_lower = p.name.lower()
        opp, ha, m_disp, g_time = get_player_matchup_info(
            p.team, odds, getattr(p, "bye_week", 0), current_week
        )
        act_pts = getattr(p, "actual_points", 0.0)
        has_played = getattr(p, "has_played", False)

        if has_played:
            # Player is locked in active lineup
            current_lineup_items.append(
                CurrentRosterPlayer(
                    player_name=p.name,
                    position=p.position,
                    team=p.team,
                    current_slot=p.slot,
                    projected_points=p.projected_points,
                    actual_points=act_pts,
                    has_played=True,
                    injury_status=p.injury_status,
                    action="KEEP_STARTING",
                    action_label=f"🏁 LOCKED ({act_pts:.1f} pts)",
                    action_detail=f"Game played/active ({act_pts:.1f} pts vs Proj {p.projected_points:.1f} pts). Locked in starting lineup.",
                    floor=act_pts,
                    ceiling=act_pts,
                    game_script_note="Game completed/in-progress",
                    opponent=opp,
                    home_away=ha,
                    matchup_display=m_disp,
                    game_time=g_time,
                )
            )
        elif p_lower in rec_starters_map:
            s_rec = rec_starters_map[p_lower]
            current_lineup_items.append(
                CurrentRosterPlayer(
                    player_name=p.name,
                    position=p.position,
                    team=p.team,
                    current_slot=p.slot,
                    projected_points=p.projected_points,
                    actual_points=act_pts,
                    has_played=False,
                    injury_status=p.injury_status,
                    action="KEEP_STARTING",
                    action_label="✅ KEEP STARTING",
                    action_detail=s_rec.reasoning,
                    floor=s_rec.floor,
                    ceiling=s_rec.ceiling,
                    game_script_note=s_rec.game_script_note,
                    opponent=opp,
                    home_away=ha,
                    matchup_display=m_disp,
                    game_time=g_time,
                )
            )
        else:
            b_rec = rec_bench_map.get(p_lower)
            reason = b_rec.reasoning if b_rec else "Out-projected by optimal starters."
            is_out = (p.injury_status or "").upper() in ("OUT", "IR", "DOUBTFUL")
            label = "🚨 BENCH THIS PLAYER"
            detail = (
                f"🚨 INACTIVE/OUT ({p.injury_status}): Remove from lineup immediately!"
                if is_out
                else f"Suboptimal ({p.projected_points:.1f} pts). {reason}"
            )
            current_lineup_items.append(
                CurrentRosterPlayer(
                    player_name=p.name,
                    position=p.position,
                    team=p.team,
                    current_slot=p.slot,
                    projected_points=p.projected_points,
                    actual_points=act_pts,
                    has_played=False,
                    injury_status=p.injury_status,
                    action="BENCH_NOW",
                    action_label=label,
                    action_detail=detail,
                    floor=round(p.projected_points * 0.7, 1),
                    ceiling=round(p.projected_points * 1.3, 1),
                    game_script_note=None,
                    opponent=opp,
                    home_away=ha,
                    matchup_display=m_disp,
                    game_time=g_time,
                )
            )

    rec.current_lineup = sort_current_lineup_by_order(current_lineup_items, league)
    rec.actual_total_points = round(
        sum(p.actual_points for p in current_lineup_items if p.has_played and p.actual_points is not None), 1
    )
    rec.projected_total_points = round(
        sum(p.projected_points for p in current_lineup_items), 1
    )

    # Build current_bench from roster.bench
    current_bench_items: list[CurrentRosterPlayer] = []
    for p in roster.bench:
        p_lower = p.name.lower()
        opp, ha, m_disp, g_time = get_player_matchup_info(
            p.team, odds, getattr(p, "bye_week", 0), current_week
        )
        act_pts = getattr(p, "actual_points", 0.0)
        has_played = getattr(p, "has_played", False)

        if has_played:
            current_bench_items.append(
                CurrentRosterPlayer(
                    player_name=p.name,
                    position=p.position,
                    team=p.team,
                    current_slot=p.slot,
                    projected_points=p.projected_points,
                    actual_points=act_pts,
                    has_played=True,
                    injury_status=p.injury_status,
                    action="STAY_ON_BENCH",
                    action_label=f"⏸️ BENCH LOCKED ({act_pts:.1f} pts)",
                    action_detail=f"Played on bench ({act_pts:.1f} pts vs Proj {p.projected_points:.1f} pts). Locked on bench for Week {current_week}.",
                    floor=act_pts,
                    ceiling=act_pts,
                    game_script_note="Game completed/in-progress",
                    opponent=opp,
                    home_away=ha,
                    matchup_display=m_disp,
                    game_time=g_time,
                )
            )
        elif p_lower in rec_starters_map:
            s_rec = rec_starters_map[p_lower]
            current_bench_items.append(
                CurrentRosterPlayer(
                    player_name=p.name,
                    position=p.position,
                    team=p.team,
                    current_slot=p.slot,
                    projected_points=p.projected_points,
                    actual_points=act_pts,
                    has_played=False,
                    injury_status=p.injury_status,
                    action="PROMOTE_TO_START",
                    action_label="⚡ START THIS PLAYER",
                    action_detail=f"Optimal starter on your bench! {s_rec.reasoning}",
                    floor=s_rec.floor,
                    ceiling=s_rec.ceiling,
                    game_script_note=s_rec.game_script_note,
                    opponent=opp,
                    home_away=ha,
                    matchup_display=m_disp,
                    game_time=g_time,
                )
            )
        else:
            b_rec = rec_bench_map.get(p_lower)
            reason = (
                b_rec.reasoning
                if b_rec
                else f"Reserve depth ({p.projected_points:.1f} pts). Lacks the requisite touch volume and scoring equity to unseat our primary starters this week."
            )
            current_bench_items.append(
                CurrentRosterPlayer(
                    player_name=p.name,
                    position=p.position,
                    team=p.team,
                    current_slot=p.slot,
                    projected_points=p.projected_points,
                    actual_points=act_pts,
                    has_played=False,
                    injury_status=p.injury_status,
                    action="STAY_ON_BENCH",
                    action_label="⏸️ KEEP ON BENCH",
                    action_detail=reason,
                    floor=round(p.projected_points * 0.7, 1),
                    ceiling=round(p.projected_points * 1.3, 1),
                    game_script_note=None,
                    opponent=opp,
                    home_away=ha,
                    matchup_display=m_disp,
                    game_time=g_time,
                )
            )


    pos_priority = {"QB": 1, "TQB": 1, "RB": 2, "WR": 3, "TE": 4, "DST": 5, "D/ST": 5, "K": 6, "PK": 6}
    rec.current_bench = sorted(
        current_bench_items,
        key=lambda p: (pos_priority.get(p.position.upper(), 99), -p.projected_points),
    )

    eastern = zoneinfo.ZoneInfo("America/New_York")
    rec.generated_at = datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z")

    return rec


def sort_current_lineup_by_order(
    starters: list[CurrentRosterPlayer], league: LeagueConfig
) -> list[CurrentRosterPlayer]:
    """Sort current ESPN starters strictly into standard fantasy roster order:
    QB, RB, RB, WR, WR, TE, FLEX, [FLEX], [K], D/ST (D/ST strictly last row).
    """
    qbs = [s for s in starters if "QB" in s.position.upper()]
    rbs = [s for s in starters if s.position.upper() == "RB"]
    wrs = [s for s in starters if s.position.upper() == "WR"]
    tes = [s for s in starters if s.position.upper() == "TE"]
    dsts = [
        s
        for s in starters
        if s.position.upper() in ("DST", "D/ST", "DEF") or "DEF" in s.position.upper()
    ]
    kickers = [s for s in starters if s.position.upper() in ("K", "PK")]

    others = [
        s
        for s in starters
        if s not in qbs
        and s not in rbs
        and s not in wrs
        and s not in tes
        and s not in dsts
        and s not in kickers
    ]

    ordered: list[CurrentRosterPlayer] = []
    ordered.extend(qbs[: league.roster.qb])
    flex_candidates = qbs[league.roster.qb :]

    ordered.extend(rbs[: league.roster.rb])
    flex_candidates.extend(rbs[league.roster.rb :])

    ordered.extend(wrs[: league.roster.wr])
    flex_candidates.extend(wrs[league.roster.wr :])

    ordered.extend(tes[: league.roster.te])
    flex_candidates.extend(tes[league.roster.te :])

    flex_candidates.extend(others)
    ordered.extend(flex_candidates[: league.roster.flex])

    if league.roster.k:
        ordered.extend(kickers[: league.roster.k])

    for s in dsts[: league.roster.dst]:
        ordered.append(s.model_copy(update={"position": "D/ST"}))

    added = {s.player_name.lower() for s in ordered}
    for s in starters:
        if s.player_name.lower() not in added:
            ordered.append(s)

    return ordered


def sort_bench_by_position(bench: list[StartSitDecision]) -> list[StartSitDecision]:
    """Sort bench players logically: QB -> RB -> WR -> TE -> D/ST -> K."""
    pos_priority = {
        "QB": 1,
        "TQB": 1,
        "RB": 2,
        "WR": 3,
        "TE": 4,
        "DST": 5,
        "D/ST": 5,
        "K": 6,
        "PK": 6,
    }
    return sorted(
        bench,
        key=lambda p: (
            pos_priority.get(p.position.upper(), 99),
            -p.projected_points,
        ),
    )


def optimize_lineup(
    league: LeagueConfig,
    week: int,
    roster: ParsedRoster,
    matchup: Optional[MatchupData] = None,
    injuries: Optional[list[PlayerInjuryInfo]] = None,
    odds: Optional[list[GameOdds]] = None,
    weather_map: Optional[dict[str, GameWeather]] = None,
    client: Optional[GeminiIntelligenceClient] = None,
    espn_league: Optional[Any] = None,
) -> LineupRecommendation:
    """Optimize starting lineup using game theory, Vegas totals, weather, and injury news.

    Args:
        league: League configuration.
        week: Current NFL week.
        roster: Parsed roster of the user.
        matchup: Current week matchup data (optional).
        injuries: List of active injury reports (optional).
        odds: List of Vegas game odds (optional).
        weather_map: Dict of team -> weather condition (optional).
        client: Gemini intelligence client (optional).
        espn_league: Connected ESPN league instance (optional).

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

    # Detect lineup holes (unplayable starters, bye weeks, vacant slots)
    detected_holes = detect_lineup_holes_and_solutions(
        roster=roster,
        league=league,
        current_week=week,
        espn_league=espn_league,
        injuries=injuries,
        odds=odds,
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
            lineup_hole_alerts=[h.model_dump() for h in detected_holes],
        )

        try:
            rec = client.generate_structured(prompt=prompt, response_schema=LineupRecommendation)

            # Code-level safety guard: zero-tolerance for OUT, IR, or DOUBTFUL starters
            out_player_names = {
                p.name.lower()
                for p in roster.players
                if (p.injury_status or "").upper() in ("OUT", "IR", "SUSPENSION", "DOUBTFUL")
            }
            if injuries:
                for inj in injuries:
                    if inj.injury_status and inj.injury_status.upper() in ("OUT", "IR", "DOUBTFUL"):
                        out_player_names.add(inj.full_name.lower())

            cleaned_starters = []
            for s in rec.recommended_starters:
                if s.player_name.lower() in out_player_names:
                    logger.warning(
                        "Enforcing injury rule: Moving injured starter %s to bench", s.player_name
                    )
                    s.action = "BENCH"
                    s.reasoning = f"🚨 INACTIVE/OUT: Must be benched. {s.reasoning}"
                    rec.bench_players.append(s)
                else:
                    cleaned_starters.append(s)
            rec.recommended_starters = sort_starters_by_lineup_order(cleaned_starters, league)
            rec.bench_players = sort_bench_by_position(rec.bench_players)
            return enrich_lineup_recommendation_with_espn_status(
                rec,
                roster,
                league,
                odds=odds,
                current_week=week,
                espn_league=espn_league,
                injuries=injuries,
            )
        except Exception as e:
            logger.warning(
                "Gemini lineup optimization call failed (%s). Falling back to deterministic optimization.",
                e,
            )

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
        pos = p.position.upper().replace("/", "")
        if pos == "DEF":
            pos = "DST"
        is_injured = (p.injury_status or "").upper() in ("OUT", "IR", "DOUBTFUL")

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
                f"Locked-in starter with a strong {p.projected_points:.1f} pt projection in full PPR. High-floor volume option with secure touch share."
                if action == "START"
                else (
                    f"🚨 MUST BENCH: Inactive/Injured ({p.injury_status}). Zero floor and severe liability; remove immediately."
                    if is_injured
                    else f"Reserve depth ({p.projected_points:.1f} projected pts). Lacks the target share or scoring equity to unseat our primary starters."
                )
            ),
            game_script_note=game_note,
        )

        if action == "START":
            recommended_starters.append(decision)
        else:
            bench_players.append(decision)

    rec = LineupRecommendation(
        league_id=league.league_id,
        week=week,
        game_theory_strategy=strategy,
        strategy_reasoning=strategy_reasoning,
        recommended_starters=sort_starters_by_lineup_order(recommended_starters, league),
        bench_players=sort_bench_by_position(bench_players),
        key_flex_decisions=[
            f"Filled {slots_filled['FLEX']}/{slots_needed['FLEX']} flex slots with highest projection upside."
        ],
    )
    return enrich_lineup_recommendation_with_espn_status(
        rec,
        roster,
        league,
        odds=odds,
        current_week=week,
        espn_league=espn_league,
        injuries=injuries,
    )

