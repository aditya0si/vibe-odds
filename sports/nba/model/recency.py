"""Phase-3 candidate: recency-weighted fitting (arm A10; plan R3, docs/phase3-candidates.md).

One variable against A7: the SAME feature set and the SAME transparent logistic, with the
fit weighted by game age:

    w_g = 0.5 ** (season_age_g / H)      age in seasons; w = 1 for the last fit-window season

The half-life H is chosen ONCE, ON THE FIT WINDOW ONLY, by 5-fold time-blocked CV over a
grid fixed a priori - H in (1, 2, 3, 5, 8, 16) seasons plus no-decay - minimising the mean
out-of-fold Brier; ties break toward LESS decay (conservative). The standardisation stays
unweighted (weights enter the logistic fit only). The gate then runs ONCE on the 2021-22
tune season under the registered rule (strict improvement over A6), exactly like A7/A8/A9.

NOT a registered arm. A10 is a Phase-3 candidate: nothing here may inform any claim until
a Phase-3 registration exists (docs/phase3-candidates.md).
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
GATE_PATH = paths.DATA / "nba_phase3_gate_a10.json"

HALF_LIFE_GRID = (1, 2, 3, 5, 8, 16, None)      # None = no decay (unweighted fit)
CV_FOLDS = 5


def season_weights(seasons: list[str], half_life, ref_season: str) -> list[float]:
    """w = 0.5 ** (season_age / H) per row; None -> all 1.0; w(ref_season) = 1."""
    if half_life is None:
        return [1.0] * len(seasons)
    idx = {s: i for i, s in enumerate(sorted(set(seasons)))}
    ref = idx[ref_season]
    return [0.5 ** ((ref - idx[s]) / half_life) for s in seasons]


def pick_half_life(table: dict):
    """Smallest mean CV Brier; ties break toward LESS decay (None = largest)."""
    def rank(h):
        return float("inf") if h is None else float(h)

    best = min(table.values())
    ok = [h for h, v in table.items() if v <= best + 1e-12]
    return max(ok, key=rank)


def choose_half_life(con) -> tuple[object, dict]:
    """5-fold time-blocked CV over the fit window (<= 2020-21) -> (chosen H, table)."""
    rows = F.load_dataset(con, version=A7_VERSION, feature_names=A7_FEATURES)
    fit_rows = [r for r in rows if r["season"] <= F.TRAIN_END]
    seasons = [r["season"] for r in fit_rows]
    n = len(fit_rows)
    bounds = [(k * n // CV_FOLDS, (k + 1) * n // CV_FOLDS) for k in range(CV_FOLDS)]
    table: dict = {}
    for h in HALF_LIFE_GRID:
        ws = season_weights(seasons, h, F.TRAIN_END)
        errs = []
        for lo, hi in bounds:
            train_idx = [i for i in range(n) if not (lo <= i < hi)]
            train = [fit_rows[i] for i in train_idx]
            w = [ws[i] for i in train_idx]
            formula = F.fit_formula(train, tuple(A7_FEATURES), sample_weight=w)
            errs.append(sum((F.predict(formula, r) - r["home_win"]) ** 2
                            for r in fit_rows[lo:hi]) / (hi - lo))
        table[h] = round(float(sum(errs) / len(errs)), 5)
    return pick_half_life(table), table


def gate_json(half_life, table: dict, arm_brier: float, base_brier: float, prev_brier: float,
              n_fit: int, n_tune: int) -> dict:
    return {
        "arm": "A10",
        "gate_season": F.TUNE_SEASON,
        "n_fit": n_fit,
        "n_tune": n_tune,
        "features": list(A7_FEATURES),
        "fit_weighting": "w = 0.5 ** (season_age / half_life); standardisation unweighted",
        "half_life_grid": [str(h) for h in HALF_LIFE_GRID],
        "half_life_cv_brier": {str(h): v for h, v in table.items()},
        "half_life_chosen": str(half_life),
        "baseline_arm": BASELINE_ARM,
        "baseline_brier": round(base_brier, 5),
        "arm_brier": round(arm_brier, 5),
        "verdict": "enter" if arm_brier < base_brier else "rejected",
        "prev_arm": "A7",
        "prev_arm_brier": round(prev_brier, 5),
        "burned_test_set": False,
        "claim_eligible": False,
        "candidate_status": CANDIDATE_STATUS,
        "note": ("half-life CV selected NO decay (the decay curve is monotone against it): "
                 "all weights are 1.0 and A10 is arithmetically identical to A7 - the "
                 "'enter' verdict re-states A7's entry, not a new admission."
                 if half_life is None else
                 "A10 = A7 features with the fit weighted by season age (half-life above); "
                 "standardisation unweighted."),
    }


def run_gate(con) -> dict:
    half_life, table = choose_half_life(con)
    rows = F.load_dataset(con, version=A7_VERSION, feature_names=A7_FEATURES)
    fit_rows = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    ws = season_weights([r["season"] for r in fit_rows], half_life, F.TRAIN_END)
    formula = F.fit_formula(fit_rows, tuple(A7_FEATURES), sample_weight=ws)
    arm_brier = sum((F.predict(formula, r) - r["home_win"]) ** 2 for r in tune) / len(tune)
    base_brier, _, _ = P3.tune_brier(con, BASELINE_VERSION, tuple(F.A6_FEATURES))
    prev_brier, _, _ = P3.tune_brier(con, A7_VERSION, tuple(A7_FEATURES))
    rec = gate_json(half_life, table, arm_brier, base_brier, prev_brier,
                    n_fit=len(fit_rows), n_tune=len(tune))
    GATE_PATH.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main() -> int:
    rec = run_gate(build.init(verbose=False))
    print(f"half-life chosen: {rec['half_life_chosen']}  cv: {rec['half_life_cv_brier']}")
    print(f"A10 tune Brier {rec['arm_brier']} vs A6 {rec['baseline_brier']} "
          f"(prev A7 {rec['prev_arm_brier']}) -> {rec['verdict'].upper()}")
    print(f"-> {GATE_PATH.name} (candidate record; not a claim)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
