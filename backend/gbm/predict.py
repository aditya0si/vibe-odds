"""Live GBM scoring: same columns as training, native SHAP via pred_contrib."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

DATA = Path(__file__).resolve().parents[2] / "data"

_MODEL = None
_META = None

HUMAN = {
    "elo_surf_diff": "surface Elo", "elo_overall_diff": "overall Elo",
    "h2h_diff": "head-to-head", "surf_h2h_diff": "surface H2H",
    "rank_diff": "ranking", "exp_diff": "experience",
    "win_rate_10_diff": "recent form", "first_won_diff": "1st-serve won",
    "second_won_diff": "2nd-serve won", "bp_saved_diff": "break points saved",
    "ace_rate_diff": "aces", "df_rate_diff": "double faults",
    "tb_rate_diff": "tiebreak record", "minutes_14d_diff": "recent court time",
    "grind_10_diff": "gruelling matches", "rest_days_diff": "rest",
    "markov_p": "point model", "serve_edge": "serve/return edge",
    "markov_p_fast": "point model (fast)", "serve_edge_fast": "serve edge (fast)",
    "return_state_diff": "return edge", "elo_fast_diff": "fast Elo",
    "s1_shrunk_diff": "1st-serve (shrunk)", "s2_shrunk_diff": "2nd-serve (shrunk)",
    "ace_shrunk_diff": "aces (shrunk)", "df_shrunk_diff": "DFs (shrunk)",
    "age_diff": "age",
    "best_of": "format", "level_mult": "event level", "round": "round",
}


def _load():
    global _MODEL, _META
    if _MODEL is None:
        _MODEL = lgb.Booster(model_file=str(DATA / "gbm.txt"))
        _META = json.loads((DATA / "gbm_features.json").read_text())
    return _MODEL, _META


def score_row(row: dict) -> dict:
    """row: feature dict (a-perspective). Returns prob + top SHAP drivers."""
    model, meta = _load()
    feats = meta["features"]
    df = pd.DataFrame([{k: row.get(k) for k in feats}])
    for c in meta["categorical"]:
        cats = meta.get("rounds" if c == "round" else "surfaces", [])
        df[c] = pd.Categorical(df[c], categories=cats)
    p = float(model.predict(df[feats])[0])
    contrib = model.predict(df[feats], pred_contrib=True)[0]
    names = model.feature_name()
    pairs = sorted(zip(names, contrib[:-1]), key=lambda x: -abs(x[1]))
    drivers = [{"feature": HUMAN.get(n, n), "push": round(float(v), 3)} for n, v in pairs[:4]]
    return {"p": p, "drivers": drivers}
