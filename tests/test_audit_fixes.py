"""
Comprehensive regression tests for Tier 1 accuracy/reliability fixes and Tier 2
fantasy intelligence upgrades (IR lifecycle, Thursday FLEX hygiene, trade deadline,
UTC->ET weather alignment, duplicate-name injury disambiguation, traded player stats,
Tuesday recap FINAL / upcoming waiver week targeting, and fast cold-start probing).
"""

import zoneinfo
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import polars as pl

from src.analysis.lineup import (
    detect_lineup_holes_and_solutions,
    enrich_lineup_recommendation_with_espn_status,
    optimize_lineup,
)
from src.analysis.matchup_preview import generate_matchup_preview
from src.analysis.recap import _compute_optimal_points
from src.analysis.trade_finder import propose_league_trades
from src.analysis.trades import compute_optimal_starters, evaluate_trade
from src.analysis.waivers import evaluate_waivers
from src.config import CHIPS_AHOY, PNA_2026
from src.data import injuries as injuries_mod
from src.data import stats as stats_mod
from src.data.vegas import GameOdds, get_player_game_odds
from src.data.weather import fetch_game_weather
from src.espn.matchup import MatchupData
from src.espn.roster import (
    ParsedRoster,
    RosterPlayer,
    enrich_roster_from_box_matchup,
    get_safe_drop_candidates,
    parse_roster,
)
from src.intelligence.gemini_client import negotiate_active_model, set_negotiated_model
from src.intelligence.schemas import LineupRecommendation, StartSitDecision
from src.notifications.email import DEFAULT_APP_URL, _build_email_html

# ---------------------------------------------------------------------------
# 1. Roster Parsing: Bye vs Injury, Pre-Game 0.0 Lock, Mid-Game Kickoff Lock,
#    ROS Baseline Anchor, Protected Asset Tuning & BoxPlayer DvP Enrichment
# ---------------------------------------------------------------------------


def test_out_and_ir_players_with_zero_projection_are_not_misclassified_as_bye():
    mock_team = MagicMock()
    mock_team.team_name = "Mad Dawg"

    devonta = MagicMock()
    devonta.name = "DeVonta Smith"
    devonta.position = "WR"
    devonta.proTeam = "PHI"
    devonta.lineupSlot = "Bench"
    devonta.projected_points = 0.0
    devonta.points = 0.0
    devonta.injuryStatus = "OUT"
    devonta.percent_owned = 94.0
    devonta.stats = {8: {"projected_points": 0.0}, 0: {"avg_points": 14.2}}
    devonta.pro_opponent = None
    devonta.game_played = 0
    devonta.game_date = None

    spears = MagicMock()
    spears.name = "Tyjae Spears"
    spears.position = "RB"
    spears.proTeam = "TEN"
    spears.lineupSlot = "IR"
    spears.projected_points = 0.0
    spears.points = 0.0
    spears.injuryStatus = "IR"
    spears.percent_owned = 42.0
    spears.stats = {8: {"projected_points": 0.0}, 0: {"avg_points": 8.4}}
    spears.pro_opponent = None
    spears.game_played = 0
    spears.game_date = None

    mock_team.roster = [devonta, spears]
    parsed = parse_roster(mock_team, PNA_2026, week=8)

    by_name = {p.name: p for p in parsed.players + parsed.ir}
    assert by_name["DeVonta Smith"].is_on_bye(8) is False
    assert by_name["Tyjae Spears"].is_on_bye(8) is False


def test_pre_game_zero_actual_points_in_stats_dict_does_not_lock_player():
    mock_team = MagicMock()
    mock_team.team_name = "Mad Dawg"

    p = MagicMock()
    p.name = "Amon-Ra St. Brown"
    p.position = "WR"
    p.proTeam = "DET"
    p.lineupSlot = "WR"
    p.projected_points = 0.0
    p.points = 0.0
    p.injuryStatus = "ACTIVE"
    p.percent_owned = 99.5
    p.lineupLocked = False
    p.game_played = 0
    p.game_date = datetime.now(timezone.utc) + timedelta(hours=5)
    # ESPN pre-populates stats[week]["points"] = 0.0 before Sunday kickoff
    p.stats = {8: {"projected_points": 18.4, "points": 0.0}, 0: {"avg_points": 19.1}}

    mock_team.roster = [p]
    parsed = parse_roster(mock_team, PNA_2026, week=8)
    amon_ra = parsed.starters[0]
    assert amon_ra.projected_points == 18.4
    assert amon_ra.has_played is False
    assert amon_ra.is_locked is False


