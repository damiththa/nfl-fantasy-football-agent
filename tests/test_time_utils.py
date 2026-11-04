"""Unit tests for the Time & Timezone Services (time_utils.py) and dynamic alert integration.
Verifies timezone awareness, Daylight Saving Time (EDT <-> EST) transitions,
kickoff locks, and dynamic countdown alert headers.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.data.time_utils import (
    EASTERN_TZ,
    format_et,
    format_iso,
    get_imminent_slate_info,
    is_kickoff_passed,
    minutes_until,
    now_et,
    now_utc,
    to_et,
    to_utc,
)
from src.notifications.email import _build_email_html, _subject_for_job


class TestTimezoneConversions:
    """Tests for core UTC and Eastern timezone parsing and normalization."""

    def test_now_functions_are_timezone_aware(self):
        utc = now_utc()
        et = now_et()
        assert utc.tzinfo is not None
        assert utc.tzinfo == timezone.utc
        assert et.tzinfo is not None
        assert et.tzinfo == EASTERN_TZ

    def test_to_utc_epoch_timestamps(self):
        # 1762016400 is Sunday Nov 1, 2026 17:00:00 UTC
        sec_ts = 1762016400
        ms_ts = 1762016400000

        dt_sec = to_utc(sec_ts)
        dt_ms = to_utc(ms_ts)

        assert dt_sec is not None
        assert dt_ms is not None
        assert dt_sec == dt_ms
        assert dt_sec.tzinfo == timezone.utc

    def test_to_utc_iso_strings(self):
        # ISO with Z
        dt1 = to_utc("2026-11-01T18:00:00Z")
        assert dt1 is not None
        assert dt1.tzinfo == timezone.utc
        assert dt1.hour == 18

        # ISO with explicit offset
        dt2 = to_utc("2026-11-01T13:00:00-05:00")
        assert dt2 is not None
        assert dt2.hour == 18  # 13:00 -05:00 is 18:00 UTC

        # Naive ISO string safely defaults to UTC
        dt3 = to_utc("2026-11-01T18:00:00")
        assert dt3 is not None
        assert dt3.tzinfo == timezone.utc
        assert dt3.hour == 18

    def test_to_utc_handles_none_and_invalid(self):
        assert to_utc(None) is None
        assert to_utc("") is None
        assert to_utc("invalid-date-string") is None

    def test_dst_transition_edt_vs_est(self):
        """Verify that Daylight Saving Time (Fall Back on Nov 1, 2026) correctly
        maps UTC to EDT (-4h) in October and EST (-5h) in November.
        """
        # October 25, 2026 (Before DST change — Daylight Saving Time EDT UTC-4)
        oct_utc = datetime(2026, 10, 25, 17, 0, 0, tzinfo=timezone.utc)
        oct_et = to_et(oct_utc)
        assert oct_et is not None
        assert oct_et.hour == 13  # 1:00 PM
        assert oct_et.tzname() == "EDT"
        assert format_et(oct_utc, "%a %-I:%M %p %Z") == "Sun 1:00 PM EDT"

        # November 1, 2026 (After DST change — Standard Time EST UTC-5)
        # Note: 1:00 PM EST is 18:00 UTC
        nov_utc = datetime(2026, 11, 1, 18, 0, 0, tzinfo=timezone.utc)
        nov_et = to_et(nov_utc)
        assert nov_et is not None
        assert nov_et.hour == 13  # 1:00 PM
        assert nov_et.tzname() == "EST"
        assert format_et(nov_utc, "%a %-I:%M %p %Z") == "Sun 1:00 PM EST"

    def test_format_iso_has_explicit_timezone(self):
        iso_str = format_iso()
        assert "+" in iso_str or "-" in iso_str
        parsed = datetime.fromisoformat(iso_str)
        assert parsed.tzinfo is not None


class TestKickoffCountdownAndLocks:
    """Tests for relative kickoff calculations and kickoff lock gates."""

    def test_minutes_until(self):
        base_now = datetime(2026, 11, 1, 16, 45, 0, tzinfo=timezone.utc)  # 11:45 AM EST
        kickoff = datetime(2026, 11, 1, 18, 0, 0, tzinfo=timezone.utc)    # 1:00 PM EST

        mins = minutes_until(kickoff, from_dt=base_now)
        assert mins == 75.0

    def test_is_kickoff_passed(self):
        base_now = datetime(2026, 11, 1, 18, 0, 0, tzinfo=timezone.utc)

        past_game = datetime(2026, 11, 1, 17, 59, 0, tzinfo=timezone.utc)
        future_game = datetime(2026, 11, 1, 18, 5, 0, tzinfo=timezone.utc)

        assert is_kickoff_passed(past_game, from_dt=base_now) is True
        assert is_kickoff_passed(future_game, from_dt=base_now) is False

        # With 10-minute pre-kickoff buffer
        assert is_kickoff_passed(future_game, buffer_minutes=10.0, from_dt=base_now) is True


class TestImminentSlateCategorization:
    """Tests for get_imminent_slate_info grouping and countdown banners."""

    def _make_game(self, away: str, home: str, game_time_utc: datetime, status: str = "pre"):
        game = MagicMock()
        game.away_team = away
        game.home_team = home
        game.game_time = game_time_utc
        game.status = status
        return game

    def test_categorize_slates_accurately(self):
        # Evaluation time: Sunday Nov 1, 2026 at 11:45 AM EST (16:45 UTC)
        base_now = datetime(2026, 11, 1, 16, 45, 0, tzinfo=timezone.utc)

        # 1. Past Thursday game (Locked)
        tnf = self._make_game("BUF", "MIA", datetime(2026, 10, 29, 20, 15, 0, tzinfo=timezone.utc), "final")

        # 2. Imminent 1:00 PM EST early slate (75 mins away, in [0, 120] window)
        early_1 = self._make_game("KC", "DEN", datetime(2026, 11, 1, 18, 0, 0, tzinfo=timezone.utc))
        early_2 = self._make_game("GB", "CHI", datetime(2026, 11, 1, 18, 0, 0, tzinfo=timezone.utc))

        # 3. Later 4:05 PM EST afternoon slate (260 mins away)
        late = self._make_game("SF", "LAR", datetime(2026, 11, 1, 21, 5, 0, tzinfo=timezone.utc))

        odds = [tnf, early_1, early_2, late]
        slate_info = get_imminent_slate_info(odds, window_minutes=120.0, from_dt=base_now)

        assert slate_info["has_imminent_slate"] is True
        assert len(slate_info["imminent_games"]) == 2
        assert len(slate_info["locked_games"]) == 1
        assert "BUF" in slate_info["locked_teams"]
        assert "MIA" in slate_info["locked_teams"]
        assert "KC" in slate_info["imminent_teams"]
        assert "DEN" in slate_info["imminent_teams"]
        assert slate_info["minutes_to_next_kickoff"] == 75
        assert "1:00 PM EST" in slate_info["slate_label"]
        assert "in 75 mins" in slate_info["slate_label"]


class TestDynamicAlertNotifications:
    """Tests for dynamic subject line and email countdown banner generation."""

    def test_dynamic_sunday_pregame_subject_with_slate_info(self):
        results = {
            "pna": {
                "imminent_slate": {
                    "slate_title": "1:00 PM EST Slate",
                    "minutes_to_next_kickoff": 75,
                },
                "slate_label": "1:00 PM EST Kickoff (in 75 mins)",
            }
        }
        subject = _subject_for_job("sunday_pregame", results=results)
        assert subject == "🚨 GAME DAY ALERT — 1:00 PM EST Slate Inactive Check (75m to kickoff)"

    def test_email_html_includes_imminent_slate_banner(self):
        results = {
            "pna": {
                "slate_label": "1:00 PM EST Kickoff (in 75 mins — 8 games)",
                "recommended_starters": [],
                "bench_players": [],
            }
        }
        html = _build_email_html(
            subject="Test Subject",
            job_type="sunday_pregame",
            results=results,
            timestamp="Sunday, November 1, 2026 at 11:45 AM EST",
        )
        assert "IMMINENT SLATE COUNTDOWN:" in html
        assert "1:00 PM EST Kickoff (in 75 mins — 8 games)" in html

    def test_enriched_lineup_imminent_slate_is_json_serializable(self):
        import json

        from src.analysis.lineup import enrich_lineup_recommendation_with_espn_status
        from src.config import PNA_2026
        from src.data.vegas import GameOdds
        from src.espn.roster import ParsedRoster, RosterPlayer
        from src.intelligence.schemas import LineupRecommendation, StartSitDecision

        odds = [
            GameOdds(
                game_id="g1",
                home_team="KC",
                away_team="BUF",
                spread=-2.5,
                over_under=49.5,
                home_implied_total=26.0,
                away_implied_total=23.5,
                game_time=datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc),
                status="pre",
            )
        ]
        roster = ParsedRoster(
            team_name="Mad Dawg",
            players=[
                RosterPlayer("Patrick Mahomes", "QB", "KC", "QB", 22.0, 0.0, "ACTIVE", 10, 99.0)
            ],
        )
        rec = LineupRecommendation(
            league_id=PNA_2026.league_id,
            week=10,
            game_theory_strategy="BALANCED",
            strategy_reasoning="Test",
            recommended_starters=[
                StartSitDecision(
                    player_name="Patrick Mahomes",
                    position="QB",
                    team="KC",
                    action="START",
                    confidence=0.95,
                    floor=18.0,
                    ceiling=30.0,
                    projected_points=22.0,
                    reasoning="Elite QB1",
                )
            ],
            bench_players=[],
            key_flex_decisions=[],
        )

        enriched = enrich_lineup_recommendation_with_espn_status(
            rec, roster, PNA_2026, odds=odds, current_week=10
        )
        dumped = enriched.model_dump()
        serialized = json.dumps(dumped)
        parsed = json.loads(serialized)
        assert isinstance(parsed["imminent_slate"]["locked_teams"], list)
        assert isinstance(parsed["imminent_slate"]["imminent_teams"], list)
        assert "error" not in parsed

