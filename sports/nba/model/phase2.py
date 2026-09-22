"""Phase-2 arm admission gate (docs/preregistration.md §13).

An arm enters the Phase-2 freeze only if it beats A6 on the 2021-22 tune season
(strict improvement). The 2022-23..2025-26 window is burned for claims - an arm
may be re-scored there for diagnostics, but that score can never be its admission
argument. This module runs the tune gate ONLY.

Arms (each admitted under the registered rule; the previous arm's score is also
recorded as selection information):
   A7 = A6 feature set + player-impact absence values (v3 rows)
   A8 = A7 + schedule spots (v4 rows; the T3 candidate)
"""
from __future__ import annotations

import json
import sys

from sports.nba.db import build, paths
from sports.nba.features import impact as IM
from sports.nba.features import spots as SP
from sports.nba.model import formula as F

BASELINE_ARM = "A6"
BASELINE_VERSION = "v2"

# A7 = the formula's inputs + every availability key + signed impact absences.
A7_FEATURES = tuple(F.A6_FEATURES) + tuple(IM.IMPACT_KEYS)
# A8 = A7 + schedule spots (5-day travel, altitude venue, 4-in-5 / 3-in-4, runs).
A8_FEATURES = A7_FEATURES + tuple(SP.SPOTS_KEYS)

ARM_VERSIONS = {"A7": ("v3", A7_FEATURES), "A8": ("v4", A8_FEATURES)}


def gate_path(arm: str):
    """One frozen gate record per arm, so a later arm cannot clobber an earlier one."""
    return paths.DATA / f"nba_phase2_gate_{arm.lower()}.json"


def gate_verdict(a7_brier: float, a6_brier: float) -> str:
    """The registered rule (§13): enter iff strict improvement on the tune season."""
    return "enter" if a7_brier < a6_brier else "rejected"


def gate_json(arm: str, a7_brier: float, a6_brier: float, verdict: str,
              n_fit: int, n_tune: int, features,
              season_tune: str = F.TUNE_SEASON,
              prev_arm: str | None = None,
              prev_arm_brier: float | None = None,
              extra: dict | None = None) -> dict:
    rec = {
        "arm": arm,
        "gate_season": season_tune,
        "n_fit": n_fit,
        "n_tune": n_tune,
        "features": list(features),
        "baseline_arm": BASELINE_ARM,
        "baseline_brier": round(a6_brier, 5),
        "arm_brier": round(a7_brier, 5),
        "verdict": verdict,
        "burned_test_set": False,
        "claim_eligible": False,
    }
    if prev_arm is not None and prev_arm != BASELINE_ARM:
        rec["prev_arm"] = prev_arm
        rec["prev_arm_brier"] = round(prev_arm_brier, 5)
    if extra:
        rec.update(extra)
    return rec


def tune_brier(con, version: str, feature_names) -> tuple[float, int, int]:
    """Fit on seasons <= 2020-21, Brier on the 2021-22 tune season only."""
    rows = F.load_dataset(con, version=version, feature_names=tuple(feature_names))
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    formula = F.fit_formula(train, tuple(feature_names))
    brier = sum((F.predict(formula, r) - r["home_win"]) ** 2 for r in tune) / len(tune)
    return brier, len(train), len(tune)


def run_gate(con, arm: str, prev_arm: str | None = BASELINE_ARM) -> dict:
    version, features = ARM_VERSIONS[arm]
    arm_brier, n_fit, n_tune = tune_brier(con, version, features)
    base_brier, _, _ = tune_brier(con, BASELINE_VERSION, tuple(F.A6_FEATURES))
    prev_brier = base_brier
    if prev_arm is not None and prev_arm != BASELINE_ARM:
        prev_brier, _, _ = tune_brier(con, *ARM_VERSIONS[prev_arm])
    rec = gate_json(arm=arm, a7_brier=arm_brier, a6_brier=base_brier,
                    verdict=gate_verdict(arm_brier, base_brier),
                    n_fit=n_fit, n_tune=n_tune, features=features,
                    prev_arm=prev_arm, prev_arm_brier=prev_brier)
    gate_path(arm).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main() -> int:
    con = build.init(verbose=False)
    for arm, prev in (("A7", BASELINE_ARM), ("A8", "A7")):
        r = run_gate(con, arm=arm, prev_arm=prev)
        extra = f" (prev {r['prev_arm']} {r['prev_arm_brier']})" if "prev_arm" in r else ""
        print(f"{r['arm']} tune Brier {r['arm_brier']} vs {r['baseline_arm']} "
              f"{r['baseline_brier']}{extra} -> {r['verdict'].upper()}  -> {gate_path(arm).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
