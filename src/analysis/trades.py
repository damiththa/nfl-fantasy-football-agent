"""
Trade Value and Roster-Contextual Evaluation Engine.
Evaluates proposed trades based on true net impact to the user's weekly starting lineup,
roster balance, positional depth, and ROS/playoff strength.
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import datetime
from typing import Any, Optional

from src.config import LeagueConfig
from src.espn.roster import ParsedRoster, RosterPlayer
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.intelligence.prompts import format_trade_prompt
from src.intelligence.schemas import TradeEvaluation

logger = logging.getLogger(__name__)

# Positional replacement baselines (approximate weekly points of top free agent)
REPLACEMENT_BASELINES_12_TEAM = {
    "QB": 15.0,
    "RB": 8.5,
    "WR": 9.5,
    "TE": 6.5,
    "DST": 5.0,
    "K": 6.0,
}

REPLACEMENT_BASELINES_10_TEAM = {
    "QB": 16.5,
    "RB": 9.5,
    "WR": 10.5,
    "TE": 7.0,
    "DST": 5.5,
    "K": 6.5,
}


def normalize_player_name(name: str) -> str:
    """Normalize a player name for robust comparison: lowercase, strip punctuation and suffixes."""
    n = name.lower().strip()
    # Strip common suffixes
    for suffix in [" jr.", " jr", " sr.", " sr", " iii", " ii", " iv", " v"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    # Strip punctuation like apostrophes and periods
    return "".join(c for c in n if c.isalnum() or c.isspace()).strip()


def find_roster_player(name: str, roster_players: list[RosterPlayer]) -> Optional[RosterPlayer]:
    """Find a player in a list of RosterPlayer objects by exact, normalized, or partial match."""
    norm_target = normalize_player_name(name)
    if not norm_target:
        return None

    # 1. Exact match
    for p in roster_players:
        if p.name.lower() == name.lower():
            return p

    # 2. Normalized match
    for p in roster_players:
        if normalize_player_name(p.name) == norm_target:
            return p

    # 3. Substring match (e.g. 'Purdy' for 'Brock Purdy')
    candidates = [p for p in roster_players if norm_target in normalize_player_name(p.name)]
    if len(candidates) == 1:
        return candidates[0]

    return None


def validate_trade_roster_ownership(
    roster: ParsedRoster,
    giving_players: list[str],
    receiving_players: list[str],
) -> tuple[bool, list[str], list[RosterPlayer]]:
    """Validate that outgoing players are on the user's roster and incoming players are not already owned.

    Returns:
        (is_valid, list_of_error_strings, list_of_matched_giving_roster_players)
    """
    errors: list[str] = []
    matched_giving: list[RosterPlayer] = []

    # Check giving players (MUST own them)
    for name in giving_players:
        player = find_roster_player(name, roster.players)
        if not player:
            owned_names = ", ".join(f"{p.name} ({p.position})" for p in roster.players[:8])
            errors.append(
                f"You do not own '{name}' on your roster. You cannot trade away a player you do not have. "
                f"(Your current roster includes: {owned_names}...)"
            )
        else:
            matched_giving.append(player)

    # Check receiving players (MUST NOT already own them)
    for name in receiving_players:
        player = find_roster_player(name, roster.players)
        if player:
            errors.append(
                f"You already own '{player.name}' ({player.position} - {player.team}) on your roster. "
                "You cannot trade for a player you currently own."
            )

    return (len(errors) == 0, errors, matched_giving)


def compute_optimal_starters(
    players: list[RosterPlayer], league: LeagueConfig
) -> tuple[list[RosterPlayer], list[RosterPlayer], float]:
    """Deterministically calculate optimal starters and total starting points for a set of players."""
    # Filter out active vs injured players (injuries sorted to bottom)
    def player_sort_key(p: RosterPlayer) -> tuple[int, float]:
        is_injured = (getattr(p, "injury_status", "") or "").upper() in ("OUT", "IR")
        pts = getattr(p, "projected_points", 0.0) or 0.0
        return (0 if not is_injured else 1, -pts)

    sorted_players = sorted(players, key=player_sort_key)

    slots_needed = {
        "QB": league.roster.qb,
        "RB": league.roster.rb,
        "WR": league.roster.wr,
        "TE": league.roster.te,
        "DST": league.roster.dst,
        "K": league.roster.k,
        "FLEX": league.roster.flex,
    }
    slots_filled = {k: 0 for k in slots_needed}
    flex_eligible = ("RB", "WR", "TE")

    starters: list[RosterPlayer] = []
    bench: list[RosterPlayer] = []

    for p in sorted_players:
        pos = (p.position or "").upper().replace("/", "")
        if pos in ("DEF", "DST"):
            pos = "DST"

        is_starter = False
        if pos in slots_filled and slots_filled[pos] < slots_needed.get(pos, 0):
            slots_filled[pos] += 1
            is_starter = True
        elif pos in flex_eligible and slots_filled["FLEX"] < slots_needed.get("FLEX", 0):
            slots_filled["FLEX"] += 1
            is_starter = True

        if is_starter:
            starters.append(p)
        else:
            bench.append(p)

    total_pts = round(sum(getattr(p, "projected_points", 0.0) or 0.0 for p in starters), 2)
    return starters, bench, total_pts


def simulate_trade_roster_impact(
    roster: ParsedRoster,
    league: LeagueConfig,
    matched_giving: list[RosterPlayer],
    incoming_players: list[RosterPlayer],
) -> dict[str, Any]:
    """Simulate pre-trade vs post-trade starting lineups to evaluate true net starting lineup impact."""
    pre_starters, pre_bench, pre_pts = compute_optimal_starters(roster.players, league)

    # Simulated post-trade roster
    giving_names = {normalize_player_name(p.name) for p in matched_giving}
    remaining_players = [
        p for p in roster.players if normalize_player_name(p.name) not in giving_names
    ]
    post_roster_players = remaining_players + incoming_players

    post_starters, post_bench, post_pts = compute_optimal_starters(post_roster_players, league)
    net_starting_points = round(post_pts - pre_pts, 2)

    # Compare pre and post starting rosters
    pre_starter_names = {normalize_player_name(p.name) for p in pre_starters}
    post_starter_names = {normalize_player_name(p.name) for p in post_starters}

    new_starters = [p for p in post_starters if normalize_player_name(p.name) not in pre_starter_names]
    departed_starters = [
        p for p in pre_starters if normalize_player_name(p.name) not in post_starter_names
    ]

    lineup_changes: list[str] = []
    for ns in new_starters:
        is_incoming = any(
            normalize_player_name(ns.name) == normalize_player_name(inc.name)
            for inc in incoming_players
        )
        tag = "Acquired Starter" if is_incoming else "Promoted from Bench"
        lineup_changes.append(
            f"⬆️ {tag}: {ns.name} ({ns.position}, {getattr(ns, 'projected_points', 0.0):.1f} pts) enters starting lineup"
        )

    for ds in departed_starters:
        is_traded = any(
            normalize_player_name(ds.name) == normalize_player_name(g.name)
            for g in matched_giving
        )
        tag = "Traded Away" if is_traded else "Demoted to Bench"
        lineup_changes.append(
            f"⬇️ {tag}: {ds.name} ({ds.position}, {getattr(ds, 'projected_points', 0.0):.1f} pts) exits starting lineup"
        )

    # Check if incoming player did NOT crack the starting lineup
    for inc in incoming_players:
        if normalize_player_name(inc.name) not in post_starter_names:
            lineup_changes.append(
                f"⚠️ Bench Stash: Acquired player {inc.name} ({inc.position}, {getattr(inc, 'projected_points', 0.0):.1f} pts) "
                f"does NOT crack your starting lineup (sits on bench behind current starters)."
            )

    # Positional depth calculation
    pre_counts: dict[str, int] = {}
    for p in roster.players:
        pos = (p.position or "FLEX").upper()
        pre_counts[pos] = pre_counts.get(pos, 0) + 1

    post_counts: dict[str, int] = {}
    for p in post_roster_players:
        pos = (p.position or "FLEX").upper()
        post_counts[pos] = post_counts.get(pos, 0) + 1

    depth_details: list[str] = []
    for pos in ["RB", "WR", "TE", "QB"]:
        pre_c = pre_counts.get(pos, 0)
        post_c = post_counts.get(pos, 0)
        if pre_c != post_c:
            diff = f"{pre_c} → {post_c}"
            if post_c <= 1 and pos in ("RB", "WR"):
                diff += f" 🚨 CRITICAL DEPTH WARNING: Leaves you with only {post_c} rostered {pos}!"
            depth_details.append(f"{pos}: {diff}")

    depth_str = "; ".join(depth_details) if depth_details else "Positional balance preserved."

    return {
        "pre_starting_points": pre_pts,
        "post_starting_points": post_pts,
        "net_starting_points_change": net_starting_points,
        "starting_lineup_changes": lineup_changes,
        "positional_depth_impact": depth_str,
        "pre_starters": pre_starters,
        "post_starters": post_starters,
    }


def calculate_vorp(projected_ppg: float, position: str, num_teams: int = 12) -> float:
    """Calculate weekly Value Over Replacement Player (VORP)."""
    baselines = REPLACEMENT_BASELINES_10_TEAM if num_teams <= 10 else REPLACEMENT_BASELINES_12_TEAM
    baseline = baselines.get(position.upper(), 8.0)
    return round(projected_ppg - baseline, 2)


def _build_incoming_roster_players(
    receiving_players: list[str],
    player_projections: Optional[dict[str, tuple[str, float]]] = None,
    espn_league: Optional[Any] = None,
    num_teams: int = 12,
) -> list[RosterPlayer]:
    """Resolve incoming player objects with realistic position, team, and projected points."""
    proj_map = player_projections or {}
    incoming: list[RosterPlayer] = []

    # Search league teams if available
    league_pool: dict[str, tuple[str, str, float]] = {}  # name_norm -> (pos, proTeam, pts)
    if espn_league and hasattr(espn_league, "teams"):
        for team in espn_league.teams:
            for p in getattr(team, "roster", []):
                p_name = getattr(p, "name", "")
                p_norm = normalize_player_name(p_name)
                league_pool[p_norm] = (
                    getattr(p, "position", "FLEX") or "FLEX",
                    getattr(p, "proTeam", "FA") or "FA",
                    float(getattr(p, "projected_points", 0.0) or 0.0),
                )

    for name in receiving_players:
        norm_name = normalize_player_name(name)
        pos = "WR"
        pro_team = "NFL"
        pts = 10.0

        if norm_name in league_pool:
            pos, pro_team, pts = league_pool[norm_name]
        elif name in proj_map:
            pos, pts = proj_map[name]
        elif norm_name in {normalize_player_name(k): v for k, v in proj_map.items()}:
            norm_dict = {normalize_player_name(k): v for k, v in proj_map.items()}
            pos, pts = norm_dict[norm_name]
        else:
            baselines = (
                REPLACEMENT_BASELINES_10_TEAM if num_teams <= 10 else REPLACEMENT_BASELINES_12_TEAM
            )
            pts = baselines.get(pos, 10.0) + 2.0

        incoming.append(
            RosterPlayer(
                name=name,
                position=pos,
                team=pro_team,
                slot="Bench",
                projected_points=pts,
                actual_points=0.0,
                injury_status="ACTIVE",
                bye_week=0,
                percent_owned=90.0,
            )
        )

    return incoming


def evaluate_trade(
    league: LeagueConfig,
    roster: ParsedRoster,
    giving_players: list[str],
    receiving_players: list[str],
    player_projections: Optional[dict[str, tuple[str, float]]] = None,
    opponent_roster: Optional[ParsedRoster] = None,
    client: Optional[GeminiIntelligenceClient] = None,
    espn_league: Optional[Any] = None,
) -> TradeEvaluation:
    """Evaluate a prospective fantasy trade based on starting lineup impact, roster depth, and VORP.

    Args:
        league: League configuration.
        roster: Current user roster.
        giving_players: List of player names being sent away.
        receiving_players: List of player names being received.
        player_projections: Dict of {player_name: (position, projected_ppg)} for VORP math.
        opponent_roster: Optional opponent roster to evaluate trade partner leverage.
        client: Gemini intelligence client (optional).
        espn_league: Optional connected espn_api League instance.

    Returns:
        TradeEvaluation containing verdict, net starting points change, lineup impacts, and analysis.
    """
    eastern = zoneinfo.ZoneInfo("America/New_York")
    gen_time = datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z")

    # 1. Roster Ownership Validation
    is_valid, validation_errors, matched_giving = validate_trade_roster_ownership(
        roster=roster,
        giving_players=giving_players,
        receiving_players=receiving_players,
    )

    if not is_valid:
        logger.warning("Trade evaluation blocked: Roster ownership validation failed (%s)", validation_errors)
        error_bullets = "\n".join(f"- {e}" for e in validation_errors)
        return TradeEvaluation(
            verdict="INVALID",
            is_valid_trade=False,
            roster_validation_errors=validation_errors,
            your_vorp_change=0.0,
            starting_lineup_impact="Trade cannot be processed because one or more players violate roster ownership.",
            playoff_schedule_impact="N/A (Invalid trade proposal).",
            reasoning=(
                f"🚨 **Invalid Trade Proposal**: This trade cannot be executed against your current roster:\n\n"
                f"{error_bullets}\n\n"
                "Please verify that you only offer players currently rostered on your team, and that you are not "
                "requesting a player already on your roster."
            ),
            counter_suggestion="Select eligible players from your current roster to trade.",
            generated_at=gen_time,
        )

    # 2. Build incoming players data
    incoming_players = _build_incoming_roster_players(
        receiving_players=receiving_players,
        player_projections=player_projections,
        espn_league=espn_league,
        num_teams=league.num_teams,
    )

    # 3. Simulate Roster-Contextual Starting Lineup Impact
    sim_impact = simulate_trade_roster_impact(
        roster=roster,
        league=league,
        matched_giving=matched_giving,
        incoming_players=incoming_players,
    )

    pre_pts = sim_impact["pre_starting_points"]
    post_pts = sim_impact["post_starting_points"]
    net_pts = sim_impact["net_starting_points_change"]
    lineup_changes = sim_impact["starting_lineup_changes"]
    depth_impact = sim_impact["positional_depth_impact"]

    # Calculate net VORP as supporting metric
    giving_vorp = sum(
        calculate_vorp(p.projected_points, p.position, league.num_teams) for p in matched_giving
    )
    receiving_vorp = sum(
        calculate_vorp(p.projected_points, p.position, league.num_teams) for p in incoming_players
    )
    net_vorp = round(receiving_vorp - giving_vorp, 2)

    # 4. LLM Analysis via Gemini Pro if available
    if client is not None:
        roster_data = [
            {"name": p.name, "pos": p.position, "pts": p.projected_points, "slot": p.slot}
            for p in roster.players
        ]
        opp_data = None
        if opponent_roster:
            opp_data = [
                {"name": p.name, "pos": p.position, "pts": p.projected_points}
                for p in opponent_roster.players
            ]

        prompt = format_trade_prompt(
            league=league,
            your_roster={"players": roster_data},
            giving_players=giving_players,
            receiving_players=receiving_players,
            opponent_roster={"players": opp_data} if opp_data else None,
            simulated_impact={
                "pre_trade_starting_points": pre_pts,
                "post_trade_starting_points": post_pts,
                "net_starting_points_change": net_pts,
                "lineup_changes": lineup_changes,
                "positional_depth_impact": depth_impact,
                "net_vorp": net_vorp,
            },
        )

        try:
            verdict_obj = client.generate_structured(prompt=prompt, response_schema=TradeEvaluation)
            verdict_obj.is_valid_trade = True
            verdict_obj.roster_validation_errors = []
            verdict_obj.pre_trade_starting_points = pre_pts
            verdict_obj.post_trade_starting_points = post_pts
            verdict_obj.net_starting_points_change = net_pts
            verdict_obj.starting_lineup_changes = lineup_changes
            verdict_obj.positional_depth_impact = depth_impact
            verdict_obj.your_vorp_change = net_vorp
            verdict_obj.generated_at = gen_time
            return verdict_obj
        except Exception as e:
            logger.warning(
                "Gemini trade evaluation call failed (%s). Falling back to deterministic evaluation.",
                e,
            )

    # 5. Deterministic Roster-Contextual Verdict Logic
    has_critical_depth = "CRITICAL DEPTH WARNING" in depth_impact
    all_incoming_benched = all("does NOT crack your starting lineup" in c for c in lineup_changes)

    if has_critical_depth:
        verdict = "REJECT"
        reasoning = (
            f"Net starting change is {net_pts:+.1f} pts, but this trade creates a fatal roster flaw: {depth_impact}. "
            f"Surrendering {', '.join(p.name for p in matched_giving)} leaves your roster structurally depleted."
        )
        counter = "Demand a starting-caliber player at the depleted position to rebalance roster depth."
    elif net_pts >= 2.0:
        verdict = "ACCEPT"
        reasoning = (
            f"Decisive starting lineup upgrade (+{net_pts:.1f} weekly points). "
            f"Acquiring {', '.join(p.name for p in incoming_players)} meaningfully elevates your starting ceiling "
            f"from {pre_pts:.1f} to {post_pts:.1f} weekly projected points without breaking roster balance."
        )
        counter = None
    elif net_pts <= -1.5:
        verdict = "REJECT"
        reasoning = (
            f"Degrades starting lineup quality ({net_pts:+.1f} weekly points: {pre_pts:.1f} → {post_pts:.1f}). "
            f"Giving up {', '.join(p.name for p in matched_giving)} strips away more starting power than "
            f"{', '.join(p.name for p in incoming_players)} returns to your lineup."
        )
        counter = "Ask for an upgraded starter or an additional flex contributor to balance weekly scoring."
    elif all_incoming_benched:
        verdict = "REJECT"
        reasoning = (
            f"Net starting lineup impact is neutral/zero ({net_pts:+.1f} pts) because the acquired players "
            f"({', '.join(p.name for p in incoming_players)}) do NOT crack your current starting lineup and will merely "
            f"sit on your bench. Trading starting assets for bench depth is a losing strategy."
        )
        counter = "Target a player who actually upgrades one of your active starting slots."
    else:
        verdict = "COUNTER"
        reasoning = (
            f"Marginal net starting impact ({net_pts:+.1f} weekly points: {pre_pts:.1f} → {post_pts:.1f}). "
            f"The value is roughly balanced, but it does not decisively upgrade your weekly starting floor."
        )
        counter = "Offer a lower-tier bench asset instead of your current starter."

    lineup_impact_summary = (
        f"Alters starting projection by {net_pts:+.1f} points/week ({pre_pts:.1f} → {post_pts:.1f}). "
        + (" ".join(lineup_changes[:2]) if lineup_changes else "No starter slot changes.")
    )

    return TradeEvaluation(
        verdict=verdict,
        is_valid_trade=True,
        roster_validation_errors=[],
        pre_trade_starting_points=pre_pts,
        post_trade_starting_points=post_pts,
        net_starting_points_change=net_pts,
        starting_lineup_changes=lineup_changes,
        positional_depth_impact=depth_impact,
        your_vorp_change=net_vorp,
        starting_lineup_impact=lineup_impact_summary,
        playoff_schedule_impact=(
            f"Cross-reference acquired starters ({', '.join(p.name for p in incoming_players)}) with "
            f"Weeks 15-17 opposing defensive matchups."
        ),
        reasoning=reasoning,
        counter_suggestion=counter,
        generated_at=gen_time,
    )

