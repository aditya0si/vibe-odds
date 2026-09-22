"""LightGBM binary-model core (sport-neutral, map step 9).

Time-split training with early stopping on a past-only validation window,
deterministic seeds, monotone constraints, brier feval, SHAP driver extraction,
and the preregistered W2 experiment engines (column-set tournament, cold-start
shrinkage).

Adapters keep their literals — column sets, monotone maps, human names, params
(tennis: ``backend/gbm/*``; frozen evidence, map R3) — and wire them in.
Metric helpers are copied verbatim (map R4: no rounding unification).
"""

from __future__ import annotations

import json
from pathlib import Path


def brier(ps, ys) -> float:
    import numpy as np
    ps = np.asarray(ps, dtype=float)
    ys = np.asarray(ys, dtype=float)
    return float(((ps - ys) ** 2).mean())


def brier_feval(preds, ds):
    import numpy as np
    y = ds.get_label()
    return ("brier", float(((np.asarray(preds) - np.asarray(y)) ** 2).mean()), False)


def fit_binary(tr, va, feats, *, cat: list, params: dict):
    """One deterministic GBM fit with early stopping on the validation window."""
    import lightgbm as lgb
    dtr = lgb.Dataset(tr[feats], label=tr["y"], categorical_feature=cat)
    dva = lgb.Dataset(va[feats], label=va["y"], categorical_feature=cat, reference=dtr)
    return lgb.train(params, dtr, num_boost_round=2000, valid_sets=[dva],
                     feval=brier_feval,
                     callbacks=[lgb.early_stopping(100, verbose=False)])


def time_folds(df, folds, date_col: str = "date"):
    """The preregistered W2 outer folds: (train < Y-1, valid = Y-1, test = Y)."""
    out = []
    for t in folds:
        tr = df[df[date_col] < (t - 1) * 10000]
        va = df[(df[date_col] >= (t - 1) * 10000) & (df[date_col] < t * 10000)]
        te = df[(df[date_col] >= t * 10000) & (df[date_col] < (t + 1) * 10000)]
        out.append((t, tr, va, te))
    return out


def score_row(model, meta: dict, row: dict, human: dict | None = None) -> dict:
    """row: feature dict (a-perspective). Returns prob + top SHAP drivers."""
    import pandas as pd
    human = human or {}
    feats = meta["features"]
    df = pd.DataFrame([{k: row.get(k) for k in feats}])
    for c in meta["categorical"]:
        cats = meta.get("rounds" if c == "round" else "surfaces", [])
        df[c] = pd.Categorical(df[c], categories=cats)
    p = float(model.predict(df[feats])[0])
    contrib = model.predict(df[feats], pred_contrib=True)[0]
    names = model.feature_name()
    pairs = sorted(zip(names, contrib[:-1]), key=lambda x: -abs(x[1]))
    drivers = [{"feature": human.get(n, n), "push": round(float(v), 3)} for n, v in pairs[:4]]
    return {"p": p, "drivers": drivers}


# Preregistered W2 promotion bar: mean improvement >= 0.0005 and >= 3/4 folds
# positive (tournament); pooled test-Brier gain >= 0.0005 (cold-start).
PROMOTE_MIN_MEAN = 0.0005
PROMOTE_MIN_POSITIVE = 3


