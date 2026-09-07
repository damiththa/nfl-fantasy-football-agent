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
6. TONE & BANTER:
   - Deliver advice with sharp wit, entertaining banter, and veteran swagger.
   - Use vivid analogies and memorable punchlines when explaining start/sit dilemmas, waiver traps, or trade opportunities.
   - Lightly roast opposing managers' questionable moves and funny benchwarmers, while keeping the statistical reasoning and game-theory 100% surgically precise.
7. ABSOLUTE INJURY & INACTIVE PROTOCOL (ZERO TOLERANCE):
   - NEVER, under ANY circumstance, recommend starting any player whose injury status is "OUT", "IR", "PUP", "SUSPENSION", or "DOUBTFUL".
   - All injured, suspended, or inactive players MUST be placed in bench_players with action="BENCH" and explicitly tagged with their injury/inactive status.
   - If a starter has a "QUESTIONABLE" or "GTD" (Game-Time Decision) designation, explicitly highlight the risk and designate a specific bench backup as the contingency pivot if ruled out.
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

MATCHUP & OPPONENT OVERVIEW:
- Projected Point Margin: {projected_margin:+.1f} (Positive = You are favored; Negative = Underdog)
- Game-Theory Mandate: {"PROTECT THE LEAD with high-floor volume starters" if projected_margin > 12 else "SEEK VARIANCE with high-ceiling explosive players to pull an upset" if projected_margin < -12 else "BALANCED floor/ceiling targeting high implied team totals and red-zone volume"}

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

TASK - DELIVER A COMPLETE START 'EM / SIT 'EM MASTER REPORT:
1. START 'EM (recommended_starters): Select the optimal starting lineup adhering to the game theory strategy. Every starter must be active and healthy (or Questionable with explicit warning).
2. SIT 'EM (bench_players): Every single bench player must be accounted for with a specific, concise reason why they are benched (e.g. backup role, brutal matchup against top-5 defense, low Vegas total, or INJURED/OUT).
3. INJURED / INACTIVE PLAYERS: Ensure NO players with OUT, IR, or DOUBTFUL tags are in the starting lineup.
4. START/SIT DILEMMAS (key_flex_decisions): Address the closest 2-3 head-to-head toss-ups (e.g., "Start Player X over Player Y because...").
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
