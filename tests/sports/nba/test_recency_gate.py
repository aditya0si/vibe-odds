"""Unit tests for the A10 recency-weighted candidate gate. No gate run happens here -
the record is produced once by the operator, never by the test suite."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from sports.nba.db import paths
from sports.nba.model import formula as F
from sports.nba.model import recency as R


def test_season_weights_shape_and_values():
    seasons = ["2018-19", "2019-20", "2020-21", "2018-19"]
    w = R.season_weights(seasons, 2, "2020-21")
    assert w[2] == 1.0                                  # ref season
    assert w[1] == pytest.approx(0.5 ** 0.5)            # one season back, H=2
    assert w[0] == pytest.approx(0.5 ** 1.0)            # two seasons back, H=2
    assert w[0] == w[3]                                 # same season -> same weight


def test_season_weights_none_is_unweighted():
    assert R.season_weights(["2005-06", "2020-21"], None, "2020-21") == [1.0, 1.0]


def test_pick_half_life_ties_break_toward_less_decay():
    assert R.pick_half_life({1: 0.5, 2: 0.5, None: 0.6}) == 2
    assert R.pick_half_life({1: 0.4, None: 0.4}) is None
    assert R.pick_half_life({1: 0.6, 5: 0.5, 8: 0.51}) == 5


def test_sample_weight_moves_the_fit_toward_the_weighted_region():
    """Weights zero on the noise half must pull the fit toward the clean half."""
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(400):
        x = float(rng.normal())
        rows.append({"x": {"a": x}, "home_win": int(x > 0)})
    for _ in range(400):
        x = float(rng.normal())
        rows.append({"x": {"a": x}, "home_win": int(rng.random() < 0.5)})
    clean = F.fit_formula(rows[:400], ("a",))
    plain = F.fit_formula(rows, ("a",))
    weighted = F.fit_formula(rows, ("a",), sample_weight=[1.0] * 400 + [0.0] * 400)
    ca = clean["coef_raw"]["a"]
    assert ca > 0
    assert abs(weighted["coef_raw"]["a"] - ca) < abs(plain["coef_raw"]["a"] - ca)
    assert weighted["coef_raw"]["a"] > 0.8 * ca         # close to the clean fit


def test_a10_record_labels_and_shape():
    rec = R.gate_json(2, {1: 0.21000, 2: 0.20000, None: 0.22000},
                      arm_brier=0.21570, base_brier=0.21591, prev_brier=0.21573,
                      n_fit=19085, n_tune=1230)
    assert rec["arm"] == "A10"
    assert rec["burned_test_set"] is False and rec["claim_eligible"] is False
    assert "unregistered" in rec["candidate_status"]
    assert rec["half_life_chosen"] == "2"
    assert rec["verdict"] == "enter"
    assert rec["prev_arm"] == "A7"


def test_a10_windows_on_the_real_v3_rows():
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        rows = F.load_dataset(con, version="v3", feature_names=R.A7_FEATURES)
    finally:
        con.close()
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    assert len(train) == 19085 and len(tune) == 1230
