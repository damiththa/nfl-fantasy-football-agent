from src.intelligence.schemas import (
    CurrentRosterPlayer,
    LineupRecommendation,
    MatchupReport,
    MissedOpportunity,
    RecapPlayerPerformance,
    StartSitDecision,
    TradeEvaluation,
    WaiverRecommendation,
    WaiverReport,
    WeeklyRecapReport,
)


def test_start_sit_decision_schema():
    decision = StartSitDecision(
        player_name="Patrick Mahomes",
        position="QB",
        team="KC",
        action="START",
        confidence=0.92,
        floor=18.5,
        ceiling=32.0,
        projected_points=24.2,
        reasoning="Elite matchup at home against vulnerable secondary.",
        game_script_note="Vegas total: 51.5 (highest of the week)",
        opponent="BAL",
        home_away="HOME",
        matchup_display="vs. BAL",
        game_time="Sun 4:25 PM ET",
    )
    assert decision.player_name == "Patrick Mahomes"
    assert decision.confidence == 0.92
    assert decision.action == "START"
    assert decision.opponent == "BAL"
    assert decision.home_away == "HOME"
    assert decision.matchup_display == "vs. BAL"
    assert decision.game_time == "Sun 4:25 PM ET"

    # Verify JSON serialization round-trip
    json_data = decision.model_dump_json()
    reconstructed = StartSitDecision.model_validate_json(json_data)
    assert reconstructed == decision


def test_current_roster_player_schema():
    player = CurrentRosterPlayer(
        player_name="Amon-Ra St. Brown",
        position="WR",
        team="DET",
        current_slot="WR",
        projected_points=17.8,
        action="KEEP_STARTING",
        action_label="✅ KEEP STARTING",
        action_detail="Confirmed alpha WR1 starter",
        floor=12.5,
        ceiling=26.0,
        opponent="LAR",
        home_away="AWAY",
        matchup_display="@ LAR",
        game_time="Sun 8:20 PM ET",
    )
    assert player.player_name == "Amon-Ra St. Brown"
    assert player.opponent == "LAR"
    assert player.home_away == "AWAY"
    assert player.matchup_display == "@ LAR"
    assert player.game_time == "Sun 8:20 PM ET"

    json_data = player.model_dump_json()
    reconstructed = CurrentRosterPlayer.model_validate_json(json_data)
    assert reconstructed == player


def test_lineup_recommendation_schema():
    rec = LineupRecommendation(
        league_id=991059191,
        week=1,
        game_theory_strategy="PROTECT_LEAD",
        strategy_reasoning="Favored by 16 points; protect the floor.",
        recommended_starters=[
            StartSitDecision(
                player_name="CeeDee Lamb",
                position="WR",
                team="DAL",
                action="START",
                confidence=0.98,
                floor=14.0,
                ceiling=28.0,
                projected_points=19.5,
                reasoning="Alpha target share.",
            )
        ],
        bench_players=[],
        key_flex_decisions=["Started Lamb over flex options"],
    )
    assert rec.league_id == 991059191
    assert rec.game_theory_strategy == "PROTECT_LEAD"
    assert len(rec.recommended_starters) == 1


def test_waiver_report_schema():
    report = WaiverReport(
        league_id=735288,
        week=2,
        targets=[
            WaiverRecommendation(
                player_name="Jordan Mason",
                position="RB",
                team="SF",
                priority="MUST_ADD",
                recommended_drop="Backup TE",
                reasoning="Clear bellcow starter with lead back injured.",
                upside_summary="RB1 overall upside in Shanahan offense.",
            )
        ],
        roster_drop_candidates=["Backup TE", "3rd QB"],
        overall_waiver_strategy="Aggressive claims on scarce running backs.",
    )
    assert report.targets[0].priority == "MUST_ADD"
    assert len(report.roster_drop_candidates) == 2


def test_trade_evaluation_schema():
    eval_result = TradeEvaluation(
        verdict="ACCEPT",
        your_vorp_change=4.2,
        starting_lineup_impact="Adds weekly RB1 starter to replace weak flex.",
        playoff_schedule_impact="Soft Weeks 15-17 opposing rush defenses.",
        reasoning="Massive starting lineup upgrade that out-weighs bench depth loss.",
        counter_suggestion=None,
    )
    assert eval_result.verdict == "ACCEPT"
    assert eval_result.your_vorp_change == 4.2


def test_matchup_report_schema():
    report = MatchupReport(
        league_id=991059191,
        week=3,
        opponent_name="Rival Team",
        projected_score_user=124.5,
        projected_score_opponent=111.0,
        projected_margin=13.5,
        win_probability=0.76,
        key_advantages=["WR1 vs CB2 mismatch", "Higher QB ceiling"],
        key_vulnerabilities=["Opponent TE1 volume"],
        weather_and_vegas_factors=["High Vegas implied team total (28.0)"],
        strategic_summary="Solid favorite; rely on high-volume starters.",
    )
    assert report.win_probability == 0.76
    assert report.projected_margin == 13.5


def test_weekly_recap_report_schema():
    recap = WeeklyRecapReport(
        league_id=991059191,
        league_name="PNA 2026",
        week=1,
        matchup_status="FINAL",
        result="WIN",
        user_team_name="Mad Dawg",
        user_score=118.4,
        user_projected=112.0,
        opponent_team_name="Rival",
        opponent_score=98.2,
        opponent_projected=105.0,
        score_margin=20.2,
        optimal_lineup_points=128.5,
        points_left_on_bench=10.1,
        coach_game_summary="Decisive victory behind Purdy and Nacua.",
        game_balls=[
            RecapPlayerPerformance(
                player_name="Brock Purdy",
                position="QB",
                team="SF",
                slot="QB",
                actual_points=21.1,
                projected_points=15.5,
                point_differential=5.6,
                verdict_comment="Elite pocket command and rushing TD.",
            )
        ],
        missed_opportunities=[
            MissedOpportunity(
                bench_player="Khalil Shakir",
                bench_points=14.5,
                started_player="Courtland Sutton",
                starter_points=8.2,
                points_differential=6.3,
                lesson="Target slot efficiency over outside contested targets.",
            )
        ],
        busts=[
            RecapPlayerPerformance(
                player_name="Courtland Sutton",
                position="WR",
                team="DEN",
                slot="WR",
                actual_points=8.2,
                projected_points=11.7,
                point_differential=-3.5,
                verdict_comment="Quarterback inaccuracy limited targets.",
            )
        ],
        lessons_learned=["High volume beats low-target upside"],
        next_week_priorities=["Target RB handcuff on waivers"],
    )
    assert recap.result == "WIN"
    assert recap.score_margin == 20.2
    assert recap.points_left_on_bench == 10.1
    assert len(recap.game_balls) == 1
    assert len(recap.missed_opportunities) == 1

