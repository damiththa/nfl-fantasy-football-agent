"""
Models and functions for parsing ESPN roster data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from src.config import SLOT_DISPLAY_NAMES, LeagueConfig


@dataclass
class RosterPlayer:
    """Represents a player on a fantasy team."""

    name: str
    position: str
    team: str
    slot: str
    projected_points: float
    actual_points: float = 0.0
    injury_status: str = "NORMAL"
    bye_week: int = 0
    percent_owned: float = 0.0
    has_played: bool = False


@dataclass
class ParsedRoster:
    """Structured representation of a fantasy team's roster."""

    team_name: str
    players: List[RosterPlayer] = field(default_factory=list)
    starters: List[RosterPlayer] = field(default_factory=list)
    bench: List[RosterPlayer] = field(default_factory=list)
    ir: List[RosterPlayer] = field(default_factory=list)


def parse_roster(
    team: Any, league_config: LeagueConfig, week: int | None = None
) -> ParsedRoster:
    """Parse an espn_api Team or lineup list into a ParsedRoster.

    Extracts both projected and actual points from player.stats or BoxPlayer
    attributes for the specified scoring period / week.

    Args:
        team: The espn_api Team object, or a list of Player/BoxPlayer objects.
        league_config: The configuration for the league.
        week: Optional week number for stats lookup. If None, infers from team.

    Returns:
        A ParsedRoster containing structured player data with projected and actual points.
    """
    team_name = getattr(team, "team_name", "Unknown Team") if not isinstance(team, list) else "My Team"
    parsed = ParsedRoster(team_name=team_name)

    if isinstance(team, list):
        roster = team
    elif hasattr(team, "roster"):
        roster = team.roster
    else:
        roster = getattr(team, "roster", [])

    # Infer target scoring period / week
    target_week = week
    if target_week is None:
        target_week = getattr(team, "current_week", None)
    if target_week is None:
        target_week = 1

    for player in roster:
        # Resolve slot safely
        slot_pos = getattr(player, "slot_position", None)
        lineup_slot = getattr(player, "lineupSlot", None)
        if isinstance(slot_pos, (str, int)):
            slot_val = slot_pos
        elif isinstance(lineup_slot, (str, int)):
            slot_val = lineup_slot
        else:
            slot_val = "Bench"

        # If we have an integer slot ID instead (e.g. box score lineup)
        if isinstance(slot_val, int):
            slot_name = SLOT_DISPLAY_NAMES.get(slot_val, str(slot_val))
        else:
            slot_name = str(slot_val) if slot_val else "Bench"

        # 1. Direct attributes (e.g. from BoxPlayer)
        direct_actual = getattr(player, "points", None)
        if not isinstance(direct_actual, (int, float)):
            direct_actual = None

        direct_proj = getattr(player, "projected_points", None)
        if not isinstance(direct_proj, (int, float)):
            direct_proj = None

        game_played_attr = getattr(player, "game_played", None)
        if not isinstance(game_played_attr, (int, float)):
            game_played_attr = None

        # 2. Stats dictionary (standard espn_api Player objects)
        stats_dict = getattr(player, "stats", {})
        if not isinstance(stats_dict, dict):
            stats_dict = {}

        week_stat = stats_dict.get(target_week) or stats_dict.get(str(target_week)) or {}

        stat_actual = week_stat.get("points") if isinstance(week_stat, dict) else None
        stat_proj = week_stat.get("projected_points") if isinstance(week_stat, dict) else None

        # Resolve actual points and whether the player has already played
        has_played = False
        actual_pts = 0.0

        if direct_actual is not None and ((game_played_attr is not None and game_played_attr > 0) or direct_actual > 0):
            actual_pts = round(float(direct_actual), 2)
            has_played = True
        elif stat_actual is not None:
            actual_pts = round(float(stat_actual), 2)
            has_played = True
        elif direct_actual is not None and direct_actual != 0.0:
            actual_pts = round(float(direct_actual), 2)
            has_played = True
        elif game_played_attr is not None and game_played_attr == 100:
            # Player played but scored 0.0 points
            actual_pts = 0.0
            has_played = True

        # Resolve projected points
        if direct_proj is not None and direct_proj > 0:
            proj_pts = round(float(direct_proj), 2)
        elif stat_proj is not None and stat_proj > 0:
            proj_pts = round(float(stat_proj), 2)
        elif direct_proj is not None:
            proj_pts = round(float(direct_proj), 2)
        elif stat_proj is not None:
            proj_pts = round(float(stat_proj), 2)
        else:
            proj_pts = 0.0

        rp = RosterPlayer(
            name=getattr(player, "name", "Unknown Player") or "Unknown Player",
            position=getattr(player, "position", "UNK") or "UNK",
            team=getattr(player, "proTeam", "UNK") or "UNK",
            slot=slot_name,
            projected_points=proj_pts,
            actual_points=actual_pts,
            injury_status=getattr(player, "injuryStatus", "NORMAL") or "NORMAL",
            bye_week=getattr(player, "bye_week", 0) or 0,
            percent_owned=getattr(player, "percent_owned", 0.0) or 0.0,
            has_played=has_played,
        )

        parsed.players.append(rp)

        # Identify starters vs bench vs IR
        slot_upper = slot_name.upper()
        if slot_upper in ("BENCH", "BE"):
            parsed.bench.append(rp)
        elif slot_upper == "IR":
            parsed.ir.append(rp)
        else:
            parsed.starters.append(rp)

    return parsed

