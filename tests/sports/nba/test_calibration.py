"""Calibration bake-off tests (Step 4).

Three properties, none of which trust prose:

  * the calibrator fit set is the frozen tune season (2021-22) **only**;
  * the locked decision rule is an executable function and behaves correctly on
    synthetic tables (strictly-lower-Brier / not-worse-ECE, both ways);
  * the shipped variant recorded in ``nba_calibration_v1.json`` is what the rule
    returns for the artifact's own tune numbers.

The artifact writers are exercised by ``python -m sports.nba.model.formula
--calibrate``; these tests only read the frozen artifact and call the helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sports.nba.db import paths
from sports.nba.model import formula as F

ROOT = Path(__file__).resolve().parents[3]
NBA_DATA = ROOT / "sports" / "nba" / "data"
ARTIFACT = NBA_DATA / "nba_calibration_v1.json"

DISCLAIMER = ("calibration decision made on the tune season only; "
              "test evaluation is this arm's single evaluation")


def _artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _rule_metrics(arm: str) -> dict:
    bake = _artifact()["bakeoff_tune"][arm]
    return {v: {"brier": bake[v]["brier"], "ece": bake[v]["ece"]} for v in F.CALIBRATION_VARIANTS}


# ---- 1. the fit set is the tune season only ---------------------------------

def test_calibration_constants_and_disclaimer() -> None:
    assert F.CALIBRATION_VARIANTS == ("raw", "platt", "isotonic")
    assert F.CALIBRATION_DISCLAIMER == DISCLAIMER
    assert _artifact()["disclaimer"] == DISCLAIMER


def test_calibrator_fit_rows_are_tune_only() -> None:
    rows = [
        {"season": F.TRAIN_END, "game_id": "train-1"},
        {"season": F.TUNE_SEASON, "game_id": "tune-1"},
        {"season": F.TUNE_SEASON, "game_id": "tune-2"},
        {"season": F.TEST_SEASONS[0], "game_id": "test-1"},
    ]
    fit = F.calibrator_fit_rows(rows)
    assert {r["season"] for r in fit} == {F.TUNE_SEASON}
    assert {r["game_id"] for r in fit} == {"tune-1", "tune-2"}


def test_calibrator_fit_pairs_exclude_non_tune() -> None:
    rows = [
        {"season": F.TRAIN_END, "game_id": "train-1"},
        {"season": F.TUNE_SEASON, "game_id": "tune-1"},
        {"season": F.TEST_SEASONS[0], "game_id": "test-1"},
    ]
    probs = {"train-1": 0.9, "tune-1": 0.5, "test-1": 0.1}
    labels = {"train-1": 1, "tune-1": 0, "test-1": 1}
    ps, ys = F.calibrator_fit_pairs(rows, probs, labels)
    assert ps == [0.5]
    assert ys == [0]


def test_fit_ignores_non_tune_rows() -> None:
    """Perturbing every non-tune row must not move the fitted calibrators."""
    rows = [
        {"season": F.TUNE_SEASON, "game_id": "a"},
        {"season": F.TUNE_SEASON, "game_id": "b"},
        {"season": F.TEST_SEASONS[0], "game_id": "x"},
    ]
    probs = {"a": 0.6, "b": 0.4, "x": 0.5}
    labels = {"a": 1, "b": 0, "x": 0}
    ps, ys = F.calibrator_fit_pairs(rows, probs, labels)
    first = F.fit_calibrator_models(ps, ys)
    perturbed_probs = dict(probs, x=0.999)
    perturbed_labels = dict(labels, x=1)
    ps2, ys2 = F.calibrator_fit_pairs(rows, perturbed_probs, perturbed_labels)
    second = F.fit_calibrator_models(ps2, ys2)
    assert first == second


def test_apply_calibrator_raw_is_identity() -> None:
    model = F.fit_calibrator_models([0.2, 0.4, 0.6, 0.8], [0, 0, 1, 1])
    for p in (0.05, 0.5, 0.95):
        assert F.apply_calibrator(model["raw"], p) == p


# ---- 2. the decision rule is executable and behaves -------------------------

def test_decide_rule_ships_platt_when_both_calibrators_qualify() -> None:
    metrics = {"raw": {"brier": 0.22, "ece": 0.03},
               "platt": {"brier": 0.21, "ece": 0.03},
               "isotonic": {"brier": 0.20, "ece": 0.01}}
    assert F.decide_calibrator(metrics) == "platt"


def test_decide_rule_requires_strictly_lower_brier() -> None:
    metrics = {"raw": {"brier": 0.22, "ece": 0.03},
               "platt": {"brier": 0.22, "ece": 0.02},
               "isotonic": {"brier": 0.22, "ece": 0.0}}
    assert F.decide_calibrator(metrics) == "raw"


def test_decide_rule_rejects_a_calibrator_with_worse_ece() -> None:
    metrics = {"raw": {"brier": 0.22, "ece": 0.03},
               "platt": {"brier": 0.21, "ece": 0.04},
               "isotonic": {"brier": 0.215, "ece": 0.031}}
    assert F.decide_calibrator(metrics) == "raw"


def test_decide_rule_ships_isotonic_when_only_qualifier() -> None:
    metrics = {"raw": {"brier": 0.22, "ece": 0.03},
               "platt": {"brier": 0.23, "ece": 0.01},
               "isotonic": {"brier": 0.21, "ece": 0.02}}
    assert F.decide_calibrator(metrics) == "isotonic"


def test_decide_rule_accepts_equal_ece() -> None:
    """'not worse' is <=, so an equal-ECE calibrator still qualifies."""
    metrics = {"raw": {"brier": 0.22, "ece": 0.03},
               "platt": {"brier": 0.21, "ece": 0.03},
               "isotonic": {"brier": 0.20, "ece": 0.01}}
    assert F.decide_calibrator(metrics) == "platt"


# ---- 3. the artifact's decision is what the rule returns --------------------

def test_artifact_decision_matches_the_rule() -> None:
    art = _artifact()
    for arm in ("A5", "A6"):
        assert art["decision"][arm]["shipped"] == F.decide_calibrator(_rule_metrics(arm)), (
            f"{arm}: artifact decision disagrees with the locked rule applied to its "
            "own tune numbers"
        )


def test_artifact_bakeoff_is_the_six_way_table() -> None:
    art = _artifact()
    for arm in ("A5", "A6"):
        entry = art["bakeoff_tune"][arm]
        assert entry["n"] > 0
        for variant in F.CALIBRATION_VARIANTS:
            assert variant in entry, f"{arm}: missing {variant}"
            for key in ("brier", "logloss", "ece"):
                assert key in entry[variant], f"{arm}/{variant}: missing {key}"


def test_artifact_labels_test_evaluation_as_single_and_shipped() -> None:
    art = _artifact()
    assert art["registered_claims_untouched"].endswith("nba_walkforward_v1.json")
    for arm in ("A5", "A6"):
        test = art["test_evaluation"][arm]
        assert test["shipped"] == art["decision"][arm]["shipped"]
        assert test["shipped_metrics"]["n"] == test["raw"]["n"]
        assert set(test["paired_tests"]) == {
            "shipped_vs_raw", "shipped_vs_market_open", "shipped_vs_market_close"}


def test_artifact_shipped_calibrator_matches_variant() -> None:
    art = _artifact()
    for arm in ("A5", "A6"):
        test = art["test_evaluation"][arm]
        assert test["shipped_calibrator"]["kind"] == test["shipped"]


# ---- 4. DB-backed: the real dataset also fits on the tune season only -------

@pytest.fixture(scope="module")
def a5_dataset():
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    import sqlite3
    con = sqlite3.connect(paths.DB, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        rows = F.load_dataset(con)
    finally:
        con.close()
    if not rows:
        pytest.skip("feature rows not built")
    return rows


def test_real_calibrator_fit_set_is_2021_22_only(a5_dataset) -> None:
    probs = {r["game_id"]: F.predict(json.loads((NBA_DATA / "formula_v1.json")
                                                 .read_text(encoding="utf-8")), r)
             for r in a5_dataset}
    labels = {r["game_id"]: r["home_win"] for r in a5_dataset}
    fit_rows = F.calibrator_fit_rows(a5_dataset)
    assert {r["season"] for r in fit_rows} == {F.TUNE_SEASON}
    ps, ys = F.calibrator_fit_pairs(a5_dataset, probs, labels)
    assert len(ps) == len(fit_rows)
    assert len(ps) == _artifact()["row_counts"]["A5"]["tune"]
