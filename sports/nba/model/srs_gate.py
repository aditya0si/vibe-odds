"""Phase-3 candidate: A11 (schedule-adjusted team ratings; features v6; plan R3).

One variable against A7: the same transparent logistic on A7's features plus ONE new
feature - ``srs_diff``, the as-of opponent-adjusted team rating differential from
features/team_ratings.py (a team-level ridge on prior seasons' margins; expanding
window, no decay, per the A10 result). Fit <= 2020-21, scored ONCE on 2021-22 under
the registered rule (enter iff strict improvement over A6), like every other arm.

Expected near-zero delta: Elo already carries much of the team-strength signal - a
null here is a REDUNDANCY finding, not a failure, and the note in the record says so.

NOT a registered arm. A11 is a Phase-3 candidate: nothing here may inform any claim
until a Phase-3 registration exists (docs/phase3-candidates.md).
"""
from __future__ import annotations

import json
import sys

from sports.nba.db import build, paths
from sports.nba.model import formula as F
from sports.nba.model import phase3 as P3
from sports.nba.model.phase2 import A7_FEATURES
from sports.nba.model.phase3 import CANDIDATE_STATUS

BASELINE_ARM = "A6"
BASELINE_VERSION = "v2"
A7_VERSION = "v3"
SRS_VERSION = "v6"
GATE_PATH = paths.DATA / "nba_phase3_gate_a11.json"

A11_FEATURES = tuple(A7_FEATURES) + ("srs_diff",)


def tune_brier_srs(con) -> tuple[float, int, int]:
    """Fit on seasons <= 2020-21, Brier on the 2021-22 tune season only."""
    rows = F.load_dataset(con, version=SRS_VERSION, feature_names=A11_FEATURES)
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    formula = F.fit_formula(train, A11_FEATURES)
    brier = sum((F.predict(formula, r) - r["home_win"]) ** 2 for r in tune) / len(tune)
    return brier, len(train), len(tune)


def gate_json(arm_brier: float, base_brier: float, prev_brier: float,
              n_fit: int, n_tune: int) -> dict:
    return {
        "arm": "A11",
        "gate_season": F.TUNE_SEASON,
        "n_fit": n_fit,
        "n_tune": n_tune,
        "features": list(A11_FEATURES),
        "new_feature": "srs_diff",
        "feature_version": SRS_VERSION,
        "feature_evidence": "features_v6_evidence.json",
        "baseline_arm": BASELINE_ARM,
        "baseline_brier": round(base_brier, 5),
        "arm_brier": round(arm_brier, 5),
        "verdict": "enter" if arm_brier < base_brier else "rejected",
        "prev_arm": "A7",
        "prev_arm_brier": round(prev_brier, 5),
        "burned_test_set": False,
        "claim_eligible": False,
        "candidate_status": CANDIDATE_STATUS,
        "note": ("One variable against A7: +srs_diff (as-of opponent-adjusted team rating). "
                 "Elo already carries much of this signal - a near-zero delta is a "
                 "redundancy finding, not a failure."),
    }


def run_gate(con) -> dict:
    arm_brier, n_fit, n_tune = tune_brier_srs(con)
    base_brier, _, _ = P3.tune_brier(con, BASELINE_VERSION, tuple(F.A6_FEATURES))
    prev_brier, _, _ = P3.tune_brier(con, A7_VERSION, tuple(A7_FEATURES))
    rec = gate_json(arm_brier, base_brier, prev_brier, n_fit, n_tune)
    GATE_PATH.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main() -> int:
    rec = run_gate(build.init(verbose=False))
    print(f"A11 tune Brier {rec['arm_brier']} vs A6 {rec['baseline_brier']} "
          f"(prev A7 {rec['prev_arm_brier']}) -> {rec['verdict'].upper()}")
    print(f"-> {GATE_PATH.name} (candidate record; not a claim)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
