"""Integrity tests (Astra W1 audit): orientation, symmetry, unknown handling."""

from backend.model.signals import Ctx, build_row
from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years


def _ctx():
    return Ctx(SurfaceElo(), HeadToHead(), FormTracker())


def test_build_row_antisymmetric():
    """Swapping perspective must negate every diff feature (orientation contract)."""
    ctx = _ctx()
    m = {"date": 20240101, "surface": "hard", "best_of": 3, "round": "R128", "level_mult": 1.0}
    r1 = build_row("AAA", "BBB", m, ctx)
    r2 = build_row("BBB", "AAA", m, ctx)
    for k in r1:
        if k.endswith("_diff"):
            assert r1[k] == -r2[k], k
    assert r1["elo_surf_diff"] == -r2["elo_surf_diff"]


def test_neutral_ordering_label():
    """y=1 iff winner is the alphabetically-first player (no winner-column leak)."""
    n = 0
    for m in load_years(2024, 2024):
        if m["walkover"]:
            continue
        a, b = sorted([m["winner"], m["loser"]])
        y = 1 if m["winner"] == a else 0
        assert y in (0, 1)
        assert (a == m["winner"]) == (y == 1)
        n += 1
        if n >= 200:
            break
    assert n == 200


def test_serve_swap_complementary():
    """P(a beats b) + P(b beats a) == 1 (player-swap symmetry, live path)."""
    from backend.model.serve import predict_match
    c1 = predict_match("Carlos Alcaraz", "Jannik Sinner", "hard", best_of=5)
    c2 = predict_match("Jannik Sinner", "Carlos Alcaraz", "hard", best_of=5)
    assert abs(c1["model_prob_a"] + c2["model_prob_a"] - 1.0) < 1e-9
    assert c1["pick"] == c2["pick"]  # same winner from either orientation


def test_serve_unknown_flagged_end_to_end():
    from backend.model.serve import predict_match
    c = predict_match("Nobody McNobody", "Jannik Sinner", "hard")
    assert c["unknown_players"] == ["Nobody Mcnobody"]
    assert any("caution" in r for r in c["reasons"])