def test_box_player_game_date_kickoff_passed_locks_mid_game_player():
    mock_team = MagicMock()
    mock_team.team_name = "Mad Dawg"

    p = MagicMock()
    p.name = "Josh Allen"
    p.position = "QB"
    p.proTeam = "BUF"
    p.lineupSlot = "QB"
    p.projected_points = 23.0
    p.points = 0.0
    p.injuryStatus = "ACTIVE"
    p.percent_owned = 99.9
    p.lineupLocked = False
    p.game_played = 0  # ESPN leaves game_played=0 during Q1-Q4 until game goes final
    p.game_date = datetime.now(timezone.utc) - timedelta(minutes=35)
    p.stats = {}

    mock_team.roster = [p]
    parsed = parse_roster(mock_team, PNA_2026, week=8)
    assert parsed.starters[0].is_locked is True


def test_ros_baseline_ppg_uses_season_avg_and_is_protected_asset_tuning():
    mock_team = MagicMock()
    mock_team.team_name = "Mad Dawg"

    spike_wr = MagicMock()
    spike_wr.name = "Bench WR5 Filler"
    spike_wr.position = "WR"
    spike_wr.proTeam = "CAR"
    spike_wr.lineupSlot = "Bench"
    spike_wr.projected_points = 8.0
    spike_wr.points = 0.0
    spike_wr.injuryStatus = "ACTIVE"
    spike_wr.percent_owned = 28.0
    spike_wr.lineupLocked = False
    spike_wr.game_played = 0
    spike_wr.game_date = None
    # Season avg 9.5 + current proj 8.0 -> blended ~8.9 PPG (< 12.0 and < 65% owned -> droppable!)
    spike_wr.stats = {
        0: {"avg_points": 9.5},
        1: {"points": 22.0},
        8: {"projected_points": 8.0},
    }

    star_rb = MagicMock()
    star_rb.name = "Core Star RB"
    star_rb.position = "RB"
    star_rb.proTeam = "BUF"
    star_rb.lineupSlot = "Bench"
    star_rb.projected_points = 0.0
    star_rb.points = 0.0
    star_rb.injuryStatus = "ACTIVE"
    star_rb.percent_owned = 96.0
    star_rb.lineupLocked = False
    star_rb.game_played = 0
    star_rb.game_date = None
    star_rb.pro_opponent = "BYE"
    star_rb.stats = {0: {"avg_points": 16.2}, 8: {"projected_points": 0.0}}

    mock_team.roster = [spike_wr, star_rb]
    parsed = parse_roster(mock_team, PNA_2026, week=8)
    by_name = {p.name: p for p in parsed.players}

    assert by_name["Bench WR5 Filler"].ros_baseline_ppg == 8.9
    assert by_name["Bench WR5 Filler"].is_protected_asset is False
    assert by_name["Core Star RB"].ros_baseline_ppg == 16.2
    assert by_name["Core Star RB"].is_protected_asset is True

    drops = get_safe_drop_candidates(parsed, current_week=8)
    assert [d.name for d in drops] == ["Bench WR5 Filler"]


def test_enrich_roster_from_box_matchup_transfers_dvp_and_clears_false_bye():
    team_roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[
            RosterPlayer(
                name="Terry McLaurin",
                position="WR",
                team="WSH",
                slot="WR",
                projected_points=0.0,
                bye_week=8,  # False bye inferred before enrichment
                percent_owned=92.0,
            )
        ],
    )
    box_roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[
            RosterPlayer(
                name="Terry McLaurin",
                position="WR",
                team="WSH",
                slot="WR",
                projected_points=15.6,
                pro_opponent="CHI",
                opponent_pos_rank=29,
                is_locked=False,
            )
        ],
    )
    odds = [
        GameOdds(
            game_id="g1",
            home_team="WAS",
            away_team="CHI",
            game_time=datetime(2026, 10, 25, 20, 25, tzinfo=timezone.utc),
            spread=-3.0,
            over_under=47.5,
            home_implied_total=25.25,
            away_implied_total=22.25,
            status="pre",
        )
    ]

    enrich_roster_from_box_matchup(team_roster, box_roster, current_week=8, odds=odds)
    terry = team_roster.starters[0]
    assert terry.pro_opponent == "CHI"
    assert terry.opponent_pos_rank == 29
    assert terry.projected_points == 15.6
    assert terry.is_on_bye(8) is False


# ---------------------------------------------------------------------------
# 2. Weather UTC->ET Forecast Alignment & Stadium / Vegas Team Abbreviations
# ---------------------------------------------------------------------------


