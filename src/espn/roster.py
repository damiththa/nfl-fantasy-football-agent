"""
Models and functions for parsing ESPN roster data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

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
    is_locked: bool = False
    lock_status: str = "UNLOCKED_ACTIONABLE"
    pro_opponent: str = ""
    opponent_pos_rank: Optional[int] = None
    ros_baseline_ppg: float = 0.0

    @property
    def is_zero_projection_unplayed(self) -> bool:
        """Returns True if the player has not yet played and is projected for 0.0 points."""
        return not self.has_played and self.projected_points <= 0.0

    def is_on_bye(self, current_week: int = 0) -> bool:
        """Returns True if the player is on a bye week in current_week."""
        if current_week and self.bye_week == current_week:
            return True
        if (
            not self.has_played
            and self.projected_points <= 0.0
            and (self.injury_status or "NORMAL").upper() in ("NORMAL", "ACTIVE", "")
            and (not self.pro_opponent or self.pro_opponent.upper() in ("BYE", "NONE", ""))
        ):
            return True
        return False

    @property
    def is_protected_asset(self) -> bool:
        """Returns True if the player is a core/star asset that must NEVER be recommended as a waiver drop."""
        pos = (self.position or "").upper()
        if pos in ("DST", "D/ST", "DEF", "K", "PK"):
            return False
        if (self.injury_status or "").upper() in ("IR", "INJURY_RESERVE"):
            return False
        if self.percent_owned >= 65.0:
            return True
        effective_ppg = max(self.projected_points, self.ros_baseline_ppg)
        if effective_ppg >= 10.0:
            return True
        return False

    def __post_init__(self):
        if self.has_played:
            self.is_locked = True
        if self.is_locked and self.lock_status == "UNLOCKED_ACTIONABLE":
            slot_upper = (self.slot or "").upper()
            if slot_upper in ("BENCH", "BE"):
                self.lock_status = "LOCKED_ON_BENCH"
            elif slot_upper == "IR":
                self.lock_status = "LOCKED_ON_IR"
            else:
                self.lock_status = "LOCKED_IN_STARTING_LINEUP"


@dataclass
class ParsedRoster:
    """Structured representation of a fantasy team's roster."""

    team_name: str
    players: List[RosterPlayer] = field(default_factory=list)
    starters: List[RosterPlayer] = field(default_factory=list)
    bench: List[RosterPlayer] = field(default_factory=list)
    ir: List[RosterPlayer] = field(default_factory=list)


def get_safe_drop_candidates(
    roster: ParsedRoster, current_week: int = 0
) -> list[RosterPlayer]:
    """Return bench players that are genuinely safe to drop, ordered from most expendable to least.

    Strictly excludes:
    1. Any player currently on a bye week (whose 0.0 weekly projection is temporary).
    2. Any protected core/star skill asset (is_protected_asset == True, e.g., >=65% owned or >=10.0 ROS PPG).
    3. Any player already in an IR slot.
    """
    clear_drops: list[RosterPlayer] = []
    marginal_drops: list[RosterPlayer] = []

    for b in roster.bench:
        inj = (b.injury_status or "").upper()
        if inj == "IR":
            continue
        pos = (b.position or "").upper()

        # 1. Extra Kicker or Defense on the bench is always the #1 most expendable roster spot
        if pos in ("DST", "D/ST", "DEF", "K", "PK"):
            clear_drops.append(b)
            continue

        # 2. Never drop a skill player simply because they are on a bye week!
        if b.is_on_bye(current_week):
            continue

        # 3. Never drop a protected core/star skill asset (e.g. James Cook, Ladd McConkey, Khalil Shakir)
        if b.is_protected_asset:
            continue

        marginal_drops.append(b)

    # Sort marginal drops by effective season value (not just a single week's projection), then ownership
    marginal_drops.sort(
        key=lambda p: (
            max(p.projected_points, getattr(p, "ros_baseline_ppg", 0.0)),
            p.percent_owned,
        )
    )
    clear_drops.sort(key=lambda p: p.projected_points)
    return clear_drops + marginal_drops



