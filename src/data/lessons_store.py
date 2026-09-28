"""
Persistent memory and lessons-learned store for NFL Fantasy Football Agent.
Persists weekly recap outcomes, bench differentials, missed opportunities,
and coaching takeaways to JSON storage (Google Cloud Storage or local filesystem).

Provides structured historical context injection for:
1. Start/Sit Lineup Optimization (avoids repeating bench mistakes)
2. Weekly Film Room Recap (tracks follow-through and accountability)
3. Seasonal trend and performance reporting
"""

from __future__ import annotations

import json
import logging
import os
import zoneinfo
from datetime import datetime
from typing import Any, Optional

from src.config import get_current_season

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MEMORY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "memory",
)


class LessonsMemoryStore:
    """Manages persistent weekly performance memory and coaching lessons."""

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        local_dir: Optional[str] = None,
    ):
        """Initialize memory store.

        Args:
            bucket_name: GCS bucket name. If provided or present in GCS_MEMORY_BUCKET env var,
                         GCS is used as primary storage.
            local_dir: Local filesystem directory fallback when GCS is unavailable or in dev/test.
        """
        env_bucket = os.environ.get("GCS_MEMORY_BUCKET", "").strip()
        self.bucket_name = bucket_name or (env_bucket if env_bucket else None)
        self.local_dir = local_dir or DEFAULT_LOCAL_MEMORY_DIR
        self._gcs_client: Any = None
        self._gcs_initialized = False

    def _get_gcs_client(self) -> Any:
        """Lazily initialize Google Cloud Storage client if bucket configured."""
        if not self._gcs_initialized:
            self._gcs_initialized = True
            if self.bucket_name:
                try:
                    from google.cloud import storage  # type: ignore

                    self._gcs_client = storage.Client()
                    logger.info("Initialized GCS client for memory store bucket: %s", self.bucket_name)
                except Exception as e:
                    logger.warning(
                        "GCS client initialization failed (%s). Falling back to local storage.", e
                    )
                    self._gcs_client = None
        return self._gcs_client

    def _blob_path(self, season: int, league_id: int) -> str:
        """Standardized storage path for a league season memory file."""
        return f"memory/{season}_{league_id}.json"

    def _local_file_path(self, season: int, league_id: int) -> str:
        """Local file path for a league season memory file."""
        return os.path.join(self.local_dir, f"{season}_{league_id}.json")

    def load_season_data(self, season: int, league_id: int) -> dict[str, Any]:
        """Load entire season memory document for a league.

        Returns:
            Dict containing season data, or default skeleton if none exists.
        """
        default_data: dict[str, Any] = {
            "season": season,
            "league_id": league_id,
            "last_updated": None,
            "weeks": {},
        }

        gcs_client = self._get_gcs_client()
        if gcs_client and self.bucket_name:
            try:
                bucket = gcs_client.bucket(self.bucket_name)
                blob = bucket.blob(self._blob_path(season, league_id))
                if blob.exists():
                    raw_content = blob.download_as_text()
                    loaded = json.loads(raw_content)
                    if isinstance(loaded, dict) and "weeks" in loaded:
                        return loaded
                else:
                    return default_data
            except Exception as e:
                logger.warning(
                    "Failed to read season memory from GCS (%s). Checking local fallback.", e
                )

        # Local filesystem fallback
        local_path = self._local_file_path(season, league_id)
        if os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict) and "weeks" in loaded:
                        return loaded
            except Exception as e:
                logger.warning("Failed to read local memory file %s: %s", local_path, e)

        return default_data

    def save_season_data(self, season: int, league_id: int, data: dict[str, Any]) -> bool:
        """Persist entire season memory document for a league."""
        eastern = zoneinfo.ZoneInfo("America/New_York")
        data["last_updated"] = datetime.now(eastern).isoformat()
        payload = json.dumps(data, indent=2, ensure_ascii=False)

        saved = False
        gcs_client = self._get_gcs_client()
        if gcs_client and self.bucket_name:
            try:
                bucket = gcs_client.bucket(self.bucket_name)
                blob = bucket.blob(self._blob_path(season, league_id))
                blob.upload_from_string(payload, content_type="application/json")
                saved = True
            except Exception as e:
                logger.warning("Failed to write season memory to GCS (%s). Writing locally.", e)

        # Always maintain local copy if GCS failed or if running locally
        try:
            local_path = self._local_file_path(season, league_id)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(payload)
            saved = True
        except Exception as e:
            logger.error("Failed to write season memory to local disk %s: %s", local_path, e)

        return saved

    def save_week_recap(self, recap: Any, season: Optional[int] = None) -> bool:
        """Extract and persist completed week recap results into memory store.

        Args:
            recap: WeeklyRecapReport instance or dict.
            season: NFL season year (defaults to current season).

        Returns:
            True if persisted successfully, False otherwise.
        """
        if season is None:
            season = get_current_season()

        # Handle both Pydantic model and dict
        if hasattr(recap, "model_dump"):
            r_dict = recap.model_dump()
        elif isinstance(recap, dict):
            r_dict = recap
        else:
            logger.warning("Unknown recap type provided to save_week_recap: %s", type(recap))
            return False

        league_id = r_dict.get("league_id")
        week = r_dict.get("week")
        if not league_id or not week:
            logger.warning("Recap missing league_id (%s) or week (%s)", league_id, week)
            return False

        eastern = zoneinfo.ZoneInfo("America/New_York")
        now_str = datetime.now(eastern).isoformat()

        # Extract structured missed opportunities
        clean_missed: list[dict[str, Any]] = []
        for mo in r_dict.get("missed_opportunities") or []:
            if isinstance(mo, dict):
                clean_missed.append({
                    "bench_player": mo.get("bench_player", ""),
                    "bench_points": float(mo.get("bench_points", 0.0)),
                    "started_player": mo.get("started_player", ""),
                    "starter_points": float(mo.get("starter_points", 0.0)),
                    "points_differential": float(mo.get("points_differential", 0.0)),
                    "lesson": mo.get("lesson", ""),
                })

        # Extract structured busts
        clean_busts: list[dict[str, Any]] = []
        for b in r_dict.get("busts") or []:
            if isinstance(b, dict):
                clean_busts.append({
                    "player_name": b.get("player_name", ""),
                    "position": b.get("position", ""),
                    "actual_points": float(b.get("actual_points", 0.0)),
                    "projected_points": float(b.get("projected_points", 0.0)),
                    "point_differential": float(b.get("point_differential", 0.0)),
                    "verdict_comment": b.get("verdict_comment", ""),
                })

        # Extract structured game balls
        clean_balls: list[dict[str, Any]] = []
        for gb in r_dict.get("game_balls") or []:
            if isinstance(gb, dict):
                clean_balls.append({
                    "player_name": gb.get("player_name", ""),
                    "position": gb.get("position", ""),
                    "actual_points": float(gb.get("actual_points", 0.0)),
                    "projected_points": float(gb.get("projected_points", 0.0)),
                    "point_differential": float(gb.get("point_differential", 0.0)),
                    "verdict_comment": gb.get("verdict_comment", ""),
                })

        week_entry: dict[str, Any] = {
            "week": int(week),
            "matchup_status": r_dict.get("matchup_status", "FINAL"),
            "result": r_dict.get("result", "FINAL"),
            "user_score": float(r_dict.get("user_score", 0.0)),
            "user_projected": float(r_dict.get("user_projected", 0.0)),
            "opponent_team_name": r_dict.get("opponent_team_name", "Opponent"),
            "opponent_score": float(r_dict.get("opponent_score", 0.0)),
            "opponent_projected": float(r_dict.get("opponent_projected", 0.0)),
            "score_margin": float(r_dict.get("score_margin", 0.0)),
            "optimal_lineup_points": float(r_dict.get("optimal_lineup_points", 0.0)),
            "points_left_on_bench": float(r_dict.get("points_left_on_bench", 0.0)),
            "coach_game_summary": r_dict.get("coach_game_summary", ""),
            "missed_opportunities": clean_missed,
            "busts": clean_busts,
            "game_balls": clean_balls,
            "lessons_learned": list(r_dict.get("lessons_learned") or []),
            "next_week_priorities": list(r_dict.get("next_week_priorities") or []),
            "saved_at": now_str,
        }

        try:
            season_data = self.load_season_data(season=season, league_id=int(league_id))
            season_data.setdefault("weeks", {})[str(week)] = week_entry
            return self.save_season_data(season=season, league_id=int(league_id), data=season_data)
        except Exception as e:
            logger.error("Failed to save week recap for league %s week %s: %s", league_id, week, e)
            return False

    def get_recent_history(
        self,
        league_id: int,
        season: Optional[int] = None,
        current_week: int = 1,
        lookback: int = 3,
    ) -> list[dict[str, Any]]:
        """Retrieve recent completed weeks of history prior to current_week.

        Args:
            league_id: ESPN league ID.
            season: NFL season year.
            current_week: Current NFL week number.
            lookback: Maximum number of previous completed weeks to return.

        Returns:
            List of weekly entries sorted chronologically (most recent first).
        """
        if season is None:
            season = get_current_season()

        season_data = self.load_season_data(season=season, league_id=league_id)
        weeks_dict = season_data.get("weeks", {})
        if not weeks_dict:
            return []

        completed_weeks: list[dict[str, Any]] = []
        for w_str, entry in weeks_dict.items():
            try:
                w_num = int(w_str)
                if w_num < current_week:
                    completed_weeks.append(entry)
            except (ValueError, TypeError):
                continue

        # Sort by week descending (most recent first)
        completed_weeks.sort(key=lambda x: x.get("week", 0), reverse=True)
        return completed_weeks[:lookback]

    def get_season_summary(self, league_id: int, season: Optional[int] = None) -> dict[str, Any]:
        """Aggregate season performance, trends, and cumulative lessons.

        Returns:
            Summary dict with record, total bench points lost, recurring missed ops, etc.
        """
        if season is None:
            season = get_current_season()

        season_data = self.load_season_data(season=season, league_id=league_id)
        weeks_dict = season_data.get("weeks", {})
        if not weeks_dict:
            return {
                "season": season,
                "league_id": league_id,
                "total_completed_weeks": 0,
                "record": "0-0",
                "avg_bench_pts_lost": 0.0,
                "total_bench_pts_lost": 0.0,
                "recurring_missed_players": [],
                "all_lessons": [],
            }

        entries = list(weeks_dict.values())
        entries.sort(key=lambda x: x.get("week", 0))

        wins = sum(1 for e in entries if e.get("result") == "WIN")
        losses = sum(1 for e in entries if e.get("result") == "LOSS")
        ties = sum(1 for e in entries if e.get("result") == "TIE")

        bench_pts_list = [float(e.get("points_left_on_bench", 0.0)) for e in entries]
        total_bench = round(sum(bench_pts_list), 1)
        avg_bench = round(total_bench / max(len(bench_pts_list), 1), 1)

        # Count repeat bench players
        bench_counts: dict[str, int] = {}
        for e in entries:
            for mo in e.get("missed_opportunities") or []:
                player = mo.get("bench_player")
                if player:
                    bench_counts[player] = bench_counts.get(player, 0) + 1

        recurring = sorted(
            [{"player": p, "times_benched_suboptimally": c} for p, c in bench_counts.items() if c >= 2],
            key=lambda x: x["times_benched_suboptimally"],
            reverse=True,
        )

        all_lessons = []
        for e in entries:
            for lesson_item in e.get("lessons_learned") or []:
                all_lessons.append({"week": e.get("week"), "lesson": lesson_item})

        return {
            "season": season,
            "league_id": league_id,
            "total_completed_weeks": len(entries),
            "record": f"{wins}-{losses}" + (f"-{ties}" if ties > 0 else ""),
            "avg_bench_pts_lost": avg_bench,
            "total_bench_pts_lost": total_bench,
            "recurring_missed_players": recurring,
            "all_lessons": all_lessons,
            "weekly_timeline": entries,
        }

    def format_lessons_for_lineup(
        self,
        league_id: int,
        season: Optional[int] = None,
        current_week: int = 1,
        lookback: int = 3,
    ) -> Optional[str]:
        """Generate structured past-tape lessons to inject into the start/sit lineup prompt.

        Directly highlights:
        - Points left on bench in recent weeks
        - Specific players left on bench who outscored starters (and point differentials)
        - Starters who busted
        - Actionable coaching mandates to prevent repeating mistakes
        """
        history = self.get_recent_history(
            league_id=league_id,
            season=season,
            current_week=current_week,
            lookback=lookback,
        )
        if not history:
            return None

        lines: list[str] = [
            "HISTORICAL TAPE & LINEUP ACCOUNTABILITY (RECENT WEEKS BENCH VS STARTER OUTCOMES):",
            "Review past decisions to prevent repeating lineup and benching mistakes:",
        ]

        total_recent_bench_pts = 0.0
        missed_players_noted: set[str] = set()

        for entry in history:
            w = entry.get("week")
            res = entry.get("result", "FINAL")
            pts_bench = entry.get("points_left_on_bench", 0.0)
            total_recent_bench_pts += pts_bench
            opp_name = entry.get("opponent_team_name", "Opponent")
            u_score = entry.get("user_score", 0.0)
            o_score = entry.get("opponent_score", 0.0)

            lines.append(
                f"- Week {w} ({res} vs {opp_name}, {u_score:.1f}-{o_score:.1f} pts): "
                f"{pts_bench:.1f} pts left on bench."
            )

            missed = entry.get("missed_opportunities") or []
            for mo in missed:
                bp = mo.get("bench_player", "Bench Player")
                sp = mo.get("started_player", "Starter")
                diff = mo.get("points_differential", 0.0)
                lesson = mo.get("lesson", "")
                missed_players_noted.add(bp)
                lines.append(
                    f"  * COSTLY BENCH DECISION: Bench {bp} ({mo.get('bench_points', 0):.1f} pts) "
                    f"outscored starter {sp} ({mo.get('starter_points', 0):.1f} pts) by +{diff:.1f} pts."
                )
                if lesson:
                    lines.append(f"    Takeaway: {lesson}")

            busts = entry.get("busts") or []
            for b in busts:
                p_name = b.get("player_name", "")
                diff = b.get("point_differential", 0.0)
                lines.append(
                    f"  * STARTER BUST: {p_name} fell {abs(diff):.1f} pts below projection."
                )

        lines.extend([
            "",
            "COACHING MANDATE FOR WEEK " f"{current_week} START/SIT DECISIONS:",
            "- Scrutinize players on our bench who showed high target/snap share or out-produced starters in recent games.",
            "- In tight FLEX toss-ups, heavily penalize players whose archetypes yielded starter busts in prior weeks.",
            "- Do NOT leave proven high-ceiling volume on the bench if the starter has declining opportunity.",
        ])

        return "\n".join(lines)

    def format_lessons_for_recap(
        self,
        league_id: int,
        season: Optional[int] = None,
        current_week: int = 1,
        lookback: int = 2,
    ) -> Optional[str]:
        """Generate previous week's takeaways and priorities to inject into the Film Room recap prompt.

        Enables the AI coach to assess follow-through and seasonal progress.
        """
        history = self.get_recent_history(
            league_id=league_id,
            season=season,
            current_week=current_week,
            lookback=lookback,
        )
        if not history:
            return None

        lines: list[str] = [
            "PRIOR FILM ROOM LESSONS & ACTION PLAN ACCOUNTABILITY:",
            "Evaluate whether our squad addressed past lessons or repeated prior-week mistakes:",
        ]

        for entry in history:
            w = entry.get("week")
            lessons = entry.get("lessons_learned") or []
            priorities = entry.get("next_week_priorities") or []

            lines.append(f"- From Week {w} Tape & Film Room:")
            if lessons:
                lines.append("  * Key Lessons Established:")
                for lesson_text in lessons[:3]:
                    lines.append(f"    - \"{lesson_text}\"")
            if priorities:
                lines.append("  * Action Priorities Set:")
                for p in priorities[:3]:
                    lines.append(f"    - \"{p}\"")

        lines.extend([
            "",
            "ACCOUNTABILITY MANDATE FOR COACH SUMMARY:",
            "- Explicitly comment on whether the team followed through on prior priorities or corrected past vulnerabilities.",
        ])

        return "\n".join(lines)

    def format_lessons_for_trades(
        self,
        league_id: int,
        season: Optional[int] = None,
        current_week: int = 1,
        lookback: int = 4,
    ) -> Optional[str]:
        """Generate structured past-tape context to inject into trade evaluation and proposal prompts.

        Directly highlights:
        - Season record and trajectory (winning/losing/rebuilding)
        - Recurring bench mistakes indicating undervalued roster assets
        - Positional weaknesses exposed by recent losses
        - Past lessons that should inform trade strategy (buy-low/sell-high signals)
        """
        history = self.get_recent_history(
            league_id=league_id,
            season=season,
            current_week=current_week,
            lookback=lookback,
        )
        if not history:
            return None

        summary = self.get_season_summary(league_id=league_id, season=season)

        lines: list[str] = [
            "HISTORICAL TAPE & TRADE INTELLIGENCE (LESSONS FROM PRIOR WEEKS):",
            "Use this data to inform trade strategy — who to target, who to sell, and roster construction priorities:",
        ]

        # Season record context
        record = summary.get("record", "0-0")
        total_weeks = summary.get("total_completed_weeks", 0)
        avg_bench = summary.get("avg_bench_pts_lost", 0.0)
        lines.append(f"- Season Record: {record} through {total_weeks} completed weeks.")
        if avg_bench > 8.0:
            lines.append(
                f"  ⚠️ CHRONIC BENCH INEFFICIENCY: Averaging {avg_bench:.1f} pts/wk left on bench. "
                "This signals we may have undervalued starters rotting on our bench — consider "
                "trading bench depth for starting upgrades rather than hoarding talent."
            )

        # Recurring bench mistakes → trade signals
        recurring = summary.get("recurring_missed_players", [])
        if recurring:
            lines.append("- RECURRING BENCH OUTPERFORMERS (Trade Signal — These players keep proving value on our bench):")
            for entry in recurring[:3]:
                player = entry.get("player", "Unknown")
                count = entry.get("times_benched_suboptimally", 0)
                lines.append(
                    f"  * {player}: Outscored our starter {count}x this season while benched. "
                    "If we can't start them, their trade value is high — leverage this."
                )

        # Recent week-by-week context for trade timing
        for entry in history[:3]:
            w = entry.get("week")
            res = entry.get("result", "FINAL")
            u_score = entry.get("user_score", 0.0)
            o_score = entry.get("opponent_score", 0.0)
            pts_bench = entry.get("points_left_on_bench", 0.0)

            lines.append(
                f"- Week {w} ({res}, {u_score:.1f}-{o_score:.1f}): "
                f"{pts_bench:.1f} pts left on bench."
            )

            # Extract positional weaknesses from busts
            busts = entry.get("busts") or []
            for b in busts:
                pos = b.get("position", "")
                p_name = b.get("player_name", "")
                diff = b.get("point_differential", 0.0)
                if abs(diff) >= 5.0:
                    lines.append(
                        f"  * POSITIONAL WEAKNESS: {p_name} ({pos}) busted by {abs(diff):.1f} pts below projection. "
                        "Consider trading for an upgrade at this position."
                    )

            # Extract lessons relevant to trades
            lessons = entry.get("lessons_learned") or []
            for lesson_text in lessons[:2]:
                lt = lesson_text.lower()
                if any(kw in lt for kw in ["trade", "roster", "depth", "upgrade", "position", "bench", "waiver"]):
                    lines.append(f"  * Prior Coaching Lesson: \"{lesson_text}\"")

        lines.extend([
            "",
            "TRADE STRATEGY MANDATE INFORMED BY HISTORICAL TAPE:",
            "- If our season record is losing, prioritize win-now trades that upgrade starters immediately.",
            "- If we have recurring bench outperformers we cannot start, package them for starting-caliber upgrades.",
            "- Target positions where we have experienced repeated starter busts or underperformance.",
            "- Do NOT trade away assets at positions where we have historically been thin and vulnerable.",
        ])

        return "\n".join(lines)


# Singleton factory for clean access across modules
_STORE_INSTANCE: Optional[LessonsMemoryStore] = None


def get_lessons_store() -> LessonsMemoryStore:
    """Return the global LessonsMemoryStore instance."""
    global _STORE_INSTANCE
    if _STORE_INSTANCE is None:
        _STORE_INSTANCE = LessonsMemoryStore()
    return _STORE_INSTANCE