def test_weather_converts_utc_kickoff_to_america_new_york_and_resolves_jac_la(monkeypatch):
    # Dome check for "LA" (ESPN Rams abbreviation)
    la_weather = fetch_game_weather("LA", datetime(2026, 10, 25, 20, 5, tzinfo=timezone.utc))
    assert la_weather is not None
    assert la_weather.is_dome is True
    assert "SoFi" in la_weather.stadium_name

    # Outdoor UTC->ET check for "JAC" at 17:00 UTC (1:00 PM EDT = 13:00 local)
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "hourly": {
                    "time": ["2026-10-25T13:00", "2026-10-25T17:00"],
                    "temperature_2m": [74.0, 51.0],
                    "wind_speed_10m": [8.0, 29.0],
                    "wind_gusts_10m": [12.0, 42.0],
                    "precipitation_probability": [0, 80],
                }
            }

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr("src.data.weather.httpx.Client", FakeHttpClient)
    jac_weather = fetch_game_weather("JAC", datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc))
    assert jac_weather is not None
    assert "EverBank" in jac_weather.stadium_name
    # Must match 13:00 ET (74.0F, Clear), NOT 17:00 ET (51.0F, Wind/Rain)!
    assert jac_weather.temperature_f == 74.0
    assert jac_weather.wind_speed_mph == 8.0


def test_vegas_get_player_game_odds_normalizes_both_sides():
    odds = [
        GameOdds(
            game_id="g2",
            home_team="WSH",
            away_team="JAX",
            game_time=datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc),
            spread=-2.5,
            over_under=45.5,
            home_implied_total=24.0,
            away_implied_total=21.5,
            status="pre",
        )
    ]
    assert get_player_game_odds("WAS", odds) is not None
    assert get_player_game_odds("WSH", odds) is not None
    assert get_player_game_odds("JAC", odds) is not None
    assert get_player_game_odds("JAX", odds) is not None


# ---------------------------------------------------------------------------
# 3. Injuries Duplicate-Name Disambiguation, Team Aliases & TTL Refresh
# ---------------------------------------------------------------------------


def test_injury_report_prefers_offensive_fantasy_player_and_team_aliases(monkeypatch):
    fake_db = {
        "cb_1": injuries_mod.PlayerInjuryInfo(
            player_id="cb_1",
            full_name="Michael Carter II",
            position="CB",
            team="NYJ",
            injury_status="Out",
            practice_participation=None,
            injury_body_part=None,
            injury_notes=None,
            sport="nfl",
            active=True,
        ),
        "rb_1": injuries_mod.PlayerInjuryInfo(
            player_id="rb_1",
            full_name="Michael Carter",
            position="RB",
            team="ARI",
            injury_status=None,
            practice_participation=None,
            injury_body_part=None,
            injury_notes=None,
            sport="nfl",
            active=True,
        ),
        "was_wr": injuries_mod.PlayerInjuryInfo(
            player_id="was_wr",
            full_name="Noah Brown",
            position="WR",
            team="WAS",
            injury_status="Questionable",
            practice_participation="Limited Participation",
            injury_body_part="Groin",
            injury_notes=None,
            sport="nfl",
            active=True,
        ),
    }
    monkeypatch.setattr(injuries_mod, "_ALL_PLAYERS_CACHE", fake_db)
    monkeypatch.setattr(injuries_mod, "_ALL_PLAYERS_CACHE_TS", 9999999999.0)

    reports = injuries_mod.get_injury_report(["Michael Carter"])
    assert len(reports) == 1
    assert reports[0].position == "RB"
    assert reports[0].team == "ARI"
    assert reports[0].injury_status is None

    # ESPN uses "WSH" while Sleeper stores "WAS"
    wsh_inj = injuries_mod.get_team_injuries("WSH")
    assert any(p.full_name == "Noah Brown" for p in wsh_inj)


# ---------------------------------------------------------------------------
# 4. Mid-Season Traded Player Stats Aggregation
# ---------------------------------------------------------------------------


