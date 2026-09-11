"""
Core fantasy football analytics modules.
Provides scoring calculations, start/sit optimization, waiver ranking,
trade evaluation, and weekly matchup previews.
"""

from src.analysis.lineup import optimize_lineup
from src.analysis.matchup_preview import generate_matchup_preview
from src.analysis.recap import generate_weekly_recap
from src.analysis.scoring import (
    calculate_player_score,
    compare_scoring,
    project_player_score,
)
from src.analysis.trades import calculate_vorp, evaluate_trade
from src.analysis.waivers import evaluate_waivers

__all__ = [
    "calculate_player_score",
    "compare_scoring",
    "project_player_score",
    "optimize_lineup",
    "evaluate_waivers",
    "calculate_vorp",
    "evaluate_trade",
    "generate_matchup_preview",
    "generate_weekly_recap",
]
