"""W2 tournament (PREREGISTERED — see HANDOFF §9): A/B/C/D column sets x
outer folds 2022/2023/2024/2025. Identical GBM hyperparams; only columns vary.
Research only: saves no models, writes data/tourney.json (metrics + OOF preds).

python -m backend.gbm.tourney
Costs zero API credits. ~16 fits, 20-40 min.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.gbm.train import SETS, _fit, brier, load_frame

DATA = Path(__file__).resolve().parents[2] / "data"
OUT = DATA / "tourney.json"

FOLDS = [2022, 2023, 2024, 2025]


def run(verbose: bool = True) -> dict:
    from backend.model.calibrate import ece

    df, _ = load_frame()
    missing = {c for cols in SETS.values() for c in cols} - set(df.columns)
    if missing:
        raise SystemExit(f"feature store lacks tournament columns: {sorted(missing)}. "
                         "Run python -m backend.features.build first.")
    res: dict = {"sets": list(SETS), "folds": FOLDS, "results": {}, "improvement_vs_A": {}}
    oof: dict = {}
    for name, feats in SETS.items():
        res["results"][name] = {}
        oof[name] = []
        for T in FOLDS:
            tr = df[df["date"] < (T - 1) * 10000]
            va = df[(df["date"] >= (T - 1) * 10000) & (df["date"] < T * 10000)]
            te = df[(df["date"] >= T * 10000) & (df["date"] < (T + 1) * 10000)]
            if len(tr) < 500 or len(va) < 100 or len(te) < 100:
                res["results"][name][str(T)] = {"error": "too thin",
                                                "n": [len(tr), len(va), len(te)]}
                continue
            model = _fit(tr, va, feats)
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
    for name in ("B", "C", "D"):
        deltas = []
        for T in FOLDS:
            a = res["results"]["A"].get(str(T), {})
            b = res["results"][name].get(str(T), {})
            if "brier" in a and "brier" in b:
                deltas.append(round(a["brier"] - b["brier"], 5))
        mean = round(sum(deltas) / len(deltas), 5) if deltas else None
        res["improvement_vs_A"][name] = {
            "per_fold": deltas, "mean": mean,
            "positive_folds": sum(1 for d in deltas if d > 0),
            "promote": bool(mean is not None and mean >= 0.0005
                            and sum(1 for d in deltas if d > 0) >= 3)}
    res["oof"] = oof
    OUT.write_text(json.dumps(res))
    if verbose:
        print("promotion (mean>=0.0005 & >=3/4 folds positive):",
              {k: v["promote"] for k, v in res["improvement_vs_A"].items()})
    return res


if __name__ == "__main__":
    run()
