"""
League configuration, constants, and slot mappings for both ESPN leagues.

All league-specific settings are defined here so the rest of the codebase
can remain league-agnostic. Modules import from config rather than
hardcoding league-specific values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar

from dotenv import load_dotenv

# Load .env file if present (local development only; on GCP, env vars
# are injected via Secret Manager → Cloud Run env bindings).
load_dotenv()

# ---------------------------------------------------------------------------
# ESPN Slot ID mapping — maps ESPN's internal integer slot IDs to
# human-readable position names. These IDs are stable across seasons.
# ---------------------------------------------------------------------------

class SlotId(IntEnum):
    """ESPN roster slot IDs."""

    QB = 0
    TQB = 1  # Team Quarterback (rare)
    RB = 2
    RB_WR = 3
    WR = 4
    WR_TE = 5
    TE = 6
    OP = 7  # Offensive Player (Superflex)
    DT = 8
    DE = 9
    LB = 10
    DL = 11
    CB = 12
    S = 13
    DB = 14
    DP = 15  # Defensive Player
    DST = 16
    K = 17
    P = 18
    HC = 19
    BENCH = 20
    IR = 21
    FLEX = 23  # RB/WR/TE


SLOT_DISPLAY_NAMES: dict[int, str] = {
    SlotId.QB: "QB",
    SlotId.TQB: "TQB",
    SlotId.RB: "RB",
    SlotId.RB_WR: "RB/WR",
    SlotId.WR: "WR",
    SlotId.WR_TE: "WR/TE",
    SlotId.TE: "TE",
    SlotId.OP: "OP (Superflex)",
    SlotId.DT: "DT",
    SlotId.DE: "DE",
    SlotId.LB: "LB",
    SlotId.DL: "DL",
    SlotId.CB: "CB",
    SlotId.S: "S",
    SlotId.DB: "DB",
    SlotId.DP: "DP",
    SlotId.DST: "D/ST",
    SlotId.K: "K",
    SlotId.P: "P",
    SlotId.HC: "HC",
    SlotId.BENCH: "Bench",
    SlotId.IR: "IR",
    SlotId.FLEX: "FLEX",
}


# Slots that count as "starters" (not bench/IR)
STARTER_SLOT_IDS: frozenset[int] = frozenset(
    sid for sid in SlotId if sid not in (SlotId.BENCH, SlotId.IR)
)


# ---------------------------------------------------------------------------
# ESPN Stat ID mapping — used by the scoring engine to interpret
# stat categories from box scores and projections.
# ---------------------------------------------------------------------------

class StatId(IntEnum):
    """Key ESPN stat IDs used in scoring calculations."""

    # Passing
    PASS_ATT = 0
    PASS_COMP = 1
    PASS_INC = 2
    PASS_YDS = 3
    PASS_TD = 4
    PASS_INT = 20

    # Rushing
    RUSH_ATT = 23
    RUSH_YDS = 24
    RUSH_TD = 25

    # Receiving
    REC = 53  # Receptions (PPR)
    REC_YDS = 42
    REC_TD = 43

    # Misc offense
    FUMBLES_LOST = 72
    TWO_PT_CONV = 62

    # Kicking
    FG_MADE = 83
    FG_MISSED = 84
    FG_0_39 = 77
    FG_40_49 = 78
    FG_50_PLUS = 79
    XP_MADE = 86
    XP_MISSED = 88

    # Defense / Special Teams
    DST_TD = 95
    DST_FUMBLE_REC = 96
    DST_BLOCKED_KICK = 97
    DST_SAFETY = 98
    DST_INT = 99
    DST_PTS_ALLOWED = 89
    DST_SACK = 106
    DST_YARDS_ALLOWED = 123


# ---------------------------------------------------------------------------
# League configuration dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RosterSlots:
    """Starting lineup slot configuration for a league."""

    qb: int = 1
    rb: int = 2
    wr: int = 2
    te: int = 1
    flex: int = 1
    dst: int = 1
    k: int = 0
    bench: int = 7
    ir: int = 1

    @property
    def total_starters(self) -> int:
        return self.qb + self.rb + self.wr + self.te + self.flex + self.dst + self.k

    @property
    def total_roster(self) -> int:
        return self.total_starters + self.bench + self.ir


@dataclass(frozen=True)
class ScoringRules:
    """Key scoring settings for a league.

    Values represent points per occurrence (e.g., pass_td=4 means
    4 points per passing touchdown). Yardage values are per-yard
    (e.g., pass_yds=0.04 means 1 point per 25 passing yards).
    """

    pass_td: float = 4.0
    pass_yds: float = 0.04  # 1 pt per 25 yards
    pass_int: float = -2.0
    rush_td: float = 6.0
    rush_yds: float = 0.1  # 1 pt per 10 yards
    rec_td: float = 6.0
    rec_yds: float = 0.1  # 1 pt per 10 yards
    reception: float = 1.0  # PPR
    fumble_lost: float = -2.0
    two_pt_conv: float = 2.0


@dataclass(frozen=True)
class LeagueConfig:
    """Complete configuration for a single ESPN Fantasy league."""

    league_id: int
    team_id: int
    name: str
    short_name: str
    num_teams: int
    season: int
    scoring: ScoringRules
    roster: RosterSlots
    waiver_type: str = "WAIVERS_TRADITIONAL"

    # ESPN season constants
    REGULAR_SEASON_WEEKS: ClassVar[int] = 14
    PLAYOFF_WEEKS: ClassVar[tuple[int, ...]] = (15, 16, 17)


# ---------------------------------------------------------------------------
# The two leagues — all settings confirmed from ESPN API on 2026-09-07
# ---------------------------------------------------------------------------

PNA_2026 = LeagueConfig(
    league_id=991059191,
    team_id=3,
    name="PNA 2026 League",
    short_name="PNA",
    num_teams=12,
    season=2026,
    scoring=ScoringRules(
        pass_td=4.0,
        pass_yds=0.04,
        rush_td=6.0,
        rush_yds=0.1,
        rec_td=6.0,
        rec_yds=0.1,
        reception=1.0,  # Full PPR
    ),
    roster=RosterSlots(
        qb=1, rb=2, wr=2, te=1,
        flex=2,  # 2 FLEX slots
        dst=1,
        k=0,    # No kicker
        bench=7, ir=1,
    ),
)

CHIPS_AHOY = LeagueConfig(
    league_id=735288,
    team_id=3,
    name="Chips ahoy",
    short_name="Chips",
    num_teams=10,
    season=2026,
    scoring=ScoringRules(
        pass_td=6.0,  # 6pt passing TD — QBs more valuable here
        pass_yds=0.04,
        rush_td=6.0,
        rush_yds=0.1,
        rec_td=6.0,
        rec_yds=0.1,
        reception=1.0,  # Full PPR
    ),
    roster=RosterSlots(
        qb=1, rb=2, wr=2, te=1,
        flex=1,  # 1 FLEX slot
        dst=1,
        k=1,    # Has kicker
        bench=7, ir=2,
    ),
)

# All leagues indexed by league_id for easy lookup
ALL_LEAGUES: dict[int, LeagueConfig] = {
    PNA_2026.league_id: PNA_2026,
    CHIPS_AHOY.league_id: CHIPS_AHOY,
}


# ---------------------------------------------------------------------------
# Credential helpers — read from env vars, never hardcoded
# ---------------------------------------------------------------------------

def get_espn_credentials() -> tuple[str, str]:
    """Return (espn_s2, swid) from environment variables.

    Raises:
        EnvironmentError: If either credential is missing.
    """
    espn_s2 = os.environ.get("ESPN_S2")
    swid = os.environ.get("ESPN_SWID")
    if not espn_s2 or not swid:
        raise EnvironmentError(
            "ESPN credentials not found. Set ESPN_S2 and ESPN_SWID "
            "environment variables. See .env.example for details."
        )
    return espn_s2, swid


def get_gemini_api_key() -> str:
    """Return Gemini API key from environment.

    Raises:
        EnvironmentError: If the key is missing.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise EnvironmentError(
            "GEMINI_API_KEY not found. Get a free key from "
            "https://aistudio.google.com/app/apikey"
        )
    return key


def get_current_season() -> int:
    """Return the NFL season year from env or default."""
    return int(os.environ.get("NFL_SEASON", "2026"))
