"""
Weekly Post-Game Recap and Film Room Analysis Engine.
Reviews the completed week: matchup outcome, points left on bench,
MVP performances, busts, and veteran coaching lessons learned.
"""

import logging
import zoneinfo
from datetime import datetime
from typing import Optional

from src.config import LeagueConfig
from src.espn.matchup import MatchupData
from src.espn.roster import RosterPlayer
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_weekly_recap_prompt
from src.intelligence.schemas import (
    MissedOpportunity,
    RecapPlayerPerformance,
    WeeklyRecapReport,
)

logger = logging.getLogger(__name__)


def _compute_optimal_points(
    players: list[RosterPlayer], league: LeagueConfig, is_final: bool = True
) -> float:
    """Calculate maximum fantasy points possible with optimal starting lineup.

    If matchup is in progress, uses actual points for finished players and projected
    points for unplayed players to estimate lineup ceiling.
    """
    def get_pts(p: RosterPlayer) -> float:
        return p.actual_points if is_final else (p.actual_points if p.has_played else p.projected_points)

    qbs = sorted([p for p in players if p.position.upper() == "QB"], key=get_pts, reverse=True)
    rbs = sorted([p for p in players if p.position.upper() == "RB"], key=get_pts, reverse=True)
    wrs = sorted([p for p in players if p.position.upper() == "WR"], key=get_pts, reverse=True)
    tes = sorted([p for p in players if p.position.upper() == "TE"], key=get_pts, reverse=True)
    dsts = sorted([p for p in players if p.position.upper() in ("D/ST", "DST")], key=get_pts, reverse=True)
    ks = sorted([p for p in players if p.position.upper() == "K"], key=get_pts, reverse=True)

    optimal: list[RosterPlayer] = []
    used_names: set[str] = set()

    # QB
    opt_qbs = qbs[: league.roster.qb]
    optimal.extend(opt_qbs)
    used_names.update(p.name.lower() for p in opt_qbs)

    # RBs
    opt_rbs = rbs[: league.roster.rb]
    optimal.extend(opt_rbs)
    used_names.update(p.name.lower() for p in opt_rbs)

    # WRs
    opt_wrs = wrs[: league.roster.wr]
    optimal.extend(opt_wrs)
    used_names.update(p.name.lower() for p in opt_wrs)

    # TEs
    opt_tes = tes[: league.roster.te]
    optimal.extend(opt_tes)
    used_names.update(p.name.lower() for p in opt_tes)

    # FLEX (RB, WR, TE not already used)
    remaining_flex = [
        p for p in (rbs + wrs + tes)
        if p.name.lower() not in used_names
    ]
    remaining_flex.sort(key=get_pts, reverse=True)
    opt_flex = remaining_flex[: league.roster.flex]
    optimal.extend(opt_flex)
    used_names.update(p.name.lower() for p in opt_flex)

    # D/ST
    optimal.extend(dsts[: league.roster.dst])

    # K
    if league.roster.k:
        optimal.extend(ks[: league.roster.k])

    return round(sum(get_pts(p) for p in optimal), 1)


def _find_missed_opportunities(starters: list[RosterPlayer], bench: list[RosterPlayer]) -> list[MissedOpportunity]:
    """Find bench players who substantially outscored starters at eligible slots.

    Only compares players who have BOTH already played to avoid calling unplayed starters missed decisions.
    """
    misses: list[MissedOpportunity] = []

    # Check each bench player who HAS PLAYED against eligible starters who HAVE ALSO PLAYED
    bench_sorted = sorted([b for b in bench if b.has_played], key=lambda p: p.actual_points, reverse=True)
    for b in bench_sorted:
        if b.actual_points <= 5.0:
            continue
        # Find starters who scored less AND HAVE PLAYED
        eligible_starters = [
            s for s in starters
            if s.has_played
            and (s.position.upper() == b.position.upper() or s.slot in ("FLEX", "RB/WR/TE", "WR/TE"))
            and s.actual_points < b.actual_points
        ]
        eligible_starters.sort(key=lambda s: s.actual_points)
        if eligible_starters:
            worst_starter = eligible_starters[0]
            diff = round(b.actual_points - worst_starter.actual_points, 1)
            if diff >= 3.0:
                lesson = (
                    f"Trust tape and volume: {b.name} ({b.actual_points:.1f} pts) had higher touch/target "
                    f"efficiency than {worst_starter.name} ({worst_starter.actual_points:.1f} pts). "
                    "In upcoming weeks, elevate players with escalating weekly target/snap share."
                )
                misses.append(
                    MissedOpportunity(
                        bench_player=b.name,
                        bench_points=b.actual_points,
                        started_player=worst_starter.name,
                        starter_points=worst_starter.actual_points,
                        points_differential=diff,
                        lesson=lesson,
                    )
                )
    return misses[:3]


