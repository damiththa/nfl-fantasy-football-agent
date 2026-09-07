from src.analysis.scoring import calculate_player_score, compare_scoring, project_player_score
from src.config import CHIPS_AHOY, PNA_2026, StatId


def test_scoring_calculation():
    stats = {StatId.PASS_YDS: 300, StatId.PASS_TD: 3, StatId.PASS_INT: 1}

    # PNA: 300 * 0.04 = 12 + 3 * 4.0 = 12 - 1 * 2.0 = 22.0
    pna_score = calculate_player_score(stats, PNA_2026.scoring)
    assert pna_score == 22.0

    # Chips Ahoy: 300 * 0.04 = 12 + 3 * 6.0 = 18 - 1 * 2.0 = 28.0
    chips_score = calculate_player_score(stats, CHIPS_AHOY.scoring)
    assert chips_score == 28.0


def test_compare_scoring():
    stats = {StatId.PASS_YDS: 300, StatId.PASS_TD: 3, StatId.PASS_INT: 1}
    result = compare_scoring(stats, PNA_2026, CHIPS_AHOY)
    assert result[PNA_2026.short_name] == 22.0
    assert result[CHIPS_AHOY.short_name] == 28.0
    assert result["diff"] == 6.0


def test_rushing_stats():
    stats = {StatId.RUSH_YDS: 100, StatId.RUSH_TD: 1, StatId.FUMBLES_LOST: 1}
    # 100 * 0.1 = 10 + 6 - 2 = 14.0
    score = calculate_player_score(stats, PNA_2026.scoring)
    assert score == 14.0


def test_receiving_stats():
    stats = {StatId.REC: 10, StatId.REC_YDS: 100, StatId.REC_TD: 1}
    # 10 * 1.0 = 10 + 100 * 0.1 = 10 + 6 = 26.0
    score = calculate_player_score(stats, PNA_2026.scoring)
    assert score == 26.0


def test_kicking_stats():
    stats = {
        StatId.FG_0_39: 1,  # 3 pts
        StatId.FG_40_49: 1,  # 4 pts
        StatId.FG_50_PLUS: 1,  # 5 pts
        StatId.XP_MADE: 2,  # 2 pts
        StatId.FG_MISSED: 1,  # -1 pts
    }
    # 3 + 4 + 5 + 2 - 1 = 13.0
    score = calculate_player_score(stats, PNA_2026.scoring)
    assert score == 13.0


def test_empty_stats():
    score = calculate_player_score({}, PNA_2026.scoring)
    assert score == 0.0


def test_project_player_score():
    class MockPlayer:
        def __init__(self, stats):
            self.projected_stats = stats

    player = MockPlayer({StatId.RUSH_YDS: 100, StatId.RUSH_TD: 1})
    proj, floor, ceiling = project_player_score(player, PNA_2026.scoring)
    # base proj = 10 + 6 = 16.0
    assert proj == 16.0
    assert floor == 12.8
    assert ceiling == 19.2
