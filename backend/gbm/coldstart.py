"""W2-E: cold-start output shrinkage on B columns. p' = .5+(p-.5)*n/(n+k),
n = form_n_min. k tuned on VALID year per fold (never test), evaluated pooled
+ sparse subgroup (form_n_min < 5) on Brier and logloss.

E promotion bar (preregistered): pooled test-Brier gain vs k=0 >= 0.0005 AND
sparse-subgroup logloss improves AND no pooled Brier harm. Else E dies and
production ships plain B.

The experiment engine lives in ``core.boosted`` (map step 9). Tennis keeps the
frozen literals: FOLDS, SETS["B"], KS.
"""

from __future__ import annotations

from backend.gbm.tourney import FOLDS
from backend.gbm.train import SETS

FEATS = SETS["B"]

KS = [0, 3, 10, 30]


def run(verbose: bool = True) -> dict:
    from core.boosted import coldstart_experiment
    from backend.gbm.train import CAT, load_frame, make_params
    df, _ = load_frame()
    return coldstart_experiment(df, FEATS, FOLDS, cat=CAT, make_params=make_params,
                                ks=KS, verbose=verbose)


if __name__ == "__main__":
    run()