def test_traded_player_stats_and_snaps_sum_across_teams_retaining_latest_team(monkeypatch):
    monkeypatch.setattr(stats_mod, "_player_stats_cache", {})
    monkeypatch.setattr(stats_mod, "_player_stats_cache_ts", {})
    monkeypatch.setattr(stats_mod, "_snap_counts_cache", {})
    monkeypatch.setattr(stats_mod, "_snap_counts_cache_ts", {})

    stats_df = pl.DataFrame(
        {
            "player_id": ["00-0031381", "00-0031381"],
            "player_display_name": ["Davante Adams", "Davante Adams"],
            "position": ["WR", "WR"],
            "recent_team": ["LV", "NYJ"],
            "week": [2, 7],
            "season_type": ["REG", "REG"],
            "targets": [9, 11],
            "receptions": [6, 7],
            "receiving_yards": [88.0, 94.0],
            "receiving_tds": [1, 1],
            "carries": [0, 0],
            "rushing_yards": [0.0, 0.0],
            "rushing_tds": [0, 0],
            "target_share": [0.28, 0.32],
            "air_yards_share": [0.38, 0.42],
            "wopr": [0.68, 0.74],
            "fantasy_points_ppr": [20.8, 22.4],
        }
    )
    snaps_df = pl.DataFrame(
        {
            "player": ["Davante Adams", "Davante Adams"],
            "team": ["LV", "NYJ"],
            "position": ["WR", "WR"],
            "week": [2, 7],
            "game_type": ["REG", "REG"],
            "offense_snaps": [58, 62],
            "offense_pct": [0.90, 0.94],
        }
    )

    monkeypatch.setattr(stats_mod.nfl, "load_player_stats", lambda seasons: stats_df)
    monkeypatch.setattr(stats_mod.nfl, "load_snap_counts", lambda seasons: snaps_df)
    monkeypatch.setattr(stats_mod.nfl, "load_pbp", lambda seasons: pl.DataFrame())

    p_stats = stats_mod.fetch_player_stats(2026)
    adams = p_stats["Davante Adams"]
    assert adams.games_played == 2
    assert adams.targets == 20
    assert adams.receptions == 13
    assert adams.team == "NYJ"

    s_stats = stats_mod.fetch_snap_counts(2026)
    adams_snaps = s_stats["Davante Adams"]
    assert adams_snaps.games_played == 2
    assert adams_snaps.snap_count == 120
    assert adams_snaps.team == "NYJ"


# ---------------------------------------------------------------------------
# 5. Trades: COUNTER UnboundLocalError Fix, 1-for-2 Forced Drop, IR Exclusion,
#    ESPN Player.stats Lookup & Expired Trade Deadline
# ---------------------------------------------------------------------------


def _mk_rp(
    name: str,
    pos: str,
    pts: float,
    slot: str = "Bench",
    inj: str = "ACTIVE",
    pct: float = 85.0,
    team: str = "KC",
) -> RosterPlayer:
    return RosterPlayer(
        name=name,
        position=pos,
        team=team,
        slot=slot,
        projected_points=pts,
        injury_status=inj,
        percent_owned=pct,
        ros_baseline_ppg=pts,
    )


def test_evaluate_trade_counter_verdict_does_not_raise_unbound_local_error():
    roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[
            _mk_rp("QB1", "QB", 20.0, "QB"),
            _mk_rp("RB1", "RB", 15.0, "RB"),
            _mk_rp("RB2", "RB", 13.0, "RB"),
            _mk_rp("WR1", "WR", 16.0, "WR"),
            _mk_rp("WR2", "WR", 12.0, "WR"),
            _mk_rp("TE1", "TE", 10.0, "TE"),
            _mk_rp("FLEX1", "RB", 11.0, "FLEX"),
            _mk_rp("FLEX2", "WR", 10.5, "FLEX"),
            _mk_rp("DST1", "DST", 7.5, "D/ST"),
        ],
    )
    # Give WR2 (12.0) for Incoming WR (13.0) -> +1.0 marginal upgrade -> COUNTER branch!
    res = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["WR2"],
        receiving_players=["Slightly Better WR"],
        player_projections={"Slightly Better WR": ("WR", 13.0)},
    )
    assert res.verdict == "COUNTER"
    assert res.counter_suggestion is not None
    assert res.counter_suggestion in res.coach_conviction


