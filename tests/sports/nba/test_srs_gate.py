"""Unit tests for the A11 (SRS team ratings) candidate gate. No gate run happens here -
the record is produced once by the operator, never by the test suite."""
from __future__ import annotations

import sqlite3

import pytest

from sports.nba.db import paths
from sports.nba.model import formula as F
from sports.nba.model import srs_gate as G


def test_a11_record_labels_and_verdicts():
    enter = G.gate_json(arm_brier=0.21570, base_brier=0.21591, prev_brier=0.21573,
                        n_fit=19085, n_tune=1230)
    assert enter["arm"] == "A11"
    assert enter["new_feature"] == "srs_diff"
    assert enter["feature_version"] == "v6"
    assert enter["burned_test_set"] is False and enter["claim_eligible"] is False
    assert "unregistered" in enter["candidate_status"]
    assert enter["verdict"] == "enter"
    rejected = G.gate_json(arm_brier=0.21600, base_brier=0.21591, prev_brier=0.21573,
                           n_fit=19085, n_tune=1230)
    assert rejected["verdict"] == "rejected"


def test_a11_windows_on_the_real_v6_rows():
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        rows = F.load_dataset(con, version="v6", feature_names=G.A11_FEATURES)
        n_v6 = con.execute(
            "SELECT COUNT(*) FROM features WHERE feature_version='v6'").fetchone()[0]
    finally:
        con.close()
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    assert n_v6 == 25258
    assert len(train) == 19085 and len(tune) == 1230
