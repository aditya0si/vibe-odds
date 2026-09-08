from backend.features.score import parse_score
from backend.markov.match import game_p, match_p, set_p, tiebreak_p


def test_game_symmetry_and_monotone():
    assert abs(game_p(0.5) - 0.5) < 1e-9
    assert game_p(0.65) > game_p(0.60) > 0.5
    # mass check: strong server holds ~83%
    assert 0.80 < game_p(0.65) < 0.86


def test_tiebreak_symmetry():
    assert abs(tiebreak_p(0.6, 0.6) - 0.5) < 1e-9
    assert tiebreak_p(0.7, 0.6) > 0.5


def test_set_match_symmetry():
    assert abs(set_p(0.8, 0.8, 0.6, 0.6) - 0.5) < 1e-6
    assert abs(set_p(0.55, 0.55, 0.55, 0.55) - 0.5) < 1e-6
    assert abs(match_p(0.64, 0.64) - 0.5) < 1e-6


def test_server_advantage_and_bo5():
    assert match_p(0.68, 0.60) > 0.5
    p3 = match_p(0.68, 0.60, best_of=3)
    p5 = match_p(0.68, 0.60, best_of=5)
    assert p5 > p3  # longer format favors the better player


def test_score_parser():
    s = parse_score("6-2 6-7(5) 6-3 6-2")
    assert (s["sets_w"], s["sets_l"]) == (3, 1)
    assert (s["tb_w"], s["tb_l"]) == (0, 1)
    assert parse_score("W/O") is None
    assert parse_score("6-4 6-7(4) RET")["retired"] is True