def test_uneven_1_for_2_trade_accounts_for_forced_bench_drop_on_full_roster():
    # PNA_2026 max active roster = 9 starters + 7 bench = 16 active players
    players = [
        _mk_rp("QB1", "QB", 20.0, "QB"),
        _mk_rp("RB1", "RB", 16.0, "RB"),
        _mk_rp("RB2", "RB", 14.0, "RB"),
        _mk_rp("WR1", "WR", 17.0, "WR"),
        _mk_rp("WR2", "WR", 15.0, "WR"),
        _mk_rp("TE1", "TE", 11.0, "TE"),
        _mk_rp("FLEX1", "RB", 12.0, "FLEX"),
        _mk_rp("FLEX2", "WR", 11.5, "FLEX"),
        _mk_rp("DST1", "DST", 7.5, "D/ST"),
        _mk_rp("Bench1", "RB", 10.0, "Bench", pct=70.0),
        _mk_rp("Bench2", "WR", 9.8, "Bench", pct=70.0),
        _mk_rp("Bench3", "RB", 9.2, "Bench", pct=68.0),
        _mk_rp("Bench4", "WR", 8.8, "Bench", pct=66.0),
        _mk_rp("Bench5", "TE", 8.0, "Bench", pct=50.0),
        _mk_rp("Bench6", "QB", 15.0, "Bench", pct=55.0),
        _mk_rp("EndOfBenchClogger", "WR", 5.2, "Bench", pct=12.0),
    ]
    roster = ParsedRoster(team_name="Mad Dawg", players=players)
    res = evaluate_trade(
        league=PNA_2026,
        roster=roster,
        giving_players=["RB1"],
        receiving_players=["Incoming RB A", "Incoming WR B"],
        player_projections={
            "Incoming RB A": ("RB", 15.5),
            "Incoming WR B": ("WR", 14.5),
        },
    )
    assert any(
        "Roster Spot Drop Required" in c and "EndOfBenchClogger" in c
        for c in res.starting_lineup_changes
    )


def test_compute_optimal_starters_and_recap_exclude_unactivated_ir_stashes():
    players = [
        _mk_rp("QB1", "QB", 18.0, "QB"),
        _mk_rp("RB1", "RB", 14.0, "RB"),
        _mk_rp("RB2", "RB", 12.0, "RB"),
        _mk_rp("WR1", "WR", 15.0, "WR"),
        _mk_rp("WR2", "WR", 13.0, "WR"),
        _mk_rp("TE1", "TE", 9.0, "TE"),
        _mk_rp("FLEX1", "WR", 10.0, "FLEX"),
        _mk_rp("FLEX2", "RB", 9.5, "FLEX"),
        _mk_rp("DST1", "DST", 7.0, "D/ST"),
        RosterPlayer(
            name="IR Stash Superstar",
            position="WR",
            team="LAR",
            slot="IR",
            projected_points=21.0,
            actual_points=24.0,
            has_played=True,
            injury_status="ACTIVE",
            ros_baseline_ppg=21.0,
        ),
    ]
    for p in players[:-1]:
        p.actual_points = p.projected_points
        p.has_played = True

    starters, _, _ = compute_optimal_starters(players, PNA_2026)
    assert all(s.name != "IR Stash Superstar" for s in starters)

    opt_recap_pts = _compute_optimal_points(players, PNA_2026, is_final=True)
    expected_active_sum = sum(p.actual_points for p in players[:-1])
    assert opt_recap_pts == round(expected_active_sum, 1)


def test_expired_trade_deadline_returns_hold_roster_and_blocks_tier3_trade_rec():
    past_epoch_ms = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp() * 1000)

    mock_league = MagicMock()
    mock_league.settings.trade_deadline = past_epoch_ms

    user_team = MagicMock()
    user_team.team_id = PNA_2026.team_id
    user_team.team_name = "Mad Dawg"
    user_team.roster = []
    mock_league.teams = [user_team]

    report = propose_league_trades(PNA_2026, week=12, espn_league=mock_league)
    assert report.coach_verdict == "HOLD_ROSTER"
    assert report.is_trade_recommended is False
    assert "trade deadline" in (report.hold_roster_reasoning or "").lower()

    hole_roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[_mk_rp("Injured QB", "QB", 0.0, slot="QB", inj="OUT")],
    )
    holes = detect_lineup_holes_and_solutions(
        hole_roster, PNA_2026, current_week=12, espn_league=mock_league
    )
    assert any("trade deadline" in (h.trade_recommendation or "").lower() for h in holes)


# ---------------------------------------------------------------------------
# 6. Tier 2: IR-Slot Lifecycle (Waivers & Lineup) + Thursday FLEX Hygiene
# ---------------------------------------------------------------------------