def generate_weekly_recap(
    league: LeagueConfig,
    week: int,
    matchup: MatchupData,
    client: Optional[GeminiIntelligenceClient] = None,
) -> WeeklyRecapReport:
    """Generate a comprehensive weekly film room report or mid-week checkpoint.

    Args:
        league: League configuration.
        week: NFL week number.
        matchup: Weekly matchup data with user and opponent rosters/scores.
        client: Gemini intelligence client (optional).

    Returns:
        WeeklyRecapReport with state-aware analysis.
    """
    eastern = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(eastern)
    timestamp_str = now_et.strftime("%A, %B %-d, %Y at %-I:%M %p %Z")

    user_roster = matchup.your_team
    opp_roster = matchup.opponent_team
    user_team_name = user_roster.team_name or "Mad Dawg"
    opp_team_name = opp_roster.team_name if opp_roster else "Opponent"

    # Identify played vs upcoming starters
    completed_starters = [p for p in user_roster.starters if p.has_played]
    upcoming_starters = [p for p in user_roster.starters if not p.has_played]
    completed_count = len(completed_starters)
    total_count = len(user_roster.starters)

    # Determine scores
    starter_user_actual = round(sum(p.actual_points for p in user_roster.starters), 1)
    user_score = matchup.your_score if matchup.your_score > 0 else starter_user_actual
    user_projected = round(matchup.your_projected, 1)

    starter_opp_actual = round(sum(p.actual_points for p in opp_roster.starters), 1) if opp_roster else 0.0
    opp_score = matchup.opp_score if matchup.opp_score > 0 else starter_opp_actual
    opp_projected = round(matchup.opp_projected, 1)

    score_margin = round(user_score - opp_score, 1)

    # Status & Result classification
    all_user_played = (completed_count == total_count and total_count > 0)
    all_opp_played = all(p.has_played for p in opp_roster.starters) if (opp_roster and opp_roster.starters) else True
    is_final = all_user_played and all_opp_played

    if completed_count == 0:
        matchup_status = "PRE_KICKOFF"
        result = "PRE_KICKOFF"
    elif not is_final:
        matchup_status = "IN_PROGRESS"
        result = "IN_PROGRESS"
    else:
        matchup_status = "FINAL"
        if score_margin > 0:
            result = "WIN"
        elif score_margin < 0:
            result = "LOSS"
        else:
            result = "TIE"

    # Compute optimal lineup and bench delta
    optimal_pts = _compute_optimal_points(user_roster.players, league, is_final=is_final)
    if is_final:
        pts_left_on_bench = max(0.0, round(optimal_pts - user_score, 1))
    else:
        user_effective_proj = round(
            starter_user_actual + sum(p.projected_points for p in upcoming_starters), 1
        )
        pts_left_on_bench = max(0.0, round(optimal_pts - user_effective_proj, 1))

    # Identify misses (strictly completed games)
    missed_ops = _find_missed_opportunities(user_roster.starters, user_roster.bench)

    # Starters & Bench performance dicts
    completed_perf = [
        {
            "name": p.name,
            "pos": p.position,
            "slot": p.slot,
            "actual": p.actual_points,
            "proj": p.projected_points,
            "diff": round(p.actual_points - p.projected_points, 1),
            "played": True,
        }
        for p in completed_starters
    ]
    upcoming_perf = [
        {
            "name": p.name,
            "pos": p.position,
            "slot": p.slot,
            "actual": 0.0,
            "proj": p.projected_points,
            "diff": 0.0,
            "played": False,
        }
        for p in upcoming_starters
    ]
    starters_perf = completed_perf + upcoming_perf

    bench_perf = [
        {
            "name": p.name,
            "pos": p.position,
            "actual": p.actual_points,
            "proj": p.projected_points,
            "diff": round(p.actual_points - p.projected_points, 1),
            "played": p.has_played,
        }
        for p in user_roster.bench
    ]

    # Deterministic game balls and busts (STRICTLY completed starters)
    top_performers = sorted(
        [p for p in completed_starters if p.actual_points > 0],
        key=lambda p: p.actual_points,
        reverse=True,
    )
    game_balls = [
        RecapPlayerPerformance(
            player_name=p.name,
            position=p.position,
            team=p.team,
            slot=p.slot,
            actual_points=p.actual_points,
            projected_points=p.projected_points,
            point_differential=round(p.actual_points - p.projected_points, 1),
            verdict_comment=f"Offensive engine: Delivered {p.actual_points:.1f} pts (projection {p.projected_points:.1f}) in a high-leverage starter role.",
        )
        for p in top_performers[:2]
    ]

    underperformers = sorted(
        [p for p in completed_starters if p.actual_points < (p.projected_points - 2.5)],
        key=lambda p: (p.actual_points - p.projected_points),
    )
    busts = [
        RecapPlayerPerformance(
            player_name=p.name,
            position=p.position,
            team=p.team,
            slot=p.slot,
            actual_points=p.actual_points,
            projected_points=p.projected_points,
            point_differential=round(p.actual_points - p.projected_points, 1),
            verdict_comment=f"Fell flat: Scored only {p.actual_points:.1f} pts vs {p.projected_points:.1f} expected. Stalled offensive drives restricted ceiling.",
        )
        for p in underperformers[:2]
    ]

    # Structured upcoming starters
    upcoming_structured = [
        RecapPlayerPerformance(
            player_name=p.name,
            position=p.position,
            team=p.team,
            slot=p.slot,
            actual_points=0.0,
            projected_points=p.projected_points,
            point_differential=0.0,
            verdict_comment=f"Awaiting kickoff: Projected for {p.projected_points:.1f} pts. High-priority starter in our upcoming Sunday/Monday game script.",
        )
        for p in upcoming_starters
    ]

    fallback_err: Optional[str] = None
    # AI Reasoning with Gemini Pro
    if client is not None:
        try:
            prompt = format_weekly_recap_prompt(
                league=league,
                week=week,
                user_team_name=user_team_name,
                user_score=user_score,
                user_projected=user_projected,
                opponent_team_name=opp_team_name,
                opponent_score=opp_score,
                opponent_projected=opp_projected,
                starters_performance=starters_perf,
                bench_performance=bench_perf,
                optimal_lineup_points=optimal_pts,
                points_left_on_bench=pts_left_on_bench,
                matchup_status=matchup_status,
                completed_starters=completed_perf,
                upcoming_starters=upcoming_perf,
            )
            report = client.generate_structured(prompt=prompt, response_schema=WeeklyRecapReport)
            report.league_id = league.league_id
            report.league_name = league.name
            report.week = week
            report.user_team_name = user_team_name
            report.opponent_team_name = opp_team_name
            report.user_score = user_score
            report.opponent_score = opp_score
            report.score_margin = score_margin
            report.matchup_status = matchup_status
            report.result = result
            report.completed_starters_count = completed_count
            report.total_starters_count = total_count
            report.optimal_lineup_points = optimal_pts
            report.points_left_on_bench = pts_left_on_bench
            report.generated_at = timestamp_str
            report.intelligence_backend = client.model
            report.fallback_reason = None

            # Safety guard: ensure NO unplayed players ever enter busts or missed_opportunities
            if matchup_status == "IN_PROGRESS":
                completed_names = {p.name.lower() for p in completed_starters}
                report.busts = [b for b in report.busts if b.player_name.lower() in completed_names]
                if not report.upcoming_starters and upcoming_structured:
                    report.upcoming_starters = upcoming_structured
            elif matchup_status == "PRE_KICKOFF":
                report.busts = []
                report.game_balls = []
                report.missed_opportunities = []
                if not report.upcoming_starters and upcoming_structured:
                    report.upcoming_starters = upcoming_structured

            return report
        except Exception as e:
            fallback_err = str(e)
            logger.warning("Gemini failed to generate weekly recap, using deterministic fallback: %s", e)

    # Deterministic fallback based on gamestate
    if matchup_status == "IN_PROGRESS":
        coach_summary = (
            f"Week {week} Mid-Week Checkpoint: {completed_count} of {total_count} starters have completed their games, "
            f"putting our live score at {user_score:.1f} pts. With {len(upcoming_starters)} starters yet to kick off, "
            f"our projected total sits at {user_projected:.1f} pts against {opp_team_name} ({opp_projected:.1f} pts). "
            "We are holding our ground and looking for our remaining core to execute in upcoming Sunday/Monday game scripts."
        )
        lessons = [
            "Early slate momentum: Stay disciplined through Thursday night variance; high-volume starters are still ahead.",
            "Surveillance alert: Monitor Sunday pre-game inactive lists 90 minutes before kickoff for last-minute lineup adjustments.",
        ]
        priorities = [
            "Track snap counts and target shares across early Sunday games to identify emerging waiver breakout targets.",
            "Prepare waiver priority shortlist for Tuesday morning processing based on injury developments.",
        ]
    elif matchup_status == "PRE_KICKOFF":
        coach_summary = (
            f"Week {week} Matchup Outlook: Games have not kicked off yet. {user_team_name} is projected for {user_projected:.1f} pts "
            f"against {opp_team_name} ({opp_projected:.1f} pts). Starters are locked. Full Film Room drops Tuesday morning post-MNF."
        )
        lessons = [
            "Pre-game checks: Confirm active status and favorable weather conditions prior to each kickoff window.",
            "Roster readiness: Ensure bench depth is preserved rather than burned on speculative drops.",
        ]
        priorities = [
            "Lock in optimal starters ahead of initial kickoff.",
            "Scout trending waiver wire adds ahead of Tuesday's waiver cycle.",
        ]
    else:  # FINAL
        summary_outcome = (
            f"A hard-fought {score_margin:+.1f}-point victory against {opp_team_name}."
            if score_margin > 0
            else f"A tough {score_margin:.1f}-point setback against {opp_team_name}."
            if score_margin < 0
            else f"A rare {user_score:.1f}-{opp_score:.1f} tie against {opp_team_name}."
        )
        coach_summary = (
            f"Week {week} Film Room: {summary_outcome} The squad posted {user_score:.1f} points "
            f"against a projected {user_projected:.1f}. Optimal lineup simulations reveal {pts_left_on_bench:.1f} "
            f"points remained on the bench. We're breaking down the tape to refine our starter volume thresholds "
            f"and target high-leverage waiver upgrades ahead of Week {week + 1}."
        )
        lessons = [
            "Game script dictates ceiling: Verify Vegas team totals and game spread to identify shootout environments.",
            f"Bench depth efficiency: With {pts_left_on_bench:.1f} pts left unplayed, monitor red-zone usage share when deciding flex toss-ups.",
            "Stay aggressive on high-upside waiver volume: Target emerging backfield handcuffs and target-monopoly wide receivers on Tuesday.",
        ]
        priorities = [
            "Audit waiver wire for top 24-hour trending targets to address shallow positional depth.",
            f"Review Week {week + 1} defensive matchup pairings for bench receivers trending toward flex relevance.",
            "Identify opposing league rosters suffering critical injuries to explore win-win trade proposals.",
        ]

    return WeeklyRecapReport(
        league_id=league.league_id,
        league_name=league.name,
        week=week,
        matchup_status=matchup_status,
        result=result,
        completed_starters_count=completed_count,
        total_starters_count=total_count,
        user_team_name=user_team_name,
        user_score=user_score,
        user_projected=user_projected,
        opponent_team_name=opp_team_name,
        opponent_score=opp_score,
        opponent_projected=opp_projected,
        score_margin=score_margin,
        optimal_lineup_points=optimal_pts,
        points_left_on_bench=pts_left_on_bench,
        coach_game_summary=coach_summary,
        game_balls=game_balls,
        missed_opportunities=missed_ops,
        busts=busts,
        upcoming_starters=upcoming_structured if matchup_status != "FINAL" else [],
        lessons_learned=lessons,
        next_week_priorities=priorities,
        generated_at=timestamp_str,
        intelligence_backend="deterministic_fallback",
        fallback_reason=fallback_err or "Gemini client was not provided",
    )

