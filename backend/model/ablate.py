"""Leave-one-signal-out ablation over a walk-forward window.

Replays history to build state, then scores [since..last] predict-then-update
with 1 + len(SIGNALS) parallel ensembles (full + each signal dropped).
A signal earns its keep iff dropping it WORSENS Brier; the delta also says
which "dead" floor-weight signals are pure noise vs harmless passengers.

python -m backend.model.ablate [--since 20240101] [--last 2025]
Read-only: touches no state files. Costs zero API credits.
"""

from __future__ import annotations

import argparse

from backend.markov.points import PointRatings
from backend.model.ensemble import Ensemble
from backend.model.signals import SIGNALS, Ctx
from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years


def run(since: int = 20240101, last: int = 2025, verbose: bool = True) -> dict:
    matches = load_years(2018, last)
    from backend.model.signals import new_ctx, replay_ctx
    ctx = new_ctx()
    variants = {"full": None} | {f"no_{k}": k for k in SIGNALS if k not in ("market", "news")}
    ens = {v: Ensemble() for v in variants}
    acc: dict[str, list] = {v: [0, 0] for v in variants}  # correct, n
    bri: dict[str, list] = {v: [0.0, 0] for v in variants}  # sum_brier, n
    for m in matches:
        if m["walkover"]:
            continue
        if m["date"] >= since:
            a, b = sorted([m["winner"], m["loser"]])
            y = 1 if m["winner"] == a else 0
            probs = {k: fn(a, b, m["surface"], ctx, date=m["date"], best_of=m["best_of"])
                     for k, fn in SIGNALS.items() if k not in ("market", "news")}
            for v, drop in variants.items():
                p = probs if drop is None else {k: val for k, val in probs.items() if k != drop}
                out = ens[v].predict(p, m["surface"])
                acc[v][0] += ((out["p_a"] >= 0.5) == (y == 1))
                acc[v][1] += 1
                bri[v][0] += (out["p_a"] - y) ** 2
                bri[v][1] += 1
                ens[v].update(p, m["winner"] == a, m["surface"])
        replay_ctx(ctx, m)
    base = bri["full"][0] / bri["full"][1]
    rows = []
    for v in variants:
        b = bri[v][0] / bri[v][1]
        rows.append({"variant": v, "n": bri[v][1],
                     "acc": round(acc[v][0] / acc[v][1], 4),
                     "brier": round(b, 4),
                     "delta_brier": round(b - base, 5)})
    rows.sort(key=lambda r: -r["delta_brier"])
    res = {"since": since, "last": last, "full_brier": round(base, 4), "rows": rows}
    if verbose:
        print(f"full Brier {base:.4f} (n={bri['full'][1]}). +delta = dropping it HURT (signal earns keep).")
        for r in rows:
            flag = "KEEP" if r["delta_brier"] > 0.0005 else ("drop?" if r["variant"] != "full" and r["delta_brier"] <= 0 else "")
            print(f"  {r['variant']:>16} brier {r['brier']:.4f} ({r['delta_brier']:+.5f}) acc {r['acc']:.4f} {flag}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=20240101)
    ap.add_argument("--last", type=int, default=2025)
    a = ap.parse_args()
    run(a.since, a.last)
