"""Hedge ensemble engine: abstention, share math, learning (map step 5)."""

from __future__ import annotations

from core.ensemble import FLOOR, HedgeEnsemble

W = {"a": 0.5, "b": 0.5}


def test_predict_abstains_without_signals():
    e = HedgeEnsemble(dict(W), eta_by={})
    out = e.predict({"a": None, "b": None})
    assert out == {"p_a": 0.5, "weights_used": {}, "abstained": True}


def test_predict_byte_equal_contract():
    """Weighted mean over awake signals, renormalized; weights rounded to 3."""
    e = HedgeEnsemble(dict(W), eta_by={})
    out = e.predict({"a": 0.7, "b": 0.4, "c": None})
    assert out["p_a"] == 0.7 * 0.5 + 0.4 * 0.5  # a,b awake at 0.5 each after renorm
    assert out["weights_used"] == {"a": 0.5, "b": 0.5}
    assert out["abstained"] is False


def test_update_preserves_mass_and_floors():
    e = HedgeEnsemble(dict(W), eta_by={})
    snap = e.update({"a": 0.9, "b": 0.2}, winner_is_a=False)  # a was wrong
    w = e.table("hard")
    assert set(w) == {"a", "b"}
    assert abs(sum(w.values()) - 1.0) < 1e-9          # mass preserved
    assert w["b"] > w["a"]                             # loser shrank
    assert min(w.values()) >= FLOOR                    # floor holds
    assert snap["surface"] == "hard" and "losses" in snap


def test_default_eta_by_contract():
    """Explicit eta_by wins; non-default eta disables defaults; else adapter defaults."""
    assert HedgeEnsemble(dict(W), eta_by={"hard": 0.2}).eta_by == {"hard": 0.2}
    assert HedgeEnsemble(dict(W), eta=0.5).eta_by == {}
    assert (HedgeEnsemble(dict(W), default_eta_by={"hard": 0.07}).eta_by
            == {"hard": 0.07})
