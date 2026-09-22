"""Phase-2 nonlinear arm: monotone GBM + Hedge stack (plan Task 4).

Registered as the **T2g / T3g** tier (docs/preregistration.md §13): a SEPARATE
tier from the transparent-formula claims T2′/T2″/T3. It never carries the
transparent-formula claim.

Components (all market-free):
  formula       - the transparent weighted logistic on the A7 feature set
  gbm           - monotone-constrained LightGBM on the SAME A7 feature set
  elo_baseline  - the classic Elo+HCA logistic (market-free floor)

Per-game Hedge (core/ensemble.HedgeEnsemble, exponentiated-gradient) combines
them walk-forward: weights at game k reflect only games < k (update happens
after the prediction). Eta is the core default 0.1, fixed a priori - never
tuned on the gate season.

GBM splits (documented): early stopping is fit on (train <= 2019-20, valid =
2020-21), then the model is REFIT on <= 2020-21 at the chosen iteration count.
So the fit window is identical to the formula's (<= 2020-21) and the validation
window is strictly past-only. Determinism (double retrain -> byte-equal
predictions) is enforced by tests/sports/nba/test_nba_stack.py and pinned by
seed + single thread + no row/feature subsampling.

    python -m sports.nba.model.stack      # run the tune-2021-22 admission gate
"""
from __future__ import annotations

import json
import math
import sys

from core.ensemble import HedgeEnsemble
from sports.nba.db import build, paths
from sports.nba.model import formula as F
from sports.nba.model.phase2 import A7_FEATURES, BASELINE_ARM, gate_json, gate_verdict, tune_brier

ARM = "G1"
TIER = ["T2g", "T3g"]
TRAIN_END = F.TRAIN_END          # <= 2020-21: the fit window (formula's)
VALID_SEASON = "2020-21"         # early-stopping window (past-only; inside fit)
EARLY_TRAIN_END = "2019-20"      # early-stopping train part
TUNE = F.TUNE_SEASON

# Monotone directions (home-win probability vs each feature). +1 = more of this
# feature never lowers P(home win). is_neutral is left unconstrained (the frozen
# A5 coefficient is exactly 0.000000; no direction is defensible a priori).
MONO = {
    "elo_diff": +1, "hca": +1, "form_margin_diff": +1, "net_rtg_diff": +1,
    "rest_diff": +1, "b2b_away": +1, "is_neutral": 0,
    "avail_missing_home": -1, "avail_missing_away": +1, "avail_diff": +1,
    "avail_top_out_home": -1, "avail_top_out_away": +1,
    "avail_n_inactive_home": -1, "avail_n_inactive_away": +1,
    "impact_out_home": -1, "impact_out_away": +1, "impact_out_diff": -1,
}

# The NBA GBM literal (kept here, like tennis keeps backend/gbm/*). Everything
# that could inject randomness is pinned: seed, one thread, no subsampling.
GBM_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 100,
    "feature_fraction": 1.0,
    "bagging_fraction": 1.0,
    "bagging_freq": 0,
    "seed": 7,
    "num_threads": 1,
    "deterministic": True,
    "force_row_wise": True,
    "verbosity": -1,
}

ETA = 0.1                      # core default, fixed a priori (never gate-tuned)
SIGNALS = ("formula", "gbm", "elo_baseline")


def mono_vector(feats, mono_map: dict | None = None) -> list[int]:
    """The monotone constraint vector; a feature without a registered sign fails loudly."""
    m = MONO if mono_map is None else mono_map
    missing = [f for f in feats if f not in m]
    if missing:
        raise KeyError(f"monotone map is missing signs for: {missing}")
    return [m[f] for f in feats]


def _frame(rows: list[dict], feats) -> "object":   # pandas.DataFrame
    import pandas as pd
    return pd.DataFrame([{**{f: r["x"][f] for f in feats}, "y": r["home_win"]} for r in rows])


def fit_gbm(train_rows: list[dict], valid_rows: list[dict], feats=A7_FEATURES,
            mono_map: dict | None = None):
    """Deterministic monotone GBM: early-stop on a past-only window, refit on the
    full fit window at the chosen iteration count. Returns the final booster."""
    import lightgbm as lgb
    from core.boosted import fit_binary

    feats = list(feats)
    tr, va = _frame(train_rows, feats), _frame(valid_rows, feats)
    params = dict(GBM_PARAMS, monotone_constraints=mono_vector(feats, mono_map))
    first = fit_binary(tr, va, feats, cat=[], params=params)
    n = int(getattr(first, "best_iteration", 0) or 0) or 200
    full = _frame(list(train_rows) + list(valid_rows), feats)
    return lgb.train(params, lgb.Dataset(full[feats], label=full["y"]), num_boost_round=n)


def gbm_probs(model, rows: list[dict], feats=A7_FEATURES) -> list[float]:
    import pandas as pd
    feats = list(feats)
    df = pd.DataFrame([{f: r["x"][f] for f in feats} for r in rows])
    return [float(p) for p in model.predict(df[feats])]


def elo_baseline_probs(rows: list[dict]) -> list[float]:
    """The market-free floor: Elo + HCA logistic (formula.arm_probs' elo_only)."""
    return [1.0 / (1.0 + 10 ** (-(r["x"]["elo_diff"] + r["x"]["hca"]) / 400.0)) for r in rows]


