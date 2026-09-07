"""
Start/Sit Lineup Optimizer.
Applies game theory (floor vs ceiling) depending on matchup point margins,
cross-referencing injuries, Vegas totals, and weather conditions.
"""

import logging
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


def enrich_lineup_recommendation_with_espn_status(
    rec: LineupRecommendation, roster: ParsedRoster, league: LeagueConfig
) -> LineupRecommendation:
    """Populate current ESPN slots, detect vacant starting slots, flag suboptimal starters,
    and generate actionable swap instructions.
    """
    player_current_slots = {p.name.lower(): p.slot for p in roster.players}

    # Update recommended starters
    for s in rec.recommended_starters:
        s.current_slot = player_current_slots.get(s.player_name.lower(), "Bench")
        if s.current_slot in ("Bench", "BE"):
            s.alignment = "SWAP_TO_START"
        else:
            s.alignment = "ALIGNED"

    # Update bench players
    for b in rec.bench_players:
        b.current_slot = player_current_slots.get(b.player_name.lower(), "Bench")
        if b.current_slot not in ("Bench", "BE", "IR"):
            b.alignment = "MOVE_TO_BENCH"
        else:
            b.alignment = "ALIGNED"

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

    # Generate explicit actionable swap instructions
    swaps = []
    needs_bench = [b for b in rec.bench_players if b.alignment == "MOVE_TO_BENCH"]
    needs_start = [s for s in rec.recommended_starters if s.alignment == "SWAP_TO_START"]

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
    return rec


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
            return enrich_lineup_recommendation_with_espn_status(rec, roster, league)
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
                f"Ranked for starting role based on {p.projected_points:.1f} projected PPR points."
                if action == "START"
                else (
                    f"Inactive/Injured ({p.injury_status}). Must remain on bench."
                    if is_injured
                    else f"Bench depth ({p.projected_points:.1f} projected pts). Behind starting {p.position}s on depth chart this week."
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
    return enrich_lineup_recommendation_with_espn_status(rec, roster, league)