def parse_roster(team: Any, league_config: LeagueConfig, week: int | None = None) -> ParsedRoster:
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
    team_name = (
        getattr(team, "team_name", "Unknown Team") if not isinstance(team, list) else "My Team"
    )
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

        if direct_actual is not None and (
            (game_played_attr is not None and game_played_attr > 0) or direct_actual > 0
        ):
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

        espn_locked = bool(
            getattr(player, "lineupLocked", False)
            or getattr(player, "lineup_locked", False)
            or getattr(player, "is_locked", False)
        )
        is_locked = bool(has_played or espn_locked)

        slot_upper = slot_name.upper()
        if is_locked:
            if slot_upper in ("BENCH", "BE"):
                lock_status = "LOCKED_ON_BENCH"
            elif slot_upper == "IR":
                lock_status = "LOCKED_ON_IR"
            else:
                lock_status = "LOCKED_IN_STARTING_LINEUP"
        else:
            lock_status = "UNLOCKED_ACTIONABLE"

        pro_opp = getattr(player, "pro_opponent", "") or getattr(player, "proOpponent", "")
        opp_rank = getattr(player, "pro_pos_rank", None) or getattr(player, "proPosRank", None)
        opp_pos_rank = None
        if isinstance(opp_rank, (int, float)) and opp_rank > 0:
            opp_pos_rank = int(opp_rank)

        detected_bye = getattr(player, "bye_week", 0) or 0
        if not detected_bye and target_week and target_week > 0:
            if not has_played and proj_pts <= 0.0 and (not pro_opp or str(pro_opp).upper() in ("BYE", "NONE", "")):
                detected_bye = target_week

        # Compute rest-of-season (ROS) / multi-week baseline PPG so bye-week 0.0 projections
        # never disguise a star player as a 0.0-point scrub
        weekly_samples: list[float] = []
        for k, v in stats_dict.items():
            if not isinstance(v, dict):
                continue
            try:
                k_int = int(k)
            except (ValueError, TypeError):
                continue
            # Ignore season-total keys (0 or >= 2000) and focus on weekly entries (1..18)
            if 1 <= k_int <= 18:
                w_pts = v.get("points")
                w_proj = v.get("projected_points")
                if isinstance(w_pts, (int, float)) and w_pts > 0:
                    weekly_samples.append(float(w_pts))
                elif isinstance(w_proj, (int, float)) and w_proj > 0:
                    weekly_samples.append(float(w_proj))

        pct_owned = float(getattr(player, "percent_owned", 0.0) or 0.0)
        if weekly_samples:
            ros_baseline = round(sum(weekly_samples) / len(weekly_samples), 2)
        elif proj_pts > 0:
            ros_baseline = proj_pts
        elif pct_owned >= 85.0:
            ros_baseline = 14.5
        elif pct_owned >= 70.0:
            ros_baseline = 11.5
        elif pct_owned >= 50.0:
            ros_baseline = 9.0
        else:
            ros_baseline = 0.0

        rp = RosterPlayer(
            name=getattr(player, "name", "Unknown Player") or "Unknown Player",
            position=getattr(player, "position", "UNK") or "UNK",
            team=getattr(player, "proTeam", "UNK") or "UNK",
            slot=slot_name,
            projected_points=proj_pts,
            actual_points=actual_pts,
            injury_status=getattr(player, "injuryStatus", "NORMAL") or "NORMAL",
            bye_week=detected_bye,
            percent_owned=pct_owned,
            has_played=has_played,
            is_locked=is_locked,
            lock_status=lock_status,
            pro_opponent=str(pro_opp) if pro_opp else "",
            opponent_pos_rank=opp_pos_rank,
            ros_baseline_ppg=ros_baseline,
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