def new_hedge() -> HedgeEnsemble:
    return HedgeEnsemble(weights={s: 1 / 3 for s in SIGNALS}, eta=ETA, eta_by={})


def stack_probs(rows: list[dict], feats=A7_FEATURES, gbm=None, formula: dict | None = None,
                hedge: HedgeEnsemble | None = None, surface: str = "ml"):
    """Walk-forward stack: for every row in order, predict with current Hedge
    weights, THEN update on the settled result. Weights at game k only ever see
    games < k. Returns (records, hedge)."""
    gbm = gbm if gbm is not None else fit_gbm(
        [r for r in rows if r["season"] <= EARLY_TRAIN_END],
        [r for r in rows if r["season"] == VALID_SEASON], feats)
    if formula is None:
        formula = F.fit_formula([r for r in rows if r["season"] <= TRAIN_END], tuple(feats))
    hedge = hedge if hedge is not None else new_hedge()
    pf = [F.predict(formula, r) for r in rows]
    pg = gbm_probs(gbm, rows, feats)
    pe = elo_baseline_probs(rows)
    out = []
    for r, a, b, c in zip(rows, pf, pg, pe):
        probs = {"formula": a, "gbm": b, "elo_baseline": c}
        p = hedge.predict(probs, surface=surface)["p_a"]
        out.append({"game_id": r["game_id"], "season": r["season"],
                    "y": r["home_win"], "p_stack": p, **{f"p_{k}": v for k, v in probs.items()}})
        hedge.update(probs, winner_is_a=bool(r["home_win"]), surface=surface)
    return out, hedge


def _brier(recs: list[dict], key: str) -> float:
    return sum((r[key] - r["y"]) ** 2 for r in recs) / len(recs)


def run_gate(con) -> dict:
    """The registered admission rule (§13): beat A6 on the 2021-22 tune season.
    The stack's tune Brier is the arm's; component Briers are recorded as extras.

    Stack walk = COLD START over the tune season (predict-then-update on settled
    games only - exactly the prospective 2026-27 design). Superseded first run:
    a warm-up walk over <=2020-21 corrupted the Hedge weights (component
    predictions there are in-sample - the refit GBM scores its own training rows),
    producing 0.21721 with weights gbm 0.9286 / formula 0.0509 despite the GBM
    losing to the formula on tune. Both runs are recorded here: no number is hidden.
    """
    rows = F.load_dataset(con, version="v3", feature_names=A7_FEATURES)
    fit_rows = [r for r in rows if r["season"] <= TRAIN_END]
    tune = [r for r in rows if r["season"] == TUNE]
    formula = F.fit_formula(fit_rows, tuple(A7_FEATURES))
    gbm = fit_gbm([r for r in rows if r["season"] <= EARLY_TRAIN_END],
                  [r for r in rows if r["season"] == VALID_SEASON])
    recs, hedge = stack_probs(tune, feats=A7_FEATURES, gbm=gbm, formula=formula)
    arm_brier = _brier(recs, "p_stack")
    base_brier, _, _ = tune_brier(con, "v2", tuple(F.A6_FEATURES))
    extra = {
        "tiers": TIER,
        "note": "nonlinear arm: monotone GBM + Hedge stack over {formula, gbm, elo_baseline}; "
                "registered as the separate T2g/T3g tier (never carries the transparent-formula claim)",
        "hedge_walk": "cold start over the tune season; predict-then-update on settled games only (past-only)",
        "components_tune_brier": {k: round(_brier(recs, f"p_{k}"), 5) for k in SIGNALS},
        "hedge_weights_final": {k: round(v, 4) for k, v in hedge.table("ml").items()},
        "gbm": {"early_train_end": EARLY_TRAIN_END, "valid_season": VALID_SEASON,
                "refit_end": TRAIN_END, "eta": ETA},
        "superseded_runs": [{
            "arm_brier": 0.21721, "verdict": "rejected",
            "defect": "hedge warm-up walked <=2020-21 rows whose component predictions were "
                      "in-sample (the refit GBM scores its own training rows); weights locked "
                      "onto the overfit signal (gbm 0.9286 / formula 0.0509 despite the GBM "
                      "losing to the formula on tune)",
            "fix": "cold-start predict-then-update walk over the tune season only"}],
    }
    rec = gate_json(arm=ARM, a7_brier=arm_brier, a6_brier=base_brier,
                    verdict=gate_verdict(arm_brier, base_brier),
                    n_fit=len([r for r in rows if r["season"] <= TRAIN_END]),
                    n_tune=len(tune), features=A7_FEATURES, extra=extra)
    path = paths.DATA / f"nba_phase2_gate_{ARM.lower()}.json"
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(f"{ARM} stack tune Brier {rec['arm_brier']} vs {BASELINE_ARM} {rec['baseline_brier']} "
          f"-> {rec['verdict'].upper()}  -> {path.name}")
    print(f"  components: {extra['components_tune_brier']}  hedge: {extra['hedge_weights_final']}")
    return rec


def main() -> int:
    con = build.init(verbose=False)
    run_gate(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
