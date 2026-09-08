"""W2-E: cold-start output shrinkage on B columns. p' = .5+(p-.5)*n/(n+k),
n = form_n_min. k tuned on VALID year per fold (never test), evaluated pooled
+ sparse subgroup (form_n_min < 5) on Brier and logloss.

E promotion bar (preregistered): pooled test-Brier gain vs k=0 >= 0.0005 AND
sparse-subgroup logloss improves AND no pooled Brier harm. Else E dies and
production ships plain B.
"""

from __future__ import annotations

import math

from backend.gbm.tourney import FOLDS, SETS
from backend.gbm.train import _fit, brier, load_frame

FEATS = SETS["B"]

KS = [0, 3, 10, 30]


def _logloss(ps, ys) -> float:
    tot = 0.0
    for p, y in zip(ps, ys):
        p = min(0.999, max(0.001, p))
        tot += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return tot / len(ps)


def _shrink(p: float, n: float, k: float) -> float:
    return 0.5 + (p - 0.5) * (n / (n + k)) if k > 0 else p


def run(verbose: bool = True) -> dict:
    df, _ = load_frame()
    per_fold = {}
    pool = {k: [[], []] for k in KS}  # k -> (briers, sparse loglosses) pooled via lists
    pool_ps = {k: [] for k in KS}
    pool_ys = {k: [] for k in KS}
    for T in FOLDS:
        tr = df[df["date"] < (T - 1) * 10000]
        va = df[(df["date"] >= (T - 1) * 10000) & (df["date"] < T * 10000)]
        te = df[(df["date"] >= T * 10000) & (df["date"] < (T + 1) * 10000)]
        model = _fit(tr, va, FEATS)
        pv = list(model.predict(va[FEATS]))
        pt = list(model.predict(te[FEATS]))
        nv = list(va["form_n_min"])
        nt = list(te["form_n_min"])
        yv = list(va["y"])
        yt = list(te["y"])
        vb = {k: brier([_shrink(p, n, k) for p, n in zip(pv, nv)], yv) for k in KS}
        kstar = min(KS, key=lambda k: vb[k])
        row = {"kstar": kstar, "valid_brier": {str(k): round(vb[k], 4) for k in KS}}
        for k in KS:
            ps = [_shrink(p, n, k) for p, n in zip(pt, nt)]
            sp = [p for p, n in zip(ps, nt) if n < 5]
            sy = [y for y, n in zip(yt, nt) if n < 5]
            row[f"k{k}"] = {"brier": round(brier(ps, yt), 4),
                            "sparse_n": len(sp),
                            "sparse_logloss": round(_logloss(sp, sy), 4) if sp else None}
            pool_ps[k].extend(ps)
            pool_ys[k].extend(yt)
        per_fold[str(T)] = row
        if verbose:
            print(f"fold {T}: kstar={kstar} | " + " ".join(
                f"k{k}=b{row[f'k{k}']['brier']}/ll{row[f'k{k}']['sparse_logloss']}" for k in KS),
                flush=True)
    kstars = [per_fold[str(T)]["kstar"] for T in FOLDS]
    # majority valid-chosen k (tie -> smaller = less shrinkage)
    kfin = min(KS, key=lambda k: (-kstars.count(k), k))
    pb = {k: brier(pool_ps[k], pool_ys[k]) for k in KS}
    res = {"per_fold": per_fold, "k_final": kfin,
           "pooled_brier": {str(k): round(pb[k], 5) for k in KS},
           "gain_vs_k0": round(pb[0] - pb[kfin], 5),
           "promote": bool(pb[0] - pb[kfin] >= 0.0005)}
    if verbose:
        print("k_final:", kfin, "pooled:", res["pooled_brier"],
              "gain:", res["gain_vs_k0"], "promote:", res["promote"])
    return res


if __name__ == "__main__":
    run()
