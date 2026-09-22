"""W2 tournament (PREREGISTERED — see HANDOFF §9): A/B/C/D column sets x
outer folds 2022/2023/2024/2025. Identical GBM hyperparams; only columns vary.
Research only: saves no models, writes data/tourney.json (metrics + OOF preds).

python -m backend.gbm.tourney
Costs zero API credits. ~16 fits, 20-40 min.

The tournament engine lives in ``core.boosted`` (map step 9). Tennis keeps the
frozen literals: SETS, FOLDS and the output path.
"""

from __future__ import annotations

from pathlib import Path

from backend.gbm.train import SETS  # noqa: F401  (historical: tourney.SETS)

DATA = Path(__file__).resolve().parents[2] / "data"
OUT = DATA / "tourney.json"

FOLDS = [2022, 2023, 2024, 2025]


def run(verbose: bool = True) -> dict:
    from core.boosted import tournament
    from backend.gbm.train import CAT, load_frame, make_params
    df, _ = load_frame()
    return tournament(df, SETS, FOLDS, cat=CAT, make_params=make_params,
                      out=OUT, verbose=verbose)


if __name__ == "__main__":
    run()
