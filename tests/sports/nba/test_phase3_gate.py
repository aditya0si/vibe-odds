"""Unit tests for the Phase-3 candidate gate (A9). No gate run happens here - the
record is produced once by the operator, never by the test suite."""
from __future__ import annotations

import json
import sqlite3

import pytest

from sports.nba.db import paths
from sports.nba.features import player_value as PV
from sports.nba.model import formula as F
from sports.nba.model import phase2 as P2
from sports.nba.model import phase3 as P3


def test_a9_feature_families():
    assert P3.A9_FEATURES == tuple(F.FORMULA_FEATURES) + tuple(PV.PV_KEYS)
    assert P3.A9B_FEATURES == tuple(P2.A7_FEATURES) + tuple(PV.PV_KEYS)


def test_gate_verdict_is_strict():
    assert P3.gate_verdict(0.21570, 0.21591) == "enter"
    assert P3.gate_verdict(0.21591, 0.21591) == "rejected"     # ties do not enter
    assert P3.gate_verdict(0.21592, 0.21591) == "rejected"


def test_gate_record_is_dev_evidence_and_unregistered():
    rec = P3.gate_json(arm_brier=0.21570, base_brier=0.21591, prev_brier=0.21573,
                       n_fit=19085, n_tune=1230, n_excluded=33,
                       a9b=(0.21560, 19085, 1230))
    assert rec["arm"] == "A9"
    assert rec["burned_test_set"] is False
    assert rec["claim_eligible"] is False
    assert "unregistered" in rec["candidate_status"]
    assert rec["verdict"] == "enter"
    assert rec["baseline_arm"] == "A6" and rec["baseline_brier"] == 0.21591
    assert rec["prev_arm"] == "A7" and rec["prev_arm_brier"] == 0.21573
    assert rec["additive_variant"]["name"] == "A9b"
    assert rec["additive_variant"]["brier"] == 0.21560
    assert rec["feature_version"] == "v5"


def test_gate_windows_on_the_real_v5_rows():
    """v5 rows load, and the frozen split holds: fit <= 2020-21, tune 2021-22 only."""
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        rows = F.load_dataset(con, version="v5", feature_names=P3.A9_FEATURES)
    finally:
        con.close()
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    assert all(r["season"] <= "2020-21" for r in train)
    assert all(r["season"] == "2021-22" for r in tune)
    # same eligible set as A7 (identical publication rule): 19,085 fit / 1,230 tune
    assert len(train) == 19085
    assert len(tune) == 1230


def test_v5_rows_carry_only_their_own_family():
    """The A9b merge depends on v5 carrying ONLY its own keys: a v5 row with
    avail_*=None keys would overwrite v3's numeric values in {**base, **row}."""
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        v5 = json.loads(con.execute(
            "SELECT payload FROM features WHERE feature_version='v5' LIMIT 1").fetchone()["payload"])
        v3 = json.loads(con.execute(
            "SELECT payload FROM features WHERE feature_version='v3' LIMIT 1").fetchone()["payload"])
    finally:
        con.close()
    assert "avail_missing_home" not in v5
    assert "impact_out_home" not in v5
    assert "pv_missing_home" in v5
    assert "avail_missing_home" in v3 and "impact_out_home" in v3
