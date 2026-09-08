"""LightGBM match-win model on the as-of feature store.

Splits are TIME-based (train <=2022, valid 2023, test >=2024) — never random.
The validation year is strictly before the 2024-25 sim window, so even
early-stopping leaks nothing the simulator will be judged on.
python -m backend.gbm.train [--no-save]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
PARQUET = DATA / "features.parquet"
MODEL_PATH = DATA / "gbm.txt"
META_PATH = DATA / "gbm_features.json"

CAT = ["surface", "round"]
DROP = ["date", "a", "b", "y"]

# W2 tournament column sets (preregistered). A = incumbent 27; B adds shrunk
# serve states + missingness + age; C adds opponent-adjusted return states;
# D adds fast timescales. WINNER (tournament verdict 2026-09-07): B.
A_COLS = ["surface", "best_of", "round", "level_mult", "elo_surf_diff", "elo_overall_diff",
     "h2h_diff", "h2h_total", "surf_h2h_diff", "rank_diff", "rank_missing", "exp_diff",
     "form_n_min", "serve_n_min", "win_rate_10_diff", "first_won_diff", "second_won_diff",
     "bp_saved_diff", "ace_rate_diff", "df_rate_diff", "tb_rate_diff", "minutes_14d_diff",
     "grind_10_diff", "rest_days_diff", "markov_p", "serve_edge", "pt_sample"]
B_ADD = ["s1_shrunk_diff", "s2_shrunk_diff", "ace_shrunk_diff", "df_shrunk_diff",
         "serve_missing", "age_diff"]
C_ADD = ["return_state_diff", "state_n_min"]
D_ADD = ["elo_fast_diff", "markov_p_fast", "serve_edge_fast"]
SETS = {"A": A_COLS, "B": A_COLS + B_ADD, "C": A_COLS + B_ADD + C_ADD,
        "D": A_COLS + B_ADD + C_ADD + D_ADD}
PROD_SET = "B"

# Monotonic constraints by domain knowledge (+1 favors a when the diff rises,
# -1 the reverse, 0 = let the data speak). Guards against GBM learning
# backwards artefacts on thin slices (e.g. "more double faults is good").
MONO = {"elo_surf_diff": 1, "elo_overall_diff": 1, "elo_fast_diff": 1,
        "h2h_diff": 1, "surf_h2h_diff": 1,
        "rank_diff": 1, "exp_diff": 1, "win_rate_10_diff": 1, "first_won_diff": 1,
        "second_won_diff": 1, "bp_saved_diff": 1, "ace_rate_diff": 1, "df_rate_diff": -1,
        "s1_shrunk_diff": 1, "s2_shrunk_diff": 1, "ace_shrunk_diff": 1, "df_shrunk_diff": -1,
        "tb_rate_diff": 1, "rest_days_diff": 1, "markov_p": 1, "serve_edge": 1,
        "markov_p_fast": 1, "serve_edge_fast": 1, "return_state_diff": 1}


def load_frame():
    import pandas as pd

    df = pd.read_parquet(PARQUET)
    feats = [c for c in df.columns if c not in DROP]
    for c in CAT:
        df[c] = df[c].astype("category")
    return df, feats


def splits(df):
    tr = df[df["date"] < 20230101]
    va = df[(df["date"] >= 20230101) & (df["date"] < 20240101)]
    te = df[df["date"] >= 20240101]
    return tr, va, te


def brier(ps, ys) -> float:
    import numpy as np

    ps = np.asarray(ps, dtype=float)
    ys = np.asarray(ys, dtype=float)
    return float(((ps - ys) ** 2).mean())


def _brier_feval(preds, ds):
    import numpy as np

    y = ds.get_label()
    return ("brier", float(((np.asarray(preds) - np.asarray(y)) ** 2).mean()), False)


def _fit(tr, va, feats, seed: int = 7):
    import lightgbm as lgb

    dtr = lgb.Dataset(tr[feats], label=tr["y"], categorical_feature=CAT)
    dva = lgb.Dataset(va[feats], label=va["y"], categorical_feature=CAT, reference=dtr)
    params = {"objective": "binary", "metric": "None", "verbosity": -1,
              "num_leaves": 63, "min_data_in_leaf": 150, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0,
              "monotone_constraints": [MONO.get(f, 0) for f in feats],
              "seed": seed, "deterministic": True}
    return lgb.train(params, dtr, num_boost_round=2000, valid_sets=[dva],
                     feval=_brier_feval,
                     callbacks=[lgb.early_stopping(100, verbose=False)])


def train(save: bool = True, verbose: bool = True, colset: str | None = None) -> dict:
    from backend.model.calibrate import ece

    df, feats = load_frame()
    if colset is not None:
        feats = list(SETS[colset])
    # Two time-folds: stability check that one lucky split can't fake.
    folds = {"A(train<=21|valid22)": (df[df["date"] < 20220101], df[(df["date"] >= 20220101) & (df["date"] < 20230101)]),
             "B(train<=22|valid23)": (df[df["date"] < 20230101], df[(df["date"] >= 20230101) & (df["date"] < 20240101)])}
    te = df[df["date"] >= 20240101]
    out = {}
    models = {}
    for name, (tr, va) in folds.items():
        models[name] = _fit(tr, va, feats)
        p = models[name].predict(te[feats])
        acc = float((((p >= 0.5).astype(int)) == te["y"].to_numpy()).mean())
        out[name] = {"n_train": len(tr), "n_valid": len(va),
                     "test_acc": round(acc, 4), "test_brier": round(brier(p, te["y"]), 4),
                     "test_ece": round(ece(list(p), list(te["y"])), 4),
                     "best_iter": models[name].best_iteration}
    model = models["B(train<=22|valid23)"]  # final: same window the sim freezes
    for name, part in (("train", folds["B(train<=22|valid23)"][0]),
                       ("valid", folds["B(train<=22|valid23)"][1]), ("test", te)):
        p = model.predict(part[feats])
        acc = float((((p >= 0.5).astype(int)) == part["y"].to_numpy()).mean())
        out[name] = {"n": len(part), "acc": round(acc, 4),
                     "brier": round(brier(p, part["y"]), 4),
                     "ece": round(ece(list(p), list(part["y"])), 4)}
    out["best_iter"] = model.best_iteration
    out["gain"] = {k: round(v, 1) for k, v in
                   sorted(zip(model.feature_name(), model.feature_importance("gain")),
                          key=lambda x: -x[1])[:12]}
    if save:
        model.save_model(str(MODEL_PATH))
        META_PATH.write_text(json.dumps({
            "features": feats, "categorical": CAT,
            "rounds": sorted(df["round"].dropna().unique().tolist()),
            "surfaces": sorted(df["surface"].dropna().unique().tolist())}))
    if verbose:
        print(json.dumps(out, indent=1))
        gate = out["test"]["brier"] < 0.215 and out["test"]["ece"] < 0.03
        print("GATE (brier<0.215 & ece<0.03):", "PASS" if gate else "REVIEW")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--set", default=None,
                    help="column set from SETS (default: all store columns; production: B)")
    a = ap.parse_args()
    train(save=not a.no_save, colset=a.set)
