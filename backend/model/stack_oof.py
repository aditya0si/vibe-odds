"""Stack inputs (W3): past-only signal probs per match, 2020+. One replay pass.

Saves data/stack_oof.json rows: {date, surface, gbm, markov, elo, y}.
GBM prob here is the symmetrized live signal (same choke point as serve/sim).
python -m backend.model.stack_oof [--first 2020] [--last 2026]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "data" / "stack_oof.json"


def run(first: int = 2020, last: int = 2026, verbose: bool = True) -> Path:
    from backend.model.signals import new_ctx, replay_ctx
    from backend.model.signals import sig_elo_surface, sig_gbm, sig_markov
    from backend.ratings.loader import load_years

    ctx = new_ctx()
    rows = []
    for m in load_years(2018, last):
        if m["walkover"]:
            continue
        if m["date"] >= first * 10000:
            a, b = sorted([m["winner"], m["loser"]])
            y = 1 if m["winner"] == a else 0
            try:
                g = sig_gbm(a, b, m["surface"], ctx, date=m["date"], best_of=m["best_of"])
            except Exception:
                g = None
            try:
                mk = sig_markov(a, b, m["surface"], ctx, best_of=m["best_of"])
            except Exception:
                mk = None
            try:
                e = sig_elo_surface(a, b, m["surface"], ctx)
            except Exception:
                e = None
            rows.append({"date": m["date"], "surface": m["surface"],
                         "gbm": g, "markov": mk, "elo": e, "y": y})
        replay_ctx(ctx, m)
    OUT.write_text(json.dumps(rows))
    if verbose:
        print(f"wrote {len(rows)} stack rows -> {OUT}")
    return OUT


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2020)
    ap.add_argument("--last", type=int, default=2026)
    a = ap.parse_args()
    run(a.first, a.last)
