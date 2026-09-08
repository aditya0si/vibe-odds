"""Expert-activity audit (Astra W1): per-signal activation, variance, Brier,
correlation with GBM and with the outcome, over the 2024-25 window.

Answers: which experts are inert (never fire), which are GBM-echoes
(correlation ~1, no incremental value), which actually disagree usefully.
Read-only. python -m backend.model.activity [--since 20240101] [--last 2025]
"""

from __future__ import annotations

import argparse
import math

from backend.markov.points import PointRatings
from backend.model.signals import SIGNALS, Ctx
from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years


def _corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 10:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def run(since: int = 20240101, last: int = 2025, verbose: bool = True) -> dict:
    matches = load_years(2018, last)
    from backend.model.signals import new_ctx, replay_ctx
    ctx = new_ctx()
    recs: list[tuple[dict, int]] = []  # ({signal: prob|None}, y)
    n = 0
    for m in matches:
        if m["walkover"]:
            continue
        if m["date"] >= since:
            a, b = sorted([m["winner"], m["loser"]])
            y = 1 if m["winner"] == a else 0
            row = {}
            for k, fn in SIGNALS.items():
                try:
                    row[k] = fn(a, b, m["surface"], ctx, date=m["date"], best_of=m["best_of"])
                except Exception:
                    row[k] = None
            recs.append((row, y))
            n += 1
        replay_ctx(ctx, m)
    rows = []
    for k in SIGNALS:
        ps = [r[k] for r, _ in recs if r[k] is not None]
        yk = [y for r, y in recs if r[k] is not None]
        act = len(ps) / n if n else 0
        if ps:
            mean = sum(ps) / len(ps)
            var = sum((p - mean) ** 2 for p in ps) / len(ps)
            bri = sum((p - y) ** 2 for p, y in zip(ps, yk)) / len(ps)
            co = [(r[k], r["gbm"]) for r, _ in recs
                  if r[k] is not None and r["gbm"] is not None]
            cg = _corr([p for p, _ in co], [g for _, g in co]) if k != "gbm" else 1.0
            cy = _corr(ps, yk)
        else:
            mean = var = bri = cy = float("nan")
            cg = float("nan")
        status = "INERT (never fires)" if not ps else (
            "GBM-ECHO?" if (cg == cg and abs(cg) > 0.9) else ("thin" if act < 0.5 else "active"))
        rows.append({"signal": k, "activation": round(act, 4), "n": len(ps),
                     "mean": round(mean, 4) if ps else None,
                     "std": round(math.sqrt(var), 4) if ps else None,
                     "brier": round(bri, 4) if ps else None,
                     "corr_gbm": round(cg, 3) if cg == cg else None,
                     "corr_outcome": round(cy, 3) if cy == cy else None,
                     "status": status})
    res = {"since": since, "last": last, "matches": n, "rows": rows}
    if verbose:
        print(f"{n} matches. activation / std / brier / corr(gbm) / corr(y):")
        for r in rows:
            print(f"  {r['signal']:>12} act {r['activation']:.3f} std {r['std']} "
                  f"brier {r['brier']} corr_gbm {r['corr_gbm']} corr_y {r['corr_outcome']}  [{r['status']}]")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=20240101)
    ap.add_argument("--last", type=int, default=2025)
    a = ap.parse_args()
    run(a.since, a.last)