def test_waivers_open_ir_slot_with_out_player_recommends_free_ir_move_no_drop():
    # CHIPS_AHOY has 2 IR slots; 0 used here while Nico Collins is OUT on bench
    roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[
            _mk_rp("QB1", "QB", 21.0, "QB"),
            _mk_rp("RB1", "RB", 16.0, "RB"),
            _mk_rp("RB2", "RB", 14.0, "RB"),
            _mk_rp("WR1", "WR", 17.0, "WR"),
            _mk_rp("WR2", "WR", 15.0, "WR"),
            _mk_rp("TE1", "TE", 12.0, "TE"),
            _mk_rp("FLEX1", "RB", 13.0, "FLEX"),
            _mk_rp("K1", "K", 8.5, "K"),
            _mk_rp("DST1", "DST", 8.0, "D/ST"),
            _mk_rp("Nico Collins", "WR", 0.0, "Bench", inj="OUT", pct=98.0),
            _mk_rp("Protected Bench RB", "RB", 12.5, "Bench", pct=85.0),
        ],
    )
    free_agents = [
        {
            "name": "Breakout Handcuff RB",
            "position": "RB",
            "team": "SF",
            "projected_points": 11.4,
            "percent_owned": 44.0,
            "injury_status": "ACTIVE",
        }
    ]
    report = evaluate_waivers(CHIPS_AHOY, week=8, roster=roster, free_agents=free_agents)
    assert report.coach_verdict == "EXECUTE_CLAIMS"
    assert report.is_move_recommended is True
    assert len(report.targets) >= 1
    assert "Move Nico Collins to IR" in (report.targets[0].recommended_drop or "")
    assert "FREE ROSTER SPOT AVAILABLE" in report.overall_waiver_strategy


def test_waivers_healthy_player_stuck_in_ir_emits_espn_lock_warning_and_faab_context():
    roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[
            _mk_rp("QB1", "QB", 22.0, "QB"),
            _mk_rp("Bench Stud", "RB", 14.0, "Bench", pct=90.0),
            _mk_rp("Puka Nacua", "WR", 16.5, "IR", inj="ACTIVE", pct=98.0),
        ],
    )
    mock_espn = MagicMock()
    mock_espn.settings.faab = True
    mock_espn.settings.acquisition_budget = 100
    my_team = MagicMock()
    my_team.team_id = PNA_2026.team_id
    my_team.acquisition_budget_spent = 35
    mock_espn.teams = [my_team]

    report = evaluate_waivers(
        PNA_2026,
        week=8,
        roster=roster,
        free_agents=[{"name": "Low FA", "position": "WR", "team": "NYG", "projected_points": 4.0, "percent_owned": 5.0}],
        espn_league=mock_espn,
    )
    assert "ESPN IR ROSTER LOCK" in report.overall_waiver_strategy
    assert "Puka Nacua" in report.overall_waiver_strategy
    assert "$65/$100" in report.overall_waiver_strategy


def test_lineup_ir_activation_and_thursday_flex_hygiene_alert():
    eastern = zoneinfo.ZoneInfo("America/New_York")
    thu_game = datetime(2026, 10, 22, 20, 15, tzinfo=eastern)  # Thursday 8:15 PM ET
    sun_game = datetime(2026, 10, 25, 13, 0, tzinfo=eastern)  # Sunday 1:00 PM ET
    odds = [
        GameOdds(
            game_id="g_thu",
            home_team="DEN",
            away_team="NO",
            game_time=thu_game,
            spread=-3.0,
            over_under=43.5,
            home_implied_total=23.25,
            away_implied_total=20.25,
            status="pre",
        ),
        GameOdds(
            game_id="g_sun",
            home_team="ATL",
            away_team="SEA",
            game_time=sun_game,
            spread=-2.5,
            over_under=51.0,
            home_implied_total=26.75,
            away_implied_total=24.25,
            status="pre",
        ),
    ]

    roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[
            # Courtland Sutton plays Thursday night but is mistakenly sitting in FLEX!
            _mk_rp("Courtland Sutton", "WR", 14.2, slot="FLEX", team="DEN"),
            # Drake London plays Sunday afternoon and sits in dedicated WR slot
            _mk_rp("Drake London", "WR", 17.5, slot="WR", team="ATL"),
            # Healthy star sitting in IR slot ready to be activated into vacant WR2 slot
            _mk_rp("DK Metcalf", "WR", 16.0, slot="IR", inj="ACTIVE", team="SEA"),
        ],
    )
    rec = LineupRecommendation(
        league_id=PNA_2026.league_id,
        week=8,
        game_theory_strategy="BALANCED",
        strategy_reasoning="Test",
        recommended_starters=[
            StartSitDecision(
                player_name="Drake London",
                position="WR",
                team="ATL",
                action="START",
                confidence=0.9,
                floor=12.0,
                ceiling=24.0,
                projected_points=17.5,
                reasoning="Alpha WR1",
            ),
            StartSitDecision(
                player_name="DK Metcalf",
                position="WR",
                team="SEA",
                action="START",
                confidence=0.88,
                floor=11.0,
                ceiling=23.0,
                projected_points=16.0,
                reasoning="Healthy deep threat",
            ),
            StartSitDecision(
                player_name="Courtland Sutton",
                position="FLEX",
                team="DEN",
                action="START",
                confidence=0.82,
                floor=10.0,
                ceiling=20.0,
                projected_points=14.2,
                reasoning="Volume target leader",
            ),
        ],
        bench_players=[],
        key_flex_decisions=[],
    )

    enriched = enrich_lineup_recommendation_with_espn_status(
        rec, roster, PNA_2026, odds=odds, current_week=8
    )

    dk_starter = next(s for s in enriched.recommended_starters if s.player_name == "DK Metcalf")
    assert dk_starter.alignment == "SWAP_TO_START"
    assert any("Activate DK Metcalf from IR" in sw for sw in enriched.actionable_swaps)
    assert any(
        "FLEX Hygiene" in sw and "Courtland Sutton" in sw and "Drake London" in sw
        for sw in enriched.actionable_swaps
    )
    assert any(
        b.player_name == "DK Metcalf" and b.action_label == "🏥 ACTIVATE & START"
        for b in enriched.current_bench
    )


