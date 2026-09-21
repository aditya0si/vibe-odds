"""Tests for the NBA formula: artifact integrity, prediction invariants, metric sanity, and the
sign convention of the paired statistics that the pre-registered tiers depend on.

The sign convention matters more than it looks: `paired_stats(pa, pb)` returns
mean_diff = mean(Brier(pb) - Brier(pa)), so a POSITIVE value means the FIRST arm is better.
Getting that backwards would turn "we lose to the market" into "we beat the market".
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sports.nba.model import formula as F

ROOT = Path(__file__).resolve().parents[3]
FORMULA = ROOT / "sports" / "nba" / "data" / "formula_v1.json"


@pytest.fixture(scope="module")
def formula() -> dict:
    if not FORMULA.exists():
        pytest.skip("formula artifact not built yet (python -m sports.nba.model.formula --fit)")
    return json.loads(FORMULA.read_text(encoding="utf-8"))


def _row(**overrides) -> dict:
    x = {name: 0.0 for name in F.FORMULA_FEATURES}
    x["hca"] = 60.0
    x.update(overrides)
    return {"x": x}


def test_formula_artifact_is_finite_and_complete(formula: dict) -> None:
    assert list(formula["features"]) == list(F.FORMULA_FEATURES)
    assert all(math.isfinite(v) for v in formula["coef_raw"].values())
    assert math.isfinite(formula["intercept_raw"])
    assert formula["n_train"] > 10_000, "formula must be fitted on the full training block"


def test_predict_is_bounded_and_monotone_in_elo(formula: dict) -> None:
    even = F.predict(formula, _row(elo_diff=0.0))
    strong = F.predict(formula, _row(elo_diff=200.0))
    assert 0.0 < even < 1.0 and 0.0 < strong < 1.0
    assert strong > even, "a 200-point Elo edge must raise the home win probability"
    assert strong > 0.70, "200 Elo points plus home court should be a clear favourite"


def test_predict_penalises_away_back_to_back(formula: dict) -> None:
    rested = F.predict(formula, _row(elo_diff=0.0, b2b_away=0.0))
    tired = F.predict(formula, _row(elo_diff=0.0, b2b_away=1.0))
    assert tired > rested, "an away team on a back-to-back must help the home team"


def test_predict_is_deterministic(formula: dict) -> None:
    a = F.predict(formula, _row(elo_diff=37.5))
    b = F.predict(formula, _row(elo_diff=37.5))
    assert a == b


def test_paired_stats_sign_convention() -> None:
    labels = {"a": 1, "b": 0, "c": 1}
    dates = {"a": "2020-01-01", "b": "2020-01-02", "c": "2020-01-03"}
    good = {"a": 0.9, "b": 0.1, "c": 0.8}
    bad = {"a": 0.5, "b": 0.5, "c": 0.5}
    assert F.paired_stats(good, bad, labels, list(labels), dates)["mean_diff"] > 0
    assert F.paired_stats(bad, good, labels, list(labels), dates)["mean_diff"] < 0
    assert F.paired_stats(good, good, labels, list(labels), dates)["mean_diff"] == 0


def test_tier_pass_requires_positive_ci() -> None:
    """A tier may only pass when the CI is entirely on the formula's side of zero."""
    labels, dates = {}, {}
    games = []
    for i in range(40):
        gid = f"g{i}"
        games.append(gid)
        labels[gid] = i % 2
        dates[gid] = f"2022-01-{i // 2 + 1:02d}"
    better = {g: (0.9 if labels[g] else 0.1) for g in games}
    worst = {g: (0.1 if labels[g] else 0.9) for g in games}
    st_better = F.paired_stats(better, worst, labels, games, dates)
    assert st_better["mean_diff"] > 0 and st_better["ci95_blocked"][0] > 0
    st_worse = F.paired_stats(worst, better, labels, games, dates)
    assert st_worse["mean_diff"] < 0 and st_worse["ci95_blocked"][1] < 0


def test_metric_sanity() -> None:
    assert F.brier(1.0, 1) == 0.0 and F.brier(0.0, 0) == 0.0
    assert F.brier(0.0, 1) == 1.0
    assert F.logloss(0.99, 1) < F.logloss(0.60, 1)
    # perfectly calibrated bin -> ~0 ECE; a confidently wrong bin -> ~0.4 ECE
    assert F.ece([(0.5, 1), (0.5, 0)]) == pytest.approx(0.0, abs=0.01)
    assert F.ece([(0.9, 0), (0.9, 1)]) == pytest.approx(0.4, abs=0.01)
    assert F.ece([(0.0, 0), (1.0, 1)]) == pytest.approx(0.0, abs=1e-9)


def test_arm_probs_are_probabilities() -> None:
    rows = [{"game_id": "g1", "season": "2021-22", "game_date": "2021-10-19", "home_win": 1,
             "x": {**{n: 0.0 for n in F.FORMULA_FEATURES}, "hca": 60.0, "elo_diff": 50.0,
                   "net_rtg_diff": 3.0}}]
    arms = F.arm_probs(rows, {"g1": 0.62})
    for name, probs in arms.items():
        assert probs, f"arm {name} produced no probabilities"
        for gid, p in probs.items():
            assert 0.0 < p < 1.0, f"{name}/{gid} produced {p}"
    assert arms["market_close"]["g1"] == 0.62
