"""GBM core: fold windows, determinism, the recorded SETS literal (map step 9)."""

from __future__ import annotations

import numpy as np

from core.boosted import brier, time_folds

# The preregistered W2 winner column list (map R3: this literal must never drift).
RECORDED_SETS_B = [
    "surface", "best_of", "round", "level_mult", "elo_surf_diff", "elo_overall_diff",
    "h2h_diff", "h2h_total", "surf_h2h_diff", "rank_diff", "rank_missing", "exp_diff",
    "form_n_min", "serve_n_min", "win_rate_10_diff", "first_won_diff", "second_won_diff",
    "bp_saved_diff", "ace_rate_diff", "df_rate_diff", "tb_rate_diff", "minutes_14d_diff",
    "grind_10_diff", "rest_days_diff", "markov_p", "serve_edge", "pt_sample",
    "s1_shrunk_diff", "s2_shrunk_diff", "ace_shrunk_diff", "df_shrunk_diff",
    "serve_missing", "age_diff",
]


def test_sets_b_literal_equals_recorded_list():
    """Map R3: column order IS the monotone-constraint vector. Never drift."""
    from backend.gbm.train import A_COLS, B_ADD, CAT, MONO, SETS
    assert SETS["B"] == A_COLS + B_ADD == RECORDED_SETS_B
    assert CAT == ["surface", "round"]
    assert MONO["df_rate_diff"] == -1 and MONO["elo_surf_diff"] == 1


def test_time_folds_windows():
    import pandas as pd
    df = pd.DataFrame({"date": [20210101, 20220601, 20230601, 20240601, 20250601],
                       "y": [1, 0, 1, 0, 1]})
    (t, tr, va, te), = time_folds(df, [2024])
    assert t == 2024
    assert list(tr["date"]) == [20210101, 20220601]   # < 2023
    assert list(va["date"]) == [20230601]              # 2023
    assert list(te["date"]) == [20240601]              # 2024 only (bounded)


def test_fit_binary_is_deterministic():
    import pandas as pd
    from core.boosted import fit_binary
    rng = np.random.default_rng(7)
    n = 400
    df = pd.DataFrame({"x": rng.normal(size=n), "y": rng.integers(0, 2, n)})
    df["date"] = np.where(df.index < 300, 20220101, 20230101)
    tr, va = df[df["date"] < 20230101], df[df["date"] >= 20230101]
    params = {"objective": "binary", "metric": "None", "verbosity": -1,
              "num_leaves": 7, "min_data_in_leaf": 5, "seed": 7, "deterministic": True}
    m1 = fit_binary(tr, va, ["x"], cat=[], params=dict(params))
    m2 = fit_binary(tr, va, ["x"], cat=[], params=dict(params))
    assert list(m1.predict(va[["x"]])) == list(m2.predict(va[["x"]]))
    assert brier(m1.predict(va[["x"]]), va["y"]) >= 0.0
