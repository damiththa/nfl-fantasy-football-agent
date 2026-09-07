"""
Prompt templates and system instructions for Gemini Pro intelligence.
Incorporates precise league rules, owner context, and game-theory directives.
"""

from typing import Any

from src.config import LeagueConfig

SYSTEM_PROMPT = """You are a senior fantasy football analyst with 20+ years of high-stakes championship experience.
You manage two fantasy football leagues on ESPN for the same owner ("Mad Dawg").

YOUR PRIME DIRECTIVE:
Deliver analytically rigorous, objective, and game-theory optimized fantasy advice to win the championship.

ABSOLUTE OPERATING RULES:
1. TRUTH IN DATA: Never fabricate stats, player injuries, line movements, or weather. Base all analysis strictly on provided facts.
2. EXPLAIN THE "WHY": Do not just state who to start/sit or add/drop; detail the underlying usage indicators (snap %, target share, red zone touches, Vegas implied totals, weather).
3. GAME THEORY AWARENESS:
   - HEAVY FAVORITE (Margin > +12): Protect the lead. Prioritize high-floor, reliable-touch players to minimize downside variance.
   - HEAVY UNDERDOG (Margin < -12): Need a miracle. Maximize variance and ceiling by starting high-aDOT receivers, explosive backs, or correlation stacks.
   - CLOSE CONTEST (Margin +/- 10): Balance floor and ceiling, prioritizing favorable game scripts and red zone opportunities.
4. LEAGUE CONTEXT RULES:
   - PNA 2026 League (ID: 991059191): 12-Team, Full PPR, 4pt Passing TD, 2 FLEX, NO KICKER. Bench depth and high-volume pass catchers are paramount.
   - Chips Ahoy (ID: 735288): 10-Team, Full PPR, 6pt PASSING TD, 1 FLEX, HAS KICKER. High-yardage/high-TD quarterbacks are significantly more valuable than standard leagues.
5. WEATHER FACTORS:
   - Sustained wind > 20 mph or gusts > 30 mph: Heavily downgrade deep passing attacks and kickers; upgrade ground-game touches.
   - Freezing cold / precipitation: Expect elevated fumble risks and run-heavy game scripts.
"""


def format_lineup_prompt(
    league: LeagueConfig,
    week: int,
    your_roster: dict[str, Any],
    opponent_roster: dict[str, Any] | None,
    projected_margin: float,
    vegas_odds: list[dict[str, Any]],
    weather_reports: list[dict[str, Any]],
    injuries: list[dict[str, Any]],
) -> str:
    """Format prompt for starting lineup and start/sit decisions."""
    return f"""Analyze Week {week} lineup decisions for league '{league.name}'.

LEAGUE SCORING & SETUP:
- Format: {league.num_teams}-team Full PPR (1.0 pt/rec)
- Passing TD: {league.scoring.pass_td} pts
- Starting Slots: 1 QB, 2 RB, 2 WR, 1 TE, {league.roster.flex} FLEX, 1 DST{", 1 K" if league.roster.k else ", NO KICKER"}

MATCHUP OVERVIEW:
- Projected Point Margin: {projected_margin:+.1f} (Positive = You are favored; Negative = Underdog)

YOUR CURRENT ROSTER:
{your_roster}

OPPONENT LINEUP:
{opponent_roster or "Opponent lineup not yet set or unavailable"}

VEGAS ODDS & IMPLIED TOTALS FOR RELEVANT TEAMS:
{vegas_odds}

GAME-DAY WEATHER FOR RELEVANT OUTDOOR STADIUMS:
{weather_reports}

PLAYER INJURY & PRACTICE STATUSES:
{injuries}

TASK:
Recommend the optimal starting lineup adhering to game theory for this specific matchup margin.
Explicitly resolve every start/sit dilemma, especially the flex spots.
"""


def format_waiver_prompt(
    league: LeagueConfig,
    week: int,
    your_roster: dict[str, Any],
    available_players: list[dict[str, Any]],
    trending_adds: list[dict[str, Any]],
    injuries: list[dict[str, Any]],
) -> str:
    """Format prompt for waiver wire recommendations."""
    return f"""Evaluate waiver wire opportunities for Week {week} in league '{league.name}'.

LEAGUE SETUP:
- {league.num_teams}-team, Full PPR, {league.roster.flex} FLEX, {league.scoring.pass_td}pt Pass TD
- Waiver Type: Rolling Waiver Priority (Non-FAAB)

YOUR ROSTER:
{your_roster}

AVAILABLE FREE AGENTS (High Owned/Projected):
{available_players}

SLEEPER LEAGUE-WIDE 24H TRENDING ADDS:
{trending_adds}

CURRENT INJURY ENVIRONMENT:
{injuries}

TASK:
Identify priority waiver claims. Suggest specific players on your roster who should be dropped (e.g. low snap counts, loss of role, buried on depth chart).
"""


def format_trade_prompt(
    league: LeagueConfig,
    your_roster: dict[str, Any],
    giving_players: list[str],
    receiving_players: list[str],
    opponent_roster: dict[str, Any] | None = None,
) -> str:
    """Format prompt for evaluating a prospective trade."""
    return f"""Evaluate a proposed trade for league '{league.name}'.

LEAGUE CONTEXT:
- Format: {league.num_teams}-team, Full PPR
- Passing TD: {league.scoring.pass_td} pts
- FLEX Slots: {league.roster.flex}

PLAYERS YOU WOULD GIVE UP:
{giving_players}

PLAYERS YOU WOULD RECEIVE:
{receiving_players}

YOUR CURRENT ROSTER:
{your_roster}

OPPONENT ROSTER (If known):
{opponent_roster or "Not provided"}

TASK:
Evaluate this trade strictly on the NET CHANGE to your STARTING LINEUP value and ROS (Rest-of-Season) / Playoff impact (Weeks 15-17).
Provide a clear verdict: ACCEPT, REJECT, or COUNTER.
"""


def format_matchup_prompt(
    league: LeagueConfig,
    week: int,
    your_roster: dict[str, Any],
    opponent_roster: dict[str, Any],
    user_projected: float,
    opp_projected: float,
    vegas_odds: list[dict[str, Any]],
    weather_reports: list[dict[str, Any]],
) -> str:
    """Format prompt for comprehensive weekly scouting report."""
    margin = user_projected - opp_projected
    return f"""Generate a comprehensive Week {week} Matchup Preview for league '{league.name}'.

PROJECTED SCORE:
- Mad Dawg (You): {user_projected:.1f}
- Opponent: {opp_projected:.1f}
- Margin: {margin:+.1f}

YOUR ROSTER:
{your_roster}

OPPONENT ROSTER:
{opponent_roster}

VEGAS ODDS:
{vegas_odds}

WEATHER CONDITIONS:
{weather_reports}

TASK:
Provide an expert scouting report detailing positional advantages, vulnerabilities, critical game scripts, and the strategic path to victory.
"""
