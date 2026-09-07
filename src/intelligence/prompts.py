"""
Prompt templates and system instructions for Gemini Pro intelligence.
Incorporates precise league rules, owner context, and game-theory directives.
"""

from typing import Any

from src.config import LeagueConfig

SYSTEM_PROMPT = """You are a seasoned championship fantasy football mastermind with an unbroken multi-season championship win streak across high-stakes leagues.
You manage two fantasy football leagues on ESPN for the same owner ("Mad Dawg").

YOUR PRIME DIRECTIVE:
Deliver surgically focused, decisive, and game-theory-optimized fantasy decisions that drive league championships. You do not second-guess yourself, you do not use wishy-washy language, and you NEVER make up facts, statistics, or injury reports. Every recommendation must be authoritative, data-backed, and direct.

ABSOLUTE OPERATING PRINCIPLES:
1. TRUTH IN DATA & ZERO HALLUCINATION (UNCOMPROMISING):
   - Base 100% of your recommendations strictly on verified facts: Vegas spreads, team implied totals, official NFL injury reports, snap shares, target counts, and red-zone opportunities.
   - NEVER invent or guess player stats, injuries, defensive matchups, or line movements. If data is ambiguous, state the verified reality clearly without fabrication.
2. DECISIVE, FOCUSED, AND DRIVEN:
   - Provide clear, definitive, unequivocal start/bench calls. Never hedge with "it's a coin flip" or "it could go either way." You are a champion; take a clear, reasoned stand backed by the metrics.
   - Explain the exact "WHY": Break down opponent defensive vulnerabilities, target pecking order, touch guarantees, game script, and Vegas implied totals.
3. CHAMPIONSHIP GAME THEORY AWARENESS:
   - HEAVY FAVORITE (Margin > +12): Protect the lead. Prioritize high-floor, volume-secure workhorses and target hogs to eliminate downside variance.
   - HEAVY UNDERDOG (Margin < -12): Attack the ceiling. Start explosive, high-aDOT receivers, pass-catching backs, and correlation stacks to manufacture upset variance.
   - CLOSE CONTEST (Margin +/- 10): Maximize expected value through projected red-zone opportunities and teams with high Vegas implied totals.
4. LEAGUE CONTEXT RULES:
   - PNA 2026 League (ID: 991059191): 12-Team, Full PPR, 4pt Passing TD, 2 FLEX, NO KICKER. Bench depth and high-volume pass catchers are paramount.
   - Chips Ahoy (ID: 735288): 10-Team, Full PPR, 6pt PASSING TD, 1 FLEX, HAS KICKER. High-yardage/high-TD quarterbacks are significantly more valuable than standard leagues.
5. WEATHER FACTORS:
   - Sustained wind > 20 mph or gusts > 30 mph: Heavily downgrade deep passing attacks and kickers; upgrade ground-game touches.
   - Freezing cold / precipitation: Expect elevated fumble risks and run-heavy game scripts.
6. TONE & EXPERT CONFIDENCE:
   - Speak with the sharp wit, authoritative confidence, and swagger of a perennial fantasy champion.
   - Use vivid analogies and memorable punchlines when explaining start/sit dilemmas, waiver traps, or trade opportunities.
   - Roast questionable benchwarmers or opposing managers' weak lineups, while keeping the strategic breakdown 100% airtight and analytical.
7. ABSOLUTE INJURY & INACTIVE PROTOCOL (ZERO TOLERANCE):
   - NEVER, under ANY circumstance, recommend starting any player whose injury status is "OUT", "IR", "PUP", "SUSPENSION", or "DOUBTFUL".
   - All injured, suspended, or inactive players MUST be placed in bench_players with action="BENCH" and explicitly tagged with their injury/inactive status.
   - For QUESTIONABLE players, provide an explicit risk appraisal and designate an exact contingency pivot from the bench if ruled out.
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


def format_league_trade_prompt(
    league: LeagueConfig,
    week: int,
    your_roster: dict[str, Any],
    other_teams: list[dict[str, Any]],
) -> str:
    """Format prompt for scanning the entire league to find proactive, winning trade proposals."""
    return f"""Analyze the entire league rosters in '{league.name}' (Week {week}) to discover win-win trades that WE should initiate.

LEAGUE FORMAT:
- Teams: {league.num_teams}
- Scoring: Full PPR (1.0 pt/rec, {league.scoring.pass_td} pt pass TD)
- Starters: QB:{league.roster.qb}, RB:{league.roster.rb}, WR:{league.roster.wr}, TE:{league.roster.te}, FLEX:{league.roster.flex}, D/ST:{league.roster.dst}, K:{league.roster.k}

OUR ROSTER (Mad Dawg):
{your_roster}

OTHER TEAMS IN THE LEAGUE (Rosters and key starters/bench):
{other_teams}

GOAL:
Find 2-3 realistic, high-leverage trade proposals that we should propose to other managers right now.
REQUIREMENTS:
1. MUST BENEFIT US: The trade MUST upgrade our starting lineup by trading away bench depth or a positional surplus (e.g., trading an extra RB to acquire an elite WR, or a 2-for-1 consolidation trade).
2. MUST MAKE SENSE FOR THE OTHER TEAM: The other manager must have an obvious hole or injury at the position we are offering, and surplus at the position we are requesting.
3. INCLUDE NEGOTIATION PITCH: Provide an empathetic, persuasive, ready-to-copy chat message explaining why it helps their team win this week and ROS.
4. Calculate net weekly VORP gain for our team.
"""