def tournament(df, sets: dict[str, list], folds: list, *, cat: list,
               make_params, out: Path | None = None, verbose: bool = True) -> dict:
    """W2 column-set tournament (preregistered protocol, verbatim).

    ``make_params(feats)`` returns the full lgb params (the adapter supplies the
    monotone map etc). Research only: saves no models; writes ``out`` if given.
    """
    from core.metrics import ece
    missing = {c for cols in sets.values() for c in cols} - set(df.columns)
    if missing:
        raise SystemExit(f"feature store lacks tournament columns: {sorted(missing)}. "
                         "Run python -m backend.features.build first.")
    res: dict = {"sets": list(sets), "folds": list(folds), "results": {}, "improvement_vs_A": {}}
    oof: dict = {}
    for name, feats in sets.items():
        res["results"][name] = {}
        oof[name] = []
        for T, tr, va, te in time_folds(df, folds):
            if len(tr) < 500 or len(va) < 100 or len(te) < 100:
                res["results"][name][str(T)] = {"error": "too thin",
                                                "n": [len(tr), len(va), len(te)]}
                continue
            model = fit_binary(tr, va, feats, cat=cat, params=make_params(feats))
            p = model.predict(te[feats])
            acc = float((((p >= 0.5).astype(int)) == te["y"].to_numpy()).mean())
            res["results"][name][str(T)] = {
                "n": [len(tr), len(va), len(te)], "acc": round(acc, 4),
                "brier": round(brier(p, te["y"]), 4),
                "ece": round(ece(list(p), list(te["y"])), 4),
                "best_iter": model.best_iteration}
            for d, pp, yy in zip(te["date"], p, te["y"]):
                oof[name].append({"date": int(d), "p": round(float(pp), 4), "y": int(yy)})
            if verbose:
                r = res["results"][name][str(T)]
                print(f"set {name} fold {T}: brier {r['brier']} acc {r['acc']} "
                      f"(n={r['n']}) iter {r['best_iter']}", flush=True)
    for name in list(sets):
        if name == "A":
            continue
        deltas = []
        for T in folds:
            a = res["results"]["A"].get(str(T), {})
            b = res["results"][name].get(str(T), {})
            if "brier" in a and "brier" in b:
                deltas.append(round(a["brier"] - b["brier"], 5))
        mean = round(sum(deltas) / len(deltas), 5) if deltas else None
        res["improvement_vs_A"][name] = {
            "per_fold": deltas, "mean": mean,
            "positive_folds": sum(1 for d in deltas if d > 0),
            "promote": bool(mean is not None and mean >= PROMOTE_MIN_MEAN
                            and sum(1 for d in deltas if d > 0) >= PROMOTE_MIN_POSITIVE)}
    res["oof"] = oof
    if out is not None:
        Path(out).write_text(json.dumps(res))
    if verbose:
        print("promotion (mean>=0.0005 & >=3/4 folds positive):",
              {k: v["promote"] for k, v in res["improvement_vs_A"].items()})
    return res


def logloss(ps, ys) -> float:
    import math
    tot = 0.0
    for p, y in zip(ps, ys):
        p = min(0.999, max(0.001, p))
        tot += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return tot / len(ps)


def shrink(p: float, n: float, k: float) -> float:
    return 0.5 + (p - 0.5) * (n / (n + k)) if k > 0 else p


def coldstart_experiment(df, feats, folds, *, cat: list, make_params,
                         ks=(0, 3, 10, 30), n_col: str = "form_n_min",
                         verbose: bool = True) -> dict:
    """Cold-start output shrinkage (preregistered W2-E bar, verbatim).

    p' = .5+(p-.5)*n/(n+k), n = n_col. k tuned on the VALID year per fold
    (never test). Promotion: pooled test-Brier gain vs k=0 >= 0.0005.
    """
    per_fold = {}
    pool_ps = {k: [] for k in ks}
    pool_ys = {k: [] for k in ks}
    for T, tr, va, te in time_folds(df, folds):
        model = fit_binary(tr, va, feats, cat=cat, params=make_params(feats))
        pv = list(model.predict(va[feats]))
        pt = list(model.predict(te[feats]))
        nv = list(va[n_col])
        nt = list(te[n_col])
        yv = list(va["y"])
        yt = list(te["y"])
        vb = {k: brier([shrink(p, n, k) for p, n in zip(pv, nv)], yv) for k in ks}
        kstar = min(ks, key=lambda k: vb[k])
        row = {"kstar": kstar, "valid_brier": {str(k): round(vb[k], 4) for k in ks}}
        for k in ks:
            ps = [shrink(p, n, k) for p, n in zip(pt, nt)]
            sp = [p for p, n in zip(ps, nt) if n < 5]
            sy = [y for y, n in zip(yt, nt) if n < 5]
            row[f"k{k}"] = {"brier": round(brier(ps, yt), 4),
                            "sparse_n": len(sp),
                            "sparse_logloss": round(logloss(sp, sy), 4) if sp else None}
            pool_ps[k].extend(ps)
            pool_ys[k].extend(yt)
        per_fold[str(T)] = row
        if verbose:
            print(f"fold {T}: kstar={kstar} | " + " ".join(
                f"k{k}=b{row[f'k{k}']['brier']}/ll{row[f'k{k}']['sparse_logloss']}" for k in ks),
                flush=True)
    kstars = [per_fold[str(T)]["kstar"] for T in folds]
    # majority valid-chosen k (tie -> smaller = less shrinkage)
    kfin = min(ks, key=lambda k: (-kstars.count(k), k))
    pb = {k: brier(pool_ps[k], pool_ys[k]) for k in ks}
    res = {"per_fold": per_fold, "k_final": kfin,
           "pooled_brier": {str(k): round(pb[k], 5) for k in ks},
           "gain_vs_k0": round(pb[0] - pb[kfin], 5),
           "promote": bool(pb[0] - pb[kfin] >= PROMOTE_MIN_MEAN)}
    if verbose:
        print("k_final:", kfin, "pooled:", res["pooled_brier"],
              "gain:", res["gain_vs_k0"], "promote:", res["promote"])
    return res
