from backend.model.calibrate import apply_isotonic, ece, fit_isotonic
from backend.model.ensemble import Ensemble
from backend.model.signals import SIGNALS
from backend.model.upsets import analyze


def test_hedge_rewards_winner():
    e = Ensemble({"a": 0.5, "b": 0.5}, eta=2.0)
    snap = e.update({"a": 0.9, "b": 0.4}, winner_is_a=True, surface="hard")
    assert e.table("hard")["a"] > e.table("hard")["b"]
    assert abs(sum(e.table("hard").values()) - 1.0) < 1e-9
    assert snap["losses"]["a"] < snap["losses"]["b"]


def test_ensemble_abstain_and_renorm():
    e = Ensemble({"a": 0.7, "b": 0.3})
    out = e.predict({"a": 0.8, "b": None}, "hard")
    assert out["p_a"] == 0.8
    assert e.predict({"a": None}, "hard")["abstained"]


def test_surface_tables_independent():
    e = Ensemble({"a": 0.5, "b": 0.5}, eta=1.0)
    e.update({"a": 0.9, "b": 0.1}, winner_is_a=True, surface="clay")
    assert e.table("clay")["a"] > e.table("hard")["a"]


def test_all_signals_callable():
    import inspect
    from backend.model.signals import Ctx
    from backend.ratings.elo import SurfaceElo
    from backend.ratings.features import FormTracker, HeadToHead
    ctx = Ctx(SurfaceElo(), HeadToHead(), FormTracker())
    for name, fn in SIGNALS.items():
        p = fn("A", "B", "hard", ctx, date=20240101)
        assert p is None or 0.0 <= p <= 1.0, name


def test_isotonic_monotone_and_ece():
    probs = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    ys = [0, 0, 1, 0, 1, 1]
    fn = fit_isotonic(probs, ys)
    vals = [apply_isotonic(fn, p) for p in probs]
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    assert 0 <= ece(probs, ys) <= 0.5


def test_upset_autopsy():
    probs = {"elo_surface": 0.8, "h2h": 0.75, "form": 0.4, "market": None}
    w = {"elo_surface": 0.5, "h2h": 0.2, "form": 0.3}
    rep = analyze("A", "B", "hard", probs, w, 0.72, "B", "A d B", 20260101)
    assert rep is not None and rep["loser"] == "A"
    assert any("elo_surface" in lesson for lesson in rep["lessons"])
    assert analyze("A", "B", "hard", probs, w, 0.55, "B") is None  # not confident: no upset
