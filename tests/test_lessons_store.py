"""
Unit and integration tests for LessonsMemoryStore and historical context injection.
Tests cover:
1. Local JSON persistence (saving and loading weekly recaps)
2. GCS storage and graceful local fallback on GCS error
3. Formatting structured memory for lineup optimization prompts
4. Formatting structured memory for weekly recap prompts
5. Season summary calculation (record, avg bench pts lost, repeat benched players)
6. Integration with optimize_lineup and generate_weekly_recap
7. FastAPI query endpoints (/query/lessons-summary and /query/lessons)
"""

import json
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.analysis.lineup import optimize_lineup
from src.analysis.recap import generate_weekly_recap
from src.config import PNA_2026
from src.data.lessons_store import LessonsMemoryStore
from src.espn.matchup import MatchupData
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.schemas import (
    MissedOpportunity,
    RecapPlayerPerformance,
    WeeklyRecapReport,
)
from src.main import app


@pytest.fixture
def temp_store_dir():
    """Create a temporary directory for local store testing."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def memory_store(temp_store_dir):
    """Return a LessonsMemoryStore pointing to the temporary directory."""
    return LessonsMemoryStore(bucket_name=None, local_dir=temp_store_dir)


def _make_sample_recap(week: int = 1, result: str = "LOSS", bench_pts: float = 16.5) -> WeeklyRecapReport:
    """Helper to create a structured WeeklyRecapReport."""
    return WeeklyRecapReport(
        league_id=991059191,
        league_name="PNA 2026",
        week=week,
        matchup_status="FINAL",
        result=result,
        user_team_name="Mad Dawg",
        user_score=105.0,
        user_projected=115.0,
        opponent_team_name="Rival Squad",
        opponent_score=112.5,
        opponent_projected=108.0,
        score_margin=-7.5,
        optimal_lineup_points=121.5,
        points_left_on_bench=bench_pts,
        coach_game_summary="Tough loss due to benching high-volume WR.",
        game_balls=[
            RecapPlayerPerformance(
                player_name="Puka Nacua",
                position="WR",
                team="LAR",
                slot="WR",
                actual_points=24.0,
                projected_points=15.0,
                point_differential=9.0,
                verdict_comment="Target magnet who delivered big.",
            )
        ],
        missed_opportunities=[
            MissedOpportunity(
                bench_player="Jayden Reed",
                bench_points=22.4,
                started_player="Diontae Johnson",
                starter_points=6.1,
                points_differential=16.3,
                lesson="Trust tape and escalating snap share in red-zone packages.",
            )
        ],
        busts=[
            RecapPlayerPerformance(
                player_name="Zamir White",
                position="RB",
                team="LV",
                slot="RB",
                actual_points=4.2,
                projected_points=13.0,
                point_differential=-8.8,
                verdict_comment="Game script collapsed in first half.",
            )
        ],
        lessons_learned=[
            "Trust tape and volume over defensive matchup tags.",
            "Bench RBs in negative game scripts.",
        ],
        next_week_priorities=[
            "Target high-target waiver adds.",
            "Promote Reed to starting lineup.",
        ],
        generated_at="Monday, September 28, 2026",
    )


# ---------------------------------------------------------------------------
# 1. Local Storage Tests
# ---------------------------------------------------------------------------


def test_save_and_load_week_recap(memory_store):
    """Test saving a completed week recap and retrieving season data."""
    recap = _make_sample_recap(week=1, result="LOSS", bench_pts=16.3)
    success = memory_store.save_week_recap(recap, season=2026)
    assert success is True

    season_data = memory_store.load_season_data(season=2026, league_id=991059191)
    assert season_data["season"] == 2026
    assert season_data["league_id"] == 991059191
    assert "1" in season_data["weeks"]

    w1 = season_data["weeks"]["1"]
    assert w1["week"] == 1
    assert w1["result"] == "LOSS"
    assert w1["points_left_on_bench"] == 16.3
    assert len(w1["missed_opportunities"]) == 1
    assert w1["missed_opportunities"][0]["bench_player"] == "Jayden Reed"
    assert w1["missed_opportunities"][0]["points_differential"] == 16.3


def test_save_multiple_weeks_chronology(memory_store):
    """Test saving weeks 1 and 2, verifying recent history ordering."""
    recap1 = _make_sample_recap(week=1, result="WIN", bench_pts=5.0)
    recap2 = _make_sample_recap(week=2, result="LOSS", bench_pts=18.0)

    memory_store.save_week_recap(recap1, season=2026)
    memory_store.save_week_recap(recap2, season=2026)

    # For week 3, history should return weeks 2 and 1 (most recent first)
    history = memory_store.get_recent_history(
        league_id=991059191, season=2026, current_week=3, lookback=3
    )
    assert len(history) == 2
    assert history[0]["week"] == 2
    assert history[1]["week"] == 1


# ---------------------------------------------------------------------------
# 2. GCS Storage & Fallback Tests
# ---------------------------------------------------------------------------


def test_gcs_upload_and_download_mock(temp_store_dir):
    """Verify GCS client interaction when GCS is enabled."""
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    sample_json = json.dumps({"season": 2026, "league_id": 991059191, "weeks": {}})
    mock_blob.download_as_text.return_value = sample_json

    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    mock_gcs_client = MagicMock()
    mock_gcs_client.bucket.return_value = mock_bucket

    with patch("google.cloud.storage.Client", return_value=mock_gcs_client):
        store = LessonsMemoryStore(bucket_name="test-bucket", local_dir=temp_store_dir)
        data = store.load_season_data(season=2026, league_id=991059191)

        assert data["season"] == 2026
        mock_bucket.blob.assert_called_with("memory/2026_991059191.json")


def test_gcs_error_graceful_fallback(temp_store_dir):
    """Verify that if GCS throws an exception, it falls back to local storage without crashing."""
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.upload_from_string.side_effect = Exception("GCS Network Timeout")
    mock_bucket.blob.return_value = mock_blob

    mock_gcs_client = MagicMock()
    mock_gcs_client.bucket.return_value = mock_bucket

    with patch("google.cloud.storage.Client", return_value=mock_gcs_client):
        store = LessonsMemoryStore(bucket_name="test-bucket", local_dir=temp_store_dir)
        recap = _make_sample_recap(week=1)
        # Should NOT raise exception, must write to local filesystem
        saved = store.save_week_recap(recap, season=2026)
        assert saved is True

        # Verify local file exists
        local_file = os.path.join(temp_store_dir, "2026_991059191.json")
        assert os.path.exists(local_file)


# ---------------------------------------------------------------------------
# 3. Prompt Formatting Tests
# ---------------------------------------------------------------------------


def test_format_lessons_for_lineup(memory_store):
    """Test generating structured start/sit lessons block."""
    # Week 1 with no prior history
    empty_block = memory_store.format_lessons_for_lineup(
        league_id=991059191, season=2026, current_week=1
    )
    assert empty_block is None

    # After saving week 1
    recap = _make_sample_recap(week=1, result="LOSS", bench_pts=16.3)
    memory_store.save_week_recap(recap, season=2026)

    lineup_block = memory_store.format_lessons_for_lineup(
        league_id=991059191, season=2026, current_week=2
    )
    assert lineup_block is not None
    assert "HISTORICAL TAPE & LINEUP ACCOUNTABILITY" in lineup_block
    assert "Week 1" in lineup_block
    assert "16.3 pts left on bench" in lineup_block
    assert "COSTLY BENCH DECISION: Bench Jayden Reed (22.4 pts)" in lineup_block
    assert "+16.3 pts" in lineup_block
    assert "STARTER BUST: Zamir White" in lineup_block
    assert "COACHING MANDATE FOR WEEK 2 START/SIT DECISIONS" in lineup_block


def test_format_lessons_for_recap(memory_store):
    """Test generating prior week recap takeaways and accountability block."""
    empty_block = memory_store.format_lessons_for_recap(
        league_id=991059191, season=2026, current_week=1
    )
    assert empty_block is None

    recap = _make_sample_recap(week=1)
    memory_store.save_week_recap(recap, season=2026)

    recap_block = memory_store.format_lessons_for_recap(
        league_id=991059191, season=2026, current_week=2
    )
    assert recap_block is not None
    assert "PRIOR FILM ROOM LESSONS & ACTION PLAN ACCOUNTABILITY" in recap_block
    assert "From Week 1 Tape & Film Room:" in recap_block
    assert "Trust tape and volume over defensive matchup tags" in recap_block
    assert "Promote Reed to starting lineup" in recap_block
    assert "ACCOUNTABILITY MANDATE FOR COACH SUMMARY" in recap_block


# ---------------------------------------------------------------------------
# 4. Season Summary & Trends Tests
# ---------------------------------------------------------------------------


def test_season_summary_repeat_benched_players(memory_store):
    """Test seasonal trend aggregation, including repeat bench mistake detection."""
    recap1 = _make_sample_recap(week=1, result="LOSS", bench_pts=16.3)
    recap2 = _make_sample_recap(week=2, result="WIN", bench_pts=12.0)
    # Both weeks benched Jayden Reed suboptimally
    recap2.missed_opportunities = [
        MissedOpportunity(
            bench_player="Jayden Reed",
            bench_points=18.0,
            started_player="Flex Player",
            starter_points=8.0,
            points_differential=10.0,
            lesson="Reed continues to lead team in target volume.",
        )
    ]

    memory_store.save_week_recap(recap1, season=2026)
    memory_store.save_week_recap(recap2, season=2026)

    summary = memory_store.get_season_summary(league_id=991059191, season=2026)
    assert summary["total_completed_weeks"] == 2
    assert summary["record"] == "1-1"
    assert summary["total_bench_pts_lost"] == 28.3
    assert summary["avg_bench_pts_lost"] == 14.2
    assert len(summary["recurring_missed_players"]) == 1
    assert summary["recurring_missed_players"][0]["player"] == "Jayden Reed"
    assert summary["recurring_missed_players"][0]["times_benched_suboptimally"] == 2


# ---------------------------------------------------------------------------
# 5. Pipeline Integration Tests
# ---------------------------------------------------------------------------


def test_generate_weekly_recap_persists_to_store(temp_store_dir):
    """Verify generate_weekly_recap automatically saves final recaps into store."""
    custom_store = LessonsMemoryStore(local_dir=temp_store_dir)
    with patch("src.data.lessons_store.get_lessons_store", return_value=custom_store):
        starters = [
            RosterPlayer("Purdy", "QB", "QB", "SF", 20.0, 18.0, has_played=True),
            RosterPlayer("Flex1", "WR", "FLEX", "GB", 10.0, 12.0, has_played=True),
        ]
        bench = [
            RosterPlayer("Reed", "WR", "BE", "GB", 22.0, 10.0, has_played=True),
        ]
        roster = ParsedRoster(
            team_name="Mad Dawg",
            players=starters + bench,
            starters=starters,
            bench=bench,
            ir=[],
        )
        matchup = MatchupData(
            week=1,
            your_team=roster,
            opponent_team=None,
            your_projected=30.0,
            opp_projected=0.0,
            projected_margin=30.0,
            is_favorite=True,
            your_score=30.0,
            opp_score=0.0,
        )

        report = generate_weekly_recap(PNA_2026, 1, matchup, client=None)
        assert report.matchup_status == "FINAL"

        # Verify it was persisted in custom_store
        season_data = custom_store.load_season_data(season=2026, league_id=PNA_2026.league_id)
        assert "1" in season_data["weeks"]
        assert season_data["weeks"]["1"]["points_left_on_bench"] == report.points_left_on_bench


def test_optimize_lineup_injects_prior_lessons(temp_store_dir):
    """Verify optimize_lineup loads prior lessons and passes them to prompt."""
    custom_store = LessonsMemoryStore(local_dir=temp_store_dir)
    recap = _make_sample_recap(week=1, result="LOSS", bench_pts=16.3)
    custom_store.save_week_recap(recap, season=2026)

    captured_prompt = None

    def mock_generate_structured(prompt, response_schema):
        nonlocal captured_prompt
        captured_prompt = prompt
        mock_rec = MagicMock()
        mock_rec.game_theory_strategy = "Test strategy"
        mock_rec.recommended_starters = []
        mock_rec.bench_players = []
        mock_rec.key_flex_decisions = []
        mock_rec.lineup_hole_alerts = []
        return mock_rec

    mock_client = MagicMock()
    mock_client.generate_structured.side_effect = mock_generate_structured

    with patch("src.data.lessons_store.get_lessons_store", return_value=custom_store):
        starters = [
            RosterPlayer("Purdy", "QB", "QB", "SF", 0.0, 18.0, has_played=False),
        ]
        roster = ParsedRoster(
            team_name="Mad Dawg",
            players=starters,
            starters=starters,
            bench=[],
            ir=[],
        )
        matchup = MatchupData(
            week=2,
            your_team=roster,
            opponent_team=None,
            your_projected=18.0,
            opp_projected=0.0,
            projected_margin=18.0,
            is_favorite=True,
            your_score=0.0,
            opp_score=0.0,
        )

        optimize_lineup(
            league=PNA_2026,
            week=2,
            roster=roster,
            matchup=matchup,
            client=mock_client,
        )

        assert captured_prompt is not None
        assert "HISTORICAL TAPE & LINEUP ACCOUNTABILITY" in captured_prompt
        assert "COSTLY BENCH DECISION: Bench Jayden Reed" in captured_prompt


# ---------------------------------------------------------------------------
# 6. FastAPI Endpoints Tests
# ---------------------------------------------------------------------------


def test_query_lessons_endpoints(temp_store_dir):
    """Verify /query/lessons-summary and /query/lessons endpoints."""
    custom_store = LessonsMemoryStore(local_dir=temp_store_dir)
    recap1 = _make_sample_recap(week=1, result="WIN", bench_pts=0.0)
    recap2 = _make_sample_recap(week=2, result="LOSS", bench_pts=15.0)
    custom_store.save_week_recap(recap1, season=2026)
    custom_store.save_week_recap(recap2, season=2026)

    client = TestClient(app)

    with patch("src.data.lessons_store.get_lessons_store", return_value=custom_store):
        # 1. Summary endpoint
        res = client.get("/query/lessons-summary?league_id=991059191&season=2026")
        assert res.status_code == 200
        summary = res.json()
        assert summary["league_id"] == 991059191
        assert summary["record"] == "1-1"
        assert summary["total_completed_weeks"] == 2
        assert summary["total_bench_pts_lost"] == 15.0

        # 2. History endpoint
        res_hist = client.get("/query/lessons?league_id=991059191&season=2026&current_week=3")
        assert res_hist.status_code == 200
        hist = res_hist.json()
        assert hist["count"] == 2
        assert hist["history"][0]["week"] == 2
        assert hist["history"][1]["week"] == 1
