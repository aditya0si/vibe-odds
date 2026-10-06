"""Phase-3 candidate gate: arm A9 (player-value availability, features v5).

NOT a registered arm. A9 is a Phase-3 CANDIDATE: this module runs the same
tune-only admission gate the Phase-2 arms went through (docs/preregistration.md
13 - fit <= 2020-21, scored on 2021-22, enter iff strict improvement over A6)
and records the outcome in nba_phase3_gate_a9.json. Nothing here may inform any
claim until a Phase-3 registration exists (docs/phase3-candidates.md).

Arms:
  A9  = the formula's inputs + the pv_* player-value family (v5 rows) - the
        primary candidate; it REPLACES the avail_*/impact_* family.
  A9b = A7's inputs + pv_* (v3 payload + v5 keys merged) - an additive
        diagnostic, reported in the record but never gating.

The tune gate is development evidence: burned_test_set=false (2021-22 is a tune
season, not the burned window), claim_eligible=false.
"""
from __future__ import annotations

import json
import sys

from sports.nba.db import build, paths
from sports.nba.features import player_value as PV
from sports.nba.model import formula as F
from sports.nba.model.phase2 import A7_FEATURES

BASELINE_ARM = "A6"
BASELINE_VERSION = "v2"
A7_VERSION = "v3"

# A9 = the formula's inputs + every player-value key (the whole absence family replaced).
A9_FEATURES = tuple(F.FORMULA_FEATURES) + tuple(PV.PV_KEYS)
# A9b = A7's inputs + the player-value family (additive; informational only).
A9B_FEATURES = tuple(A7_FEATURES) + tuple(PV.PV_KEYS)

GATE_PATH = paths.DATA / "nba_phase3_gate_a9.json"
CANDIDATE_STATUS = ("Phase-3 candidate - unregistered. May not inform any claim until a "
                    "Phase-3 registration exists (docs/phase3-candidates.md).")


def gate_verdict(arm_brier: float, baseline_brier: float) -> str:
    """The registered rule (13): enter iff strict improvement on the tune season."""
    return "enter" if arm_brier < baseline_brier else "rejected"


def tune_brier(con, version: str, feature_names) -> tuple[float, int, int]:
    """Fit on seasons <= 2020-21, Brier on the 2021-22 tune season only."""
    rows = F.load_dataset(con, version=version, feature_names=tuple(feature_names))
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    formula = F.fit_formula(train, tuple(feature_names))
    brier = sum((F.predict(formula, r) - r["home_win"]) ** 2 for r in tune) / len(tune)
    return brier, len(train), len(tune)


def load_merged_dataset(con, feature_names) -> list[dict]:
    """v3 payload + v5 pv_* keys merged per game (the additive-variant input)."""
    v3: dict[str, dict] = {}
    for r in con.execute("SELECT payload FROM features WHERE feature_version='v3'"):
        row = json.loads(r["payload"])
        v3[row["game_id"]] = row
    out: list[dict] = []
    for r in con.execute("SELECT payload FROM features WHERE feature_version='v5'"):
        row = json.loads(r["payload"])
        base = v3.get(row["game_id"])
        if base is None:
            continue
        merged = {**base, **row}
        merged["x"] = {name: F._feat(merged, name) for name in feature_names}
        if any(v is None for v in merged["x"].values()):
            continue
        out.append(merged)
    out.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return out


def tune_brier_merged(con, feature_names) -> tuple[float, int, int]:
    rows = load_merged_dataset(con, feature_names)
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    formula = F.fit_formula(train, tuple(feature_names))
    brier = sum((F.predict(formula, r) - r["home_win"]) ** 2 for r in tune) / len(tune)
    return brier, len(train), len(tune)


def gate_json(arm_brier: float, base_brier: float, prev_brier: float,
              n_fit: int, n_tune: int, n_excluded: int,
              a9b: tuple[float, int, int]) -> dict:
    a9b_brier, a9b_fit, a9b_tune = a9b
    return {
        "arm": "A9",
        "gate_season": F.TUNE_SEASON,
        "n_fit": n_fit,
        "n_tune": n_tune,
        "n_excluded_rows": n_excluded,
        "features": list(A9_FEATURES),
        "baseline_arm": BASELINE_ARM,
        "baseline_brier": round(base_brier, 5),
        "arm_brier": round(arm_brier, 5),
        "verdict": gate_verdict(arm_brier, base_brier),
        "prev_arm": "A7",
        "prev_arm_brier": round(prev_brier, 5),
        "feature_version": "v5",
        "feature_evidence": "features_v5_evidence.json",
        "additive_variant": {
            "name": "A9b",
            "features": list(A9B_FEATURES),
            "n_fit": a9b_fit,
            "n_tune": a9b_tune,
            "brier": round(a9b_brier, 5),
            "note": "informational: does the pv family add to, or replace, avail_*/impact_*? "
                    "Never gates.",
        },
        "burned_test_set": False,
        "claim_eligible": False,
        "candidate_status": CANDIDATE_STATUS,
    }


def run_gate(con) -> dict:
    a9_brier, n_fit, n_tune = tune_brier(con, "v5", A9_FEATURES)
    base_brier, _, _ = tune_brier(con, BASELINE_VERSION, tuple(F.A6_FEATURES))
    prev_brier, _, _ = tune_brier(con, A7_VERSION, tuple(A7_FEATURES))
    a9b = tune_brier_merged(con, A9B_FEATURES)
    total_pre = con.execute(
        "SELECT COUNT(*) FROM features f JOIN games g ON g.game_id = f.game_id "
        "WHERE f.feature_version='v5' AND g.season <= ?",
        (F.TUNE_SEASON,)).fetchone()[0]
    rec = gate_json(arm_brier=a9_brier, base_brier=base_brier, prev_brier=prev_brier,
                    n_fit=n_fit, n_tune=n_tune, n_excluded=int(total_pre) - n_fit - n_tune,
                    a9b=a9b)
    GATE_PATH.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main() -> int:
    rec = run_gate(build.init(verbose=False))
    a9b = rec["additive_variant"]
    print(f"A9  tune Brier {rec['arm_brier']} vs A6 {rec['baseline_brier']} "
          f"(prev A7 {rec['prev_arm_brier']}) -> {rec['verdict'].upper()}")
    print(f"A9b (additive, informational) tune Brier {a9b['brier']}")
    print(f"-> {GATE_PATH.name} (candidate record; not a claim)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