def test_optimize_lineup_normalizes_team_abbr_for_vegas_home_implied_total():
    odds = [
        GameOdds(
            game_id="g_was",
            home_team="WAS",
            away_team="CHI",
            game_time=datetime(2026, 10, 25, 20, 25, tzinfo=timezone.utc),
            spread=-4.0,
            over_under=48.0,
            home_implied_total=26.0,
            away_implied_total=22.0,
            status="pre",
        )
    ]
    # ESPN uses "WSH" while The Odds API uses "WAS"
    roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[_mk_rp("Jayden Daniels", "QB", 22.4, slot="QB", team="WSH")],
    )
    rec = optimize_lineup(PNA_2026, week=8, roster=roster, odds=odds)
    jayden = next(s for s in rec.recommended_starters if s.player_name == "Jayden Daniels")
    assert "26.0 pts" in (jayden.game_script_note or "")


# ---------------------------------------------------------------------------
# 7. Matchup Preview Hole Alert, Email Fractional Win Prob, Fast Model Probe &
#    Tuesday Pre-Rollover Recap FINAL + Upcoming Waiver Week
# ---------------------------------------------------------------------------


def test_matchup_preview_flags_unswapped_out_starter():
    your_roster = ParsedRoster(
        team_name="Mad Dawg",
        players=[
            _mk_rp("QB1", "QB", 21.0, slot="QB"),
            _mk_rp("Injured RB", "RB", 0.0, slot="RB", inj="OUT"),
        ],
    )
    opp_roster = ParsedRoster(
        team_name="Rival",
        players=[_mk_rp("Opp QB", "QB", 19.0, slot="QB")],
    )
    matchup = MatchupData(
        week=8,
        your_team=your_roster,
        opponent_team=opp_roster,
        your_score=0.0,
        opp_score=0.0,
        your_projected=112.0,
        opp_projected=108.0,
        projected_margin=4.0,
        is_favorite=True,
    )
    report = generate_matchup_preview(PNA_2026, week=8, matchup=matchup)
    assert any("UNSWAPPED LINEUP HOLE" in v and "Injured RB" in v for v in report.key_vulnerabilities)


def test_email_renders_fractional_win_probability_as_green_percentage_and_scouting_fields():
    html = _build_email_html(
        subject="Saturday Scouting Report",
        job_type="weekly_analysis",
        results={
            "PNA 2026": {
                "opponent_name": "Gridiron Gurus",
                "projected_score_user": 128.4,
                "projected_score_opponent": 116.2,
                "projected_margin": 12.2,
                "win_probability": 0.62,
                "strategic_summary": "Control the clock with volume RBs.",
                "key_advantages": ["Strong advantage at RB (+8.4 pts)"],
                "key_vulnerabilities": ["🚨 UNSWAPPED LINEUP HOLE: DeVonta Smith (WR) is OUT"],
                "weather_and_vegas_factors": ["BUF @ SEA: O/U 51.5 shootout"],
            }
        },
        timestamp="October 24, 2026 at 09:00 AM EDT",
    )
    assert "62%" in html
    assert "0.62%" not in html
    assert "#22c55e" in html
    assert "Control the clock with volume RBs." in html
    assert "Strong advantage at RB" in html
    assert "UNSWAPPED LINEUP HOLE" in html
    assert DEFAULT_APP_URL in html


