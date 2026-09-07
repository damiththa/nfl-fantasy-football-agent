import pytest

from src.config import CHIPS_AHOY, PNA_2026, SLOT_DISPLAY_NAMES, SlotId, get_espn_credentials


def test_league_configs():
    assert PNA_2026.league_id == 991059191
    assert CHIPS_AHOY.league_id == 735288

    assert PNA_2026.scoring.pass_td == 4.0
    assert CHIPS_AHOY.scoring.pass_td == 6.0

    assert PNA_2026.roster.k == 0
    assert CHIPS_AHOY.roster.k == 1


def test_slot_display_names():
    assert SLOT_DISPLAY_NAMES[SlotId.QB] == "QB"
    assert SLOT_DISPLAY_NAMES[SlotId.FLEX] == "FLEX"


def test_roster_slot_totals():
    # 1 qb, 2 rb, 2 wr, 1 te, 2 flex, 1 dst
    assert PNA_2026.roster.total_starters == 9
    # 9 + 7 bench + 1 ir
    assert PNA_2026.roster.total_roster == 17

    # 1 qb, 2 rb, 2 wr, 1 te, 1 flex, 1 dst, 1 k
    assert CHIPS_AHOY.roster.total_starters == 9
    # 9 + 7 bench + 2 ir
    assert CHIPS_AHOY.roster.total_roster == 18


def test_credential_helper(monkeypatch):
    monkeypatch.delenv("ESPN_S2", raising=False)
    monkeypatch.delenv("ESPN_SWID", raising=False)
    with pytest.raises(EnvironmentError):
        get_espn_credentials()
