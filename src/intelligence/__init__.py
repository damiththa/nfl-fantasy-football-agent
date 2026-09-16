"""
Intelligence layer powered by Gemini Pro (gemini-2.5-pro).
Provides structured output schemas, prompts, and client wrappers.
"""

from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import (
    SYSTEM_PROMPT,
    format_lineup_prompt,
    format_matchup_prompt,
    format_trade_prompt,
    format_waiver_prompt,
)
from src.intelligence.schemas import (
    LineupRecommendation,
    MatchupReport,
    StartSitDecision,
    TradeEvaluation,
    WaiverRecommendation,
    WaiverReport,
)

__all__ = [
    "GeminiIntelligenceClient",
    "SYSTEM_PROMPT",
    "format_lineup_prompt",
    "format_waiver_prompt",
    "format_trade_prompt",
    "format_matchup_prompt",
    "StartSitDecision",
    "LineupRecommendation",
    "WaiverRecommendation",
    "WaiverReport",
    "TradeEvaluation",
    "MatchupReport",
]