def test_negotiate_active_model_fast_probe_stops_on_first_available_model():
    call_count = 0

    class CountingModels:
        def generate_content(self, model, contents, config=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.text = "pong"
            resp.candidates = [MagicMock()]
            return resp

    class CountingClient:
        models = CountingModels()

    try:
        active, diag = negotiate_active_model(
            force=True, mock_client=CountingClient(), probe_all=False
        )
        assert active == "gemini-4-argon"
        assert call_count == 1
        assert diag["fast_probe"] is True
    finally:
        set_negotiated_model(None)


def test_tuesday_weekly_run_when_espn_has_not_rolled_week_marks_recap_final_and_targets_next_waiver_week(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from src.intelligence.schemas import WaiverReport, WeeklyRecapReport
    from src.main import app

    class FakeTuesdayDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 27, 7, 5, tzinfo=tz)  # Tuesday 7:05 AM EDT

    monkeypatch.setattr("src.main.datetime", FakeTuesdayDatetime)

    mock_espn = MagicMock()
    mock_espn.current_week = 7  # ESPN hasn't rolled to Week 8 yet at 7:00 AM Tuesday!
    mock_team = MagicMock()
    mock_team.team_id = 3
    mock_team.team_name = "Mad Dawg"
    mock_team.roster = []
    mock_espn.teams = [mock_team]

    class FakeLeagueClient:
        def get_league(self, cfg):
            return mock_espn

    played_starter = _mk_rp("Josh Allen", "QB", 22.0, slot="QB")
    played_starter.actual_points = 26.4
    played_starter.has_played = True
    fake_matchup = MatchupData(
        week=7,
        your_team=ParsedRoster(team_name="Mad Dawg", players=[played_starter]),
        opponent_team=ParsedRoster(team_name="Rival", players=[]),
        your_score=132.4,
        opp_score=118.0,
        your_projected=125.0,
        opp_projected=120.0,
        projected_margin=5.0,
        is_favorite=True,
    )

    recap_calls: list[tuple[int, bool]] = []
    waiver_calls: list[int] = []

    def fake_recap(league, week, matchup, client=None, is_past_week=False):
        recap_calls.append((week, is_past_week))
        return WeeklyRecapReport(
            league_id=league.league_id,
            league_name=league.name,
            week=week,
            matchup_status="FINAL" if is_past_week else "IN_PROGRESS",
            result="WIN",
            user_team_name="Mad Dawg",
            user_score=132.4,
            user_projected=125.0,
            opponent_team_name="Rival",
            opponent_score=118.0,
            opponent_projected=120.0,
            score_margin=14.4,
            optimal_lineup_points=136.0,
            points_left_on_bench=3.6,
            coach_game_summary="Dominant Week 7 win.",
        )

    def fake_waivers(
        league,
        week,
        roster,
        free_agents,
        trending_adds=None,
        injuries=None,
        client=None,
        espn_league=None,
    ):
        waiver_calls.append(week)
        return WaiverReport(
            league_id=league.league_id,
            week=week,
            is_move_recommended=False,
            coach_verdict="STAND_PAT",
            stand_pat_reasoning="Stand pat for Week 8.",
            targets=[],
            roster_drop_candidates=[],
            overall_waiver_strategy="Hold priority.",
        )

    monkeypatch.setattr("src.main.LeagueClient", FakeLeagueClient)
    monkeypatch.setattr("src.main.get_current_week", lambda lg: 7)
    monkeypatch.setattr("src.main.get_weekly_matchup", lambda *a, **kw: fake_matchup)
    monkeypatch.setattr("src.main.generate_weekly_recap", fake_recap)
    monkeypatch.setattr("src.main.evaluate_waivers", fake_waivers)
    monkeypatch.setattr("src.main.fetch_league_free_agents", lambda *a, **kw: [])
    monkeypatch.setattr("src.main.fetch_trending_adds", lambda *a, **kw: [])
    monkeypatch.setattr("src.main.get_injury_report", lambda *a, **kw: [])
    monkeypatch.setattr("src.main.send_digest_email", lambda *a, **kw: True)
    monkeypatch.setattr("src.main.GeminiIntelligenceClient", lambda *a, **kw: None)

    test_client = TestClient(app)
    resp = test_client.post("/run/weekly")
    assert resp.status_code == 200

    # Recap must run for Week 7 with is_past_week=True (FINAL), while Waivers target upcoming Week 8!
    assert all(wk == 7 and past is True for wk, past in recap_calls)
    assert all(wk == 8 for wk in waiver_calls)
