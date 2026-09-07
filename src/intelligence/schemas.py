"""
Pydantic schemas for Gemini structured outputs in the NFL Fantasy Football Agent.
These schemas guarantee type-safe, machine-parseable analysis from the LLM.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class StartSitDecision(BaseModel):
    """Start or sit recommendation for an individual player."""

    player_name: str = Field(description="Full name of the player")
    position: str = Field(description="Player primary position (QB, RB, WR, TE, DST, K)")
    team: str = Field(description="NFL team abbreviation (e.g., KC, SF)")
    action: Literal["START", "BENCH"] = Field(description="Recommended action: START or BENCH")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score in the recommendation from 0.0 to 1.0"
    )
    floor: float = Field(description="Conservative fantasy point projection under poor game script")
    ceiling: float = Field(
        description="Optimistic fantasy point projection under favorable game script"
    )
    projected_points: float = Field(description="Base projected fantasy points")
    reasoning: str = Field(description="Concise analytical rationale for the start/sit decision")
    game_script_note: Optional[str] = Field(
        default=None, description="Note on Vegas total, weather, or matchup context"
    )
    current_slot: str = Field(
        default="Bench",
        description="Where the player is currently placed on ESPN (e.g., QB, RB, WR, Bench, IR)",
    )
    alignment: str = Field(
        default="ALIGNED",
        description="Alignment with ESPN: ALIGNED, SWAP_TO_START, MOVE_TO_BENCH",
    )


class LineupRecommendation(BaseModel):
    """Complete starting lineup recommendation tailored to matchup game theory."""

    league_id: int = Field(description="ESPN League ID")
    week: int = Field(description="NFL Week number")
    game_theory_strategy: Literal["PROTECT_LEAD", "SEEK_VARIANCE", "BALANCED"] = Field(
        description="Lineup approach: PROTECT_LEAD (high-floor), SEEK_VARIANCE (high-ceiling underdog), or BALANCED"
    )
    strategy_reasoning: str = Field(description="Why this game-theory approach was selected")
    recommended_starters: list[StartSitDecision] = Field(
        description="Recommended starting lineup players"
    )
    bench_players: list[StartSitDecision] = Field(description="Recommended bench players")
    key_flex_decisions: list[str] = Field(
        default_factory=list, description="Key toss-ups and flex selection explanations"
    )
    vacant_slots: list[str] = Field(
        default_factory=list,
        description="Starting slots that are currently empty on ESPN (e.g. TE, K, D/ST)",
    )
    suboptimal_starters: list[str] = Field(
        default_factory=list,
        description="Players currently starting on ESPN who are recommended to be benched",
    )
    actionable_swaps: list[str] = Field(
        default_factory=list,
        description="Explicit player swap instructions to make in ESPN app",
    )


class WaiverRecommendation(BaseModel):
    """Waiver pickup recommendation."""

    player_name: str = Field(description="Player to target on waivers")
    position: str = Field(description="Player position")
    team: str = Field(description="NFL team abbreviation")
    priority: Literal["MUST_ADD", "HIGH", "MEDIUM", "SPECULATIVE"] = Field(
        description="Waiver priority tier"
    )
    recommended_drop: Optional[str] = Field(
        default=None, description="Player on your roster to drop to make room"
    )
    reasoning: str = Field(description="Why this player should be claimed")
    upside_summary: str = Field(
        description="Rest-of-season role potential, injury fill-in, or trend breakout"
    )


class WaiverReport(BaseModel):
    """Full weekly waiver analysis."""

    league_id: int = Field(description="ESPN League ID")
    week: int = Field(description="Current week")
    targets: list[WaiverRecommendation] = Field(description="Ranked waiver targets")
    roster_drop_candidates: list[str] = Field(
        default_factory=list,
        description="Players on roster whose opportunity/snap share is declining",
    )
    overall_waiver_strategy: str = Field(
        description="Strategic overview for this week's waiver cycle"
    )


class TradeEvaluation(BaseModel):
    """Analysis and verdict on a proposed trade."""

    verdict: Literal["ACCEPT", "REJECT", "COUNTER"] = Field(description="Verdict on the trade")
    your_vorp_change: float = Field(
        description="Estimated net change in Value Over Replacement Player for your roster"
    )
    starting_lineup_impact: str = Field(
        description="How the trade specifically alters your weekly starting lineup quality"
    )
    playoff_schedule_impact: str = Field(
        description="Evaluation of Weeks 15-17 schedule for acquired vs traded players"
    )
    reasoning: str = Field(description="Complete analytical justification for the verdict")
    counter_suggestion: Optional[str] = Field(
        default=None,
        description="Suggested counter-offer if trade has promise but is currently unbalanced",
    )


class MatchupReport(BaseModel):
    """Weekly matchup preview and scouting report."""

    league_id: int = Field(description="ESPN League ID")
    week: int = Field(description="Matchup week")
    opponent_name: str = Field(description="Opponent team name")
    projected_score_user: float = Field(description="User team projected points")
    projected_score_opponent: float = Field(description="Opponent team projected points")
    projected_margin: float = Field(
        description="Point differential (positive if user is favored, negative if underdog)"
    )
    win_probability: float = Field(
        ge=0.0, le=1.0, description="Estimated probability of victory (0.0 to 1.0)"
    )
    key_advantages: list[str] = Field(description="Positional or matchup advantages for user")
    key_vulnerabilities: list[str] = Field(
        description="Areas where opponent has positional or floor/ceiling edge"
    )
    weather_and_vegas_factors: list[str] = Field(
        description="Game-environment notes: outdoor rain/wind, high implied game totals"
    )
    strategic_summary: str = Field(
        description="High-level scouting report and primary path to victory"
    )


class TradeProposal(BaseModel):
    """Actionable trade proposal that user should initiate."""

    target_team_id: int = Field(description="ESPN Team ID of the opposing team to trade with")
    target_team_name: str = Field(description="Name of opposing team")
    target_manager: str = Field(description="Owner/manager name of opposing team")
    giving_players: list[str] = Field(description="Players you send from your surplus/depth")
    receiving_players: list[str] = Field(description="Players you acquire to upgrade starting lineup")
    net_vorp_gain: float = Field(description="Estimated net weekly VORP improvement for your team")
    your_lineup_upgrade: str = Field(description="How this specifically improves your starting lineup")
    why_target_accepts: str = Field(description="Why this trade solves a key deficiency for the opponent")
    negotiation_pitch: str = Field(description="Ready-to-send message to pitch this trade in fantasy chat")


class LeagueTradeReport(BaseModel):
    """Collection of proactive trade proposals across the entire league."""

    league_id: int = Field(description="ESPN League ID")
    week: int = Field(description="Current NFL week")
    proposals: list[TradeProposal] = Field(
        default_factory=list,
        description="Top recommended win-win trade proposals to initiate",
    )
    market_overview: str = Field(
        description="Strategic analysis of your team's positional surpluses and market trade targets"
    )
