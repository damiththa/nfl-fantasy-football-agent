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
    actual_points: float
    injury_status: str
    bye_week: int
    percent_owned: float


@dataclass
class ParsedRoster:
    """Structured representation of a fantasy team's roster."""

    team_name: str
    players: List[RosterPlayer] = field(default_factory=list)
    starters: List[RosterPlayer] = field(default_factory=list)
    bench: List[RosterPlayer] = field(default_factory=list)
    ir: List[RosterPlayer] = field(default_factory=list)


def parse_roster(team: Any, league_config: LeagueConfig) -> ParsedRoster:
    """Parse an espn_api Team object into a ParsedRoster.

    Args:
        team: The espn_api Team object.
        league_config: The configuration for the league.

    Returns:
        A ParsedRoster containing structured player data.
    """
    parsed = ParsedRoster(team_name=getattr(team, "team_name", "Unknown Team"))

    roster = getattr(team, "roster", [])
    for player in roster:
        # espn_api player object usually has these attributes:
        # name, position, proTeam, projected_points, points, injuryStatus, bye_week, percent_owned
        # lineupSlot is a string like 'Bench', 'IR', 'RB', 'RB/WR/TE'
        slot_val = getattr(player, "lineupSlot", "Bench") or "Bench"

        # If we have an integer slot ID instead for some reason (e.g., box score lineup)
        if isinstance(slot_val, int):
            slot_name = SLOT_DISPLAY_NAMES.get(slot_val, str(slot_val))
        else:
            slot_name = slot_val or "Bench"

        rp = RosterPlayer(
            name=getattr(player, "name", "Unknown Player") or "Unknown Player",
            position=getattr(player, "position", "UNK") or "UNK",
            team=getattr(player, "proTeam", "UNK") or "UNK",
            slot=slot_name,
            projected_points=getattr(player, "projected_points", 0.0) or 0.0,
            actual_points=getattr(player, "points", 0.0) or 0.0,
            injury_status=getattr(player, "injuryStatus", "NORMAL") or "NORMAL",
            bye_week=getattr(player, "bye_week", 0) or 0,
            percent_owned=getattr(player, "percent_owned", 0.0) or 0.0,
        )

        parsed.players.append(rp)

        # Identify starters vs bench vs IR
        slot_upper = slot_name.upper()
        if slot_upper == "BENCH" or slot_upper == "BE":
            parsed.bench.append(rp)
        elif slot_upper == "IR":
            parsed.ir.append(rp)
        else:
            parsed.starters.append(rp)

    return parsed
