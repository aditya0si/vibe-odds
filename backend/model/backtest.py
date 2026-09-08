"""Walk-forward backtest (no weight saving). Thin wrapper over the trainer.

python -m backend.model.backtest [--first 2024 --last 2026]
"""

from __future__ import annotations

import argparse

from backend.model.learn import run


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2024)
    ap.add_argument("--last", type=int, default=2026)
    a = ap.parse_args()
    run(a.first * 10000, a.last, save=False)
