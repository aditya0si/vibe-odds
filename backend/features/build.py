"""As-of feature rows for every match. Neutral (alphabetical) ordering + label.

python -m backend.features.build [--first 2020 --last 2026]
Writes data/features.parquet. Every value is computed from history strictly
BEFORE the match date — see tests/test_features.py::test_no_leakage.
The replay loop lives in ``core.asof`` (map step 8); tennis-specific: the
row contents (backend.model.signals.build_row) and the match stream.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.model.signals import build_row as row_for

OUT = Path(__file__).resolve().parents[2] / "data" / "features.parquet"


def build(first: int = 2020, last: int = 2026, verbose: bool = True,
          out: Path | None = None) -> str:
    import pandas as pd

    from core.asof import asof_rows
    from backend.model.signals import build_row as row_for, new_ctx, replay_ctx
    from backend.ratings.loader import load_years

    ctx = new_ctx()

    def _row(m, ctx):
        a, b = sorted([m["winner"], m["loser"]])
        r = row_for(a, b, m, ctx)  # point_cols + fast twins included
        r["y"] = 1 if m["winner"] == a else 0
        return r

    rows = list(asof_rows(load_years(2018, last), ctx, replay_ctx, _row, first=first))
    df = pd.DataFrame(rows)
    target = Path(out) if out is not None else OUT
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, index=False)
    if verbose:
        print(f"wrote {len(df)} rows x {len(df.columns)} cols -> {target}")
        print(f"base rate {df['y'].mean():.3f}, surfaces:\n{df['surface'].value_counts()}")
    return str(target)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2020)
    ap.add_argument("--last", type=int, default=2026)
    a = ap.parse_args()
    build(a.first, a.last)
