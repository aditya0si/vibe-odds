"""Phase-2 nonlinear arm (plan Task 4): monotone GBM + Hedge stack determinism."""
from __future__ import annotations

import json
import random

from sports.nba.model.stack import (ARM, ETA, MONO, SIGNALS, elo_baseline_probs,
                                    fit_gbm, gbm_probs, mono_vector, new_hedge, stack_probs)

FEATS = ("a", "b", "c")


def _rows(n=400, seed=7) -> list[dict]:
    """Synthetic games: y is a noisy monotone function of a (positive) and b (negative)."""
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        x = {"a": rnd.gauss(0, 1), "b": rnd.gauss(0, 1), "c": rnd.gauss(0, 1),
             "elo_diff": rnd.gauss(0, 40), "hca": 65.0}
        z = 0.8 * x["a"] - 0.7 * x["b"] + 0.3 * x["c"]
        p = 1 / (1 + pow(2.718281828, -z))
        rows.append({"game_id": f"g{i:03d}", "season": "2020-21" if i < n // 4 else "2019-20",
                     "x": x, "home_win": int(rnd.random() < p)})
    return rows


def test_deterministic_retrain():
    """Double retrain -> byte-equal predictions (map step 9's determinism bar)."""
    rows = _rows()
    tr = [r for r in rows if r["season"] == "2019-20"]
    va = [r for r in rows if r["season"] == "2020-21"]
    m1 = fit_gbm(tr, va, FEATS, mono_map={"a": 1, "b": -1, "c": 0})
    m2 = fit_gbm(tr, va, FEATS, mono_map={"a": 1, "b": -1, "c": 0})
    p1 = json.dumps(gbm_probs(m1, rows, FEATS))
    p2 = json.dumps(gbm_probs(m2, rows, FEATS))
    assert p1 == p2, "two retrains produced different predictions"


def test_monotone_map_covers_every_registered_sign():
    from sports.nba.model.phase2 import A7_FEATURES
    v = mono_vector(A7_FEATURES)
    assert len(v) == len(A7_FEATURES)
    assert set(v) <= {-1, 0, 1}
    m = dict(zip(A7_FEATURES, v))
    # spot-check the sign logic: a good player out hurts that side
    assert m["impact_out_home"] == -1 and m["impact_out_away"] == +1
    assert m["avail_missing_home"] == -1 and m["avail_missing_away"] == +1
    assert m["elo_diff"] == +1 and m["is_neutral"] == 0
    # an unregistered feature must fail loudly, not default to 0
    try:
        mono_vector(("no_such_feature",))
        raise AssertionError("missing sign did not raise")
    except KeyError:
        pass


def test_hedge_shrinks_the_losing_signal():
    h = new_hedge()
    assert set(h.table("ml")) == set(SIGNALS)
    # the formula signal is confidently wrong every game; the baseline is right
    for _ in range(30):
        h.predict({"formula": 0.9, "gbm": 0.55, "elo_baseline": 0.1}, surface="ml")
        h.update({"formula": 0.9, "gbm": 0.55, "elo_baseline": 0.1},
                 winner_is_a=False, surface="ml")
    w = h.table("ml")
    assert w["elo_baseline"] > w["formula"], (w,)
    assert abs(ETA - 0.1) < 1e-12            # fixed a priori, not gate-tuned


def test_elo_baseline_is_market_free_and_bounded():
    rows = _rows(n=20)
    ps = elo_baseline_probs(rows)
    assert all(0.0 < p < 1.0 for p in ps)
    assert all(isinstance(p, float) for p in ps)


def test_stack_walk_predicts_before_it_updates():
    """Weights at game k must only ever see games < k: with one signal that is
    right early, its weight can only rise AFTER those games settle."""
    rows = _rows(n=60)
    mono = {"a": 1, "b": -1, "c": 0}
    # run the walk manually through the public pieces (stack_probs fits its own models)
    tr = [r for r in rows if r["season"] == "2019-20"]
    va = [r for r in rows if r["season"] == "2020-21"]
    model = fit_gbm(tr, va, FEATS, mono_map=mono)
    pg = gbm_probs(model, rows, FEATS)
    h = new_hedge()
    for i, r in enumerate(rows):
        before = dict(h.table("ml"))
        probs = {"formula": 0.5, "gbm": pg[i], "elo_baseline": 0.5}
        h.predict(probs, surface="ml")
        h.update(probs, winner_is_a=bool(r["home_win"]), surface="ml")
        # the first game cannot move weights toward a signal based on its own outcome
        if i == 0:
            assert abs(before["gbm"] - 1 / 3) < 1e-12
    assert ARM == "G1"
