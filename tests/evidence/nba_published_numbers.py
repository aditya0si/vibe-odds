"""The numbers the NBA project publishes, in one shared place.

The README, the methods docs and the site must not retype these literals: they
import this module (Step 8). Every value here is **re-derived from the frozen
artifacts** in ``sports/nba/data/`` by ``tests/evidence/test_nba_frozen_evidence.py``;
if an artifact moves, that guard fails until this module (and the docs that
import it) are rebaselined in the same commit, with the reason in the message.

``sports/nba/data/`` is gitignored. The artifact bytes are rebuilt
deterministically by the NBA pipeline, and their sha256 is pinned in the tracked
``tests/evidence/nba_frozen_manifest.json``.
"""

from __future__ import annotations

# --- frozen split (docs/preregistration.md §2) ------------------------------
TRAIN_END = "2020-21"
TUNE_SEASON = "2021-22"
TEST_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")

# --- sigma_d (docs/preregistration.md §5; measured on 2021-22) --------------
# The REGISTERED lock: the value written into the pre-registration before any
# test season was seen. It is a frozen claim input, not a recompute, and it is
# asserted against docs/preregistration.md §5 by the evidence guard.
SIGMA_D_LOCK = 0.08497
SIGMA_D_LOCK_N = 1227
# The CURRENT code's books-only recompute on the same 2021-22 window, read back
# from formula_v1_report.json["tuning_season_paired_formula_vs_market"]. It
# differs from the registered lock because the market arm was later
# decontaminated (in-play feed rows and model feeds excluded), which changes the
# market losses sigma_d is measured against. Both values are published: the lock
# is the claim input; this is what the current pipeline reproduces.
SIGMA_D_CURRENT_CODE = 0.08522
NEEDED_N_FOR_DELTA_005 = 2267

# --- T1: calibrated and better than naive baselines -------------------------
T1_PASSES = True
T1_FORMULA_BRIER = 0.21303
T1_CLIMATOLOGICAL_BRIER = 0.24766
T1_ELO_ONLY_BRIER = 0.21518
T1_VS_CLIMATOLOGICAL_MEAN_DIFF = 0.03463
T1_VS_CLIMATOLOGICAL_CI = (0.02984, 0.03944)
T1_VS_ELO_MEAN_DIFF = 0.00215
T1_VS_ELO_CI = (0.00109, 0.00323)

# --- T2/T3: beats the opening / closing line (both FAIL, published) ---------
T2_PASSES = False
T2_N_GAMES = 4888
T2_MEAN_DIFF = -0.00681
T2_CI = (-0.00925, -0.00450)
T3_PASSES = False
T3_N_GAMES = 4890
T3_MEAN_DIFF = -0.01077
T3_CI = (-0.01324, -0.00823)

# --- market structure: close < open < formula -------------------------------
MARKET_FORMULA_BRIER = 0.21265
MARKET_OPEN_BRIER = 0.20584
MARKET_CLOSE_BRIER = 0.20190
MARKET_OPEN_TO_CLOSE_MEAN_DIFF = -0.00395
MARKET_OPEN_TO_CLOSE_CI = (-0.00543, -0.00236)

# --- A6 availability arm (exploratory, not a registered claim) --------------
A5_POOLED_BRIER = 0.21332
A6_POOLED_BRIER = 0.21053
A5_GAP_TO_OPEN = 0.00723
A6_GAP_TO_OPEN = 0.00444
A6_A5_MEAN_DIFF = 0.00279
A6_A5_CI = (0.0016, 0.00401)
A6_VERDICT = (
    "Availability narrows the pooled test Brier gap to the opening line from "
    "0.00723 (A5) to 0.00444 (A6), but does not close it (exploratory)."
)
