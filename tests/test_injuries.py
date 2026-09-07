from src.data.injuries import (
    PlayerInjuryInfo,
    get_injury_report,
    get_team_injuries,
    normalize_name,
)


def test_normalize_name():
    assert normalize_name("Patrick Mahomes II") == "patrick mahomes"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("Travis Etienne Jr.") == "travis etienne"
    assert normalize_name("Marvin Harrison Jr") == "marvin harrison"
    assert normalize_name("Amon-Ra St. Brown") == "amon-ra st brown"
    assert normalize_name("  C.J. Stroud  ") == "cj stroud"
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


def test_get_injury_report():
    mock_players = {
        "1": PlayerInjuryInfo(
            player_id="1",
            full_name="Christian McCaffrey",
            team="SF",
            position="RB",
            injury_status="Questionable",
            practice_participation="Limited Participation",
            injury_body_part="Calf",
            injury_notes="Day-to-day",
            sport="nfl",
            active=True,
        ),
        "2": PlayerInjuryInfo(
            player_id="2",
            full_name="Patrick Mahomes II",
            team="KC",
            position="QB",
            injury_status=None,
            practice_participation="Full Participation",
            injury_body_part=None,
            injury_notes=None,
            sport="nfl",
            active=True,
        ),
    }

    report = get_injury_report(
        ["Patrick Mahomes", "Christian McCaffrey", "Nonexistent Player"], mock_players
    )
    assert len(report) == 2
    names = {p.full_name for p in report}
    assert "Christian McCaffrey" in names
    assert "Patrick Mahomes II" in names

    cmc = next(p for p in report if p.full_name == "Christian McCaffrey")
    assert cmc.injury_status == "Questionable"
    assert cmc.injury_body_part == "Calf"


def test_get_team_injuries():
    mock_players = {
        "1": PlayerInjuryInfo(
            player_id="1",
            full_name="Deebo Samuel",
            team="SF",
            position="WR",
            injury_status="Questionable",
            practice_participation="Limited Participation",
            injury_body_part="Hamstring",
            injury_notes=None,
            sport="nfl",
            active=True,
        ),
        "2": PlayerInjuryInfo(
            player_id="2",
            full_name="Brock Purdy",
            team="SF",
            position="QB",
            injury_status=None,
            practice_participation=None,
            injury_body_part=None,
            injury_notes=None,
            sport="nfl",
            active=True,
        ),
        "3": PlayerInjuryInfo(
            player_id="3",
            full_name="Patrick Mahomes",
            team="KC",
            position="QB",
            injury_status="Questionable",
            practice_participation="Limited Participation",
            injury_body_part="Ankle",
            injury_notes=None,
            sport="nfl",
            active=True,
        ),
    }

    sf_injuries = get_team_injuries("sf", mock_players)
    assert len(sf_injuries) == 1
    assert sf_injuries[0].full_name == "Deebo Samuel"

    kc_injuries = get_team_injuries("KC", mock_players)
    assert len(kc_injuries) == 1
    assert kc_injuries[0].full_name == "Patrick Mahomes"

    buf_injuries = get_team_injuries("BUF", mock_players)
    assert buf_injuries == []
