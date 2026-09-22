"""Phase-2 arm admission gate (pre-registered in docs/preregistration.md §13):
an arm enters the Phase-2 freeze only if it beats A6 on the 2021-22 tune season."""
from __future__ import annotations

from sports.nba.model.phase2 import gate_json, gate_verdict


def test_gate_accepts_only_strict_improvement_over_a6():
    assert gate_verdict(a7_brier=0.2099, a6_brier=0.2105) == "enter"
    assert gate_verdict(a7_brier=0.2105, a6_brier=0.2105) == "rejected"  # ties lose
    assert gate_verdict(a7_brier=0.2111, a6_brier=0.2105) == "rejected"


def test_gate_json_carries_the_freeze_contract():
    j = gate_json(arm="A7", a7_brier=0.2, a6_brier=0.21, verdict="enter",
                  n_fit=19100, n_tune=1230, features=["x"], season_tune="2021-22")
    # contract keys the Phase-2 freeze and the retro-label rule rely on
    for k in ("arm", "gate_season", "n_fit", "n_tune", "features",
              "baseline_arm", "baseline_brier", "arm_brier", "verdict",
              "burned_test_set", "claim_eligible"):
        assert k in j
    # the tune gate is DEVELOPMENT evidence: never claim-eligible, and the burned
    # test set (2022-23+) was not consulted
    assert j["claim_eligible"] is False
    assert j["burned_test_set"] is False
