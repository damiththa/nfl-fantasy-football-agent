"""
Models and functions for parsing ESPN roster data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from src.config import SLOT_DISPLAY_NAMES, LeagueConfig
from src.data.time_utils import is_kickoff_passed


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
        opp_upper = (self.pro_opponent or "").strip().upper()
        if opp_upper == "BYE":
            return True
        # If the player's NFL team has a scheduled opponent this week, they are NOT on bye
        if opp_upper and opp_upper not in ("BYE", "NONE"):
            return False

        inj_upper = (self.injury_status or "NORMAL").strip().upper()
        if current_week and self.bye_week == current_week:
            # Never misclassify an OUT/IR/PUP/Suspended/Doubtful player as "on bye" unless opponent=="BYE"
            if inj_upper in (
                "OUT",
                "O",
                "IR",
                "INJURY_RESERVE",
                "PUP",
                "SUSPENSION",
                "SUS",
                "SUSPENDED",
                "DOUBTFUL",
                "D",
            ):
                return False
            return True

        if (
            not self.has_played
            and self.projected_points <= 0.0
            and inj_upper in ("NORMAL", "ACTIVE", "")
            and (not opp_upper or opp_upper in ("BYE", "NONE"))
            and (self.team or "").strip().upper() not in ("FA", "FREE AGENT", "UNK", "")
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
        effective_ppg = self.ros_baseline_ppg if self.ros_baseline_ppg > 0 else self.projected_points
        if effective_ppg >= 12.0:
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

    def __post_init__(self) -> None:
        if self.players and not (self.starters or self.bench or self.ir):
            for p in self.players:
                slot_upper = (p.slot or "").upper()
                if slot_upper in ("BENCH", "BE"):
                    self.bench.append(p)
                elif slot_upper == "IR":
                    self.ir.append(p)
                else:
                    self.starters.append(p)
        elif not self.players and (self.starters or self.bench or self.ir):
            self.players = list(self.starters) + list(self.bench) + list(self.ir)

    @property
    def total_projected(self) -> float:
        """Sum of projected points across current starters."""
        return round(sum(p.projected_points for p in self.starters), 1)


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

        # Resolve actual points and whether the player has already played.
        # Note: espn_api populates stats[week]["points"] = 0.0 as a placeholder BEFORE kickoff;
        # a 0.0 stat_actual without game_played==100 must NOT falsely lock an unplayed player.
        has_played = False
        actual_pts = 0.0

        if direct_actual is not None and (
            (game_played_attr is not None and game_played_attr > 0) or direct_actual > 0
        ):
            actual_pts = round(float(direct_actual), 2)
            has_played = True
        elif isinstance(stat_actual, (int, float)) and stat_actual != 0.0:
            actual_pts = round(float(stat_actual), 2)
            has_played = True
        elif direct_actual is not None and direct_actual != 0.0:
            actual_pts = round(float(direct_actual), 2)
            has_played = True
        elif game_played_attr is not None and game_played_attr == 100:
            # Player completed game and scored 0.0 points
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

        # Check kickoff timestamp (BoxPlayer.game_date) to lock players whose game is already underway.
        # Uses timezone-aware UTC comparison to ensure daylight saving time transitions never cause locks to fail.
        game_date_attr = getattr(player, "game_date", None)
        kickoff_passed = is_kickoff_passed(game_date_attr)

        espn_locked = any(
            getattr(player, attr, None) is True
            for attr in ("lineupLocked", "lineup_locked", "is_locked")
        )
        is_locked = bool(has_played or espn_locked or kickoff_passed)

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

        raw_pro_opp = getattr(player, "pro_opponent", "") or getattr(player, "proOpponent", "")
        pro_opp = raw_pro_opp if isinstance(raw_pro_opp, str) else ""
        opp_rank = getattr(player, "pro_pos_rank", None) or getattr(player, "proPosRank", None)
        opp_pos_rank = None
        if isinstance(opp_rank, (int, float)) and opp_rank > 0:
            opp_pos_rank = int(opp_rank)

        inj_status_raw = getattr(player, "injuryStatus", "NORMAL")
        inj_status = inj_status_raw if isinstance(inj_status_raw, str) and inj_status_raw else "NORMAL"
        inj_upper = inj_status.strip().upper()

        pro_team_raw = getattr(player, "proTeam", "UNK")
        pro_team_str = pro_team_raw if isinstance(pro_team_raw, str) and pro_team_raw else "UNK"

        raw_bye = getattr(player, "bye_week", 0)
        detected_bye = int(raw_bye) if isinstance(raw_bye, (int, float)) else 0
        if not detected_bye and target_week and target_week > 0:
            if pro_opp.strip().upper() == "BYE":
                detected_bye = target_week
            elif (
                not has_played
                and proj_pts <= 0.0
                and inj_upper in ("NORMAL", "ACTIVE", "")
                and not pro_opp.strip()
                and pro_team_str.strip().upper() not in ("FA", "FREE AGENT", "UNK", "")
            ):
                detected_bye = target_week

        # Compute rest-of-season (ROS) / multi-week baseline PPG so bye-week 0.0 projections
        # never disguise a star player as a 0.0-point scrub, and single-week spikes do not
        # distort full-season averages when stats[0]['avg_points'] is available.
        season_stat = stats_dict.get(0) or stats_dict.get("0") or {}
        season_avg = season_stat.get("avg_points") if isinstance(season_stat, dict) else None
        season_proj_avg = (
            season_stat.get("projected_avg_points") if isinstance(season_stat, dict) else None
        )

        weekly_samples: list[float] = []
        for k, v in stats_dict.items():
            if not isinstance(v, dict):
                continue
            try:
                k_int = int(k)
            except (ValueError, TypeError):
                continue
            # Focus on weekly entries (1..18)
            if 1 <= k_int <= 18:
                w_pts = v.get("points")
                w_proj = v.get("projected_points")
                if isinstance(w_pts, (int, float)) and w_pts > 0:
                    weekly_samples.append(float(w_pts))
                elif (
                    k_int < target_week
                    and isinstance(w_pts, (int, float))
                    and w_pts == 0.0
                    and isinstance(w_proj, (int, float))
                    and w_proj > 0
                ):
                    # Completed past game where an active projected player scored 0.0
                    weekly_samples.append(0.0)
                elif isinstance(w_proj, (int, float)) and w_proj > 0:
                    weekly_samples.append(float(w_proj))

        raw_pct_owned = getattr(player, "percent_owned", 0.0)
        pct_owned = float(raw_pct_owned) if isinstance(raw_pct_owned, (int, float)) else 0.0

        if isinstance(season_avg, (int, float)) and season_avg > 0:
            if proj_pts > 0:
                ros_baseline = round(0.6 * float(season_avg) + 0.4 * proj_pts, 2)
            else:
                ros_baseline = round(float(season_avg), 2)
        elif len(weekly_samples) >= 2:
            ros_baseline = round(sum(weekly_samples) / len(weekly_samples), 2)
        elif len(weekly_samples) == 1:
            if proj_pts > 0:
                ros_baseline = round(0.5 * weekly_samples[0] + 0.5 * proj_pts, 2)
            else:
                ros_baseline = round(weekly_samples[0], 2)
        elif isinstance(season_proj_avg, (int, float)) and season_proj_avg > 0:
            ros_baseline = round(float(season_proj_avg), 2)
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

        raw_name = getattr(player, "name", "Unknown Player")
        p_name = raw_name if isinstance(raw_name, str) and raw_name else "Unknown Player"
        raw_pos = getattr(player, "position", "UNK")
        p_pos = raw_pos if isinstance(raw_pos, str) and raw_pos else "UNK"

        rp = RosterPlayer(
            name=p_name,
            position=p_pos,
            team=pro_team_str,
            slot=slot_name,
            projected_points=proj_pts,
            actual_points=actual_pts,
            injury_status=inj_status,
            bye_week=detected_bye,
            percent_owned=pct_owned,
            has_played=has_played,
            is_locked=is_locked,
            lock_status=lock_status,
            pro_opponent=pro_opp.strip(),
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


def enrich_roster_from_box_matchup(
    roster: ParsedRoster,
    matchup_roster: Optional[ParsedRoster] = None,
    current_week: int = 0,
    odds: Optional[list[Any]] = None,
) -> ParsedRoster:
    """Enrich a Team-parsed roster with BoxPlayer fields (pro_opponent, opponent_pos_rank,
    live game locks/points, and confirmed BYE status) and/or Vegas weekly schedule odds.
    """
    box_map: dict[str, RosterPlayer] = {}
    if matchup_roster and matchup_roster.players:
        for bp in matchup_roster.players:
            box_map[bp.name.lower()] = bp

    from src.data.vegas import get_player_game_odds, normalize_team_abbr

    for p in roster.players:
        bp = box_map.get(p.name.lower())
        if bp is not None:
            if bp.pro_opponent and not p.pro_opponent:
                p.pro_opponent = bp.pro_opponent
            if bp.opponent_pos_rank is not None and p.opponent_pos_rank is None:
                p.opponent_pos_rank = bp.opponent_pos_rank
            if bp.projected_points > 0 and p.projected_points <= 0:
                p.projected_points = bp.projected_points
            if bp.has_played and not p.has_played:
                p.has_played = True
                p.actual_points = bp.actual_points
            if bp.is_locked and not p.is_locked:
                p.is_locked = True
                slot_upper = (p.slot or "").upper()
                if slot_upper in ("BENCH", "BE"):
                    p.lock_status = "LOCKED_ON_BENCH"
                elif slot_upper == "IR":
                    p.lock_status = "LOCKED_ON_IR"
                else:
                    p.lock_status = "LOCKED_IN_STARTING_LINEUP"
            if bp.pro_opponent.upper() == "BYE" and current_week > 0:
                p.bye_week = current_week

        # Cross-reference Vegas schedule if pro_opponent is still empty or to verify official kickoff lock
        if odds:
            team_clean = (p.team or "").strip().upper()
            if team_clean not in ("", "UNK", "FA", "FREE AGENT", "NONE"):
                game = get_player_game_odds(team_clean, odds)
                if game:
                    norm_team = normalize_team_abbr(team_clean)
                    norm_home = normalize_team_abbr(game.home_team)
                    if not p.pro_opponent:
                        p.pro_opponent = game.away_team if norm_team == norm_home else game.home_team
                        if current_week and p.bye_week == current_week:
                            p.bye_week = 0
                    # Lock player if game has kicked off based on official ESPN Scoreboard UTC game_time
                    if not p.is_locked and is_kickoff_passed(game.game_time):
                        p.is_locked = True
                        slot_upper = (p.slot or "").upper()
                        if slot_upper in ("BENCH", "BE"):
                            p.lock_status = "LOCKED_ON_BENCH"
                        elif slot_upper == "IR":
                            p.lock_status = "LOCKED_ON_IR"
                        else:
                            p.lock_status = "LOCKED_IN_STARTING_LINEUP"
                elif len(odds) >= 10 and not p.pro_opponent:
                    p.pro_opponent = "BYE"
                    if current_week > 0:
                        p.bye_week = current_week

    return roster
