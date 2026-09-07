from __future__ import annotations

from src.config import LeagueConfig, ScoringRules, StatId

def calculate_player_score(stats: dict[str | int, float], scoring: ScoringRules) -> float:
    """Calculate fantasy points for a given stat line using the provided scoring rules."""
    score = 0.0
    
    # Normalize keys to integers to match StatId enum values
    stats_int = {int(k): v for k, v in stats.items()}
    
    # Passing
    score += stats_int.get(StatId.PASS_YDS, 0) * scoring.pass_yds
    score += stats_int.get(StatId.PASS_TD, 0) * scoring.pass_td
    score += stats_int.get(StatId.PASS_INT, 0) * scoring.pass_int
    
    # Rushing
    score += stats_int.get(StatId.RUSH_YDS, 0) * scoring.rush_yds
    score += stats_int.get(StatId.RUSH_TD, 0) * scoring.rush_td
    
    # Receiving
    score += stats_int.get(StatId.REC, 0) * scoring.reception
    score += stats_int.get(StatId.REC_YDS, 0) * scoring.rec_yds
    score += stats_int.get(StatId.REC_TD, 0) * scoring.rec_td
    
    # Misc
    score += stats_int.get(StatId.FUMBLES_LOST, 0) * scoring.fumble_lost
    score += stats_int.get(StatId.TWO_PT_CONV, 0) * scoring.two_pt_conv
    
    # Kicking
    score += stats_int.get(StatId.FG_0_39, 0) * 3.0
    score += stats_int.get(StatId.FG_40_49, 0) * 4.0
    score += stats_int.get(StatId.FG_50_PLUS, 0) * 5.0
    score += stats_int.get(StatId.FG_MISSED, 0) * -1.0
    score += stats_int.get(StatId.XP_MADE, 0) * 1.0
    score += stats_int.get(StatId.XP_MISSED, 0) * -1.0
    
    # DST
    score += stats_int.get(StatId.DST_TD, 0) * 6.0
    score += stats_int.get(StatId.DST_SAFETY, 0) * 2.0
    score += stats_int.get(StatId.DST_SACK, 0) * 1.0
    score += stats_int.get(StatId.DST_INT, 0) * 2.0
    score += stats_int.get(StatId.DST_FUMBLE_REC, 0) * 2.0
    score += stats_int.get(StatId.DST_BLOCKED_KICK, 0) * 2.0
    
    return round(score, 2)


def compare_scoring(stats: dict[str | int, float], league1: LeagueConfig, league2: LeagueConfig) -> dict[str, float]:
    """Compare how the same player stats score differently across both leagues."""
    score1 = calculate_player_score(stats, league1.scoring)
    score2 = calculate_player_score(stats, league2.scoring)
    
    return {
        league1.short_name: score1,
        league2.short_name: score2,
        "diff": round(abs(score1 - score2), 2)
    }


def project_player_score(player, scoring: ScoringRules) -> tuple[float, float, float]:
    """Return (projected, floor, ceiling) based on projected stats and historical variance."""
    stats = getattr(player, "projected_stats", {})
    if not stats:
        stats = getattr(player, "stats", {})

    proj = calculate_player_score(stats, scoring)
    return proj, round(proj * 0.8, 2), round(proj * 1.2, 2)
