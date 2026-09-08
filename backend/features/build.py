"""As-of feature rows for every match. Neutral (alphabetical) ordering + label.

python -m backend.features.build [--first 2020 --last 2026]
Writes data/features.parquet. Every value is computed from history strictly
BEFORE the match date — see tests/test_features.py::test_no_leakage.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.model.signals import build_row as row_for

OUT = Path(__file__).resolve().parents[2] / "data" / "features.parquet"


def build(first: int = 2020, last: int = 2026, verbose: bool = True) -> str:
    import pandas as pd

    from backend.model.signals import build_row as row_for, new_ctx, replay_ctx
    from backend.ratings.loader import load_years

    matches = load_years(2018, last)
    ctx = new_ctx()
    rows = []
    for m in matches:
        if m["walkover"]:
            continue
        if m["date"] >= first * 10000:
            a, b = sorted([m["winner"], m["loser"]])
            r = row_for(a, b, m, ctx)  # point_cols + fast twins included
            r["y"] = 1 if m["winner"] == a else 0
            rows.append(r)
        replay_ctx(ctx, m)
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    if verbose:
        print(f"wrote {len(df)} rows x {len(df.columns)} cols -> {OUT}")
        print(f"base rate {df['y'].mean():.3f}, surfaces:\n{df['surface'].value_counts()}")
    return str(OUT)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2020)
    ap.add_argument("--last", type=int, default=2026)
    a = ap.parse_args()
    build(a.first, a.last)
