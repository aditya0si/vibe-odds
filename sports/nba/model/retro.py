"""Labeled retrospective re-scoring on the BURNED window (plan Task 7).

    python -m sports.nba.model.retro        # writes sports/nba/data/nba_phase2_retro.json
    python -m sports.nba.model.retro --a9   # writes sports/nba/data/nba_phase3_retro_a9.json
    python -m sports.nba.model.retro --a11  # writes sports/nba/data/nba_phase3_retro_a11.json

We have seen 2022-23 -> 2025-26 results (docs/preregistration.md §13, burned-window
rule). Everything this module produces is a RETROSPECTIVE ESTIMATE for diagnostics
and future power calculations - never a claim test. The two mandatory labels
(``burned_test_set: true`` and ``claim_eligible: false``) are set in exactly one
place (``retro_record``) and asserted by tests/sports/nba/test_retro_label.py.

Arms re-scored (fitted <= 2020-21, structure frozen):
  A7_formula - the admitted transparent arm (logistic on the A7 feature set)
  G1_stack   - the rejected nonlinear arm (monotone GBM + Hedge; diagnostic)
  A9_formula  - the rejected Phase-3 candidate (pv_* REPLACES the absence family)
  A9b_formula - the additive Phase-3 candidate (A7 + pv_*); best tune number so far
  A11_formula - the SRS team-ratings candidate (A7 + srs_diff; features v6)
Paired stats are the module's standard ones (paired_stats: mean_diff, sigma_d,
blocked bootstrap CI): vs market open (T2' analogue), vs market mid (T2''
analogue - only eras with a mid snapshot), vs market close (T3 analogue).
"""
from __future__ import annotations

import json
import sys

from sports.nba.db import build, paths
from sports.nba.model import formula as F
from sports.nba.model import phase3 as P3
from sports.nba.model import srs_gate as SG
from sports.nba.model import stack as S
from sports.nba.model.phase2 import A7_FEATURES

RETRO_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")
RETRO_PATH = paths.DATA / "nba_phase2_retro.json"
RETRO_A9_PATH = paths.DATA / "nba_phase3_retro_a9.json"
RETRO_A11_PATH = paths.DATA / "nba_phase3_retro_a11.json"
LABEL = ("RETROSPECTIVE ESTIMATES ONLY - the 2022-23..2025-26 window is burned "
         "(its results were known before Phase-2 work began). No number here may "
         "ever be published as a claim test.")


def retro_record(n_retro: int, briers: dict, paired: dict, hedge_weights: dict | None = None) -> dict:
    """The artifact builder. The burned-window labels are mandatory and ALWAYS set
    here - callers cannot produce an unlabeled retro record through this function."""
    rec = {
        "burned_test_set": True,
        "claim_eligible": False,
        "label": LABEL,
        "arm_fits": {"train_end": F.TRAIN_END, "tune": F.TUNE_SEASON,
                     "retro_seasons": list(RETRO_SEASONS)},
        "n_retro": n_retro,
        "arms_brier": briers,
        "paired": paired,
    }
    if hedge_weights is not None:
        rec["g1_hedge_weights_final"] = hedge_weights
    return rec


def _brier(probs: dict[str, float], labels: dict[str, int], games: list[str]) -> float | None:
    games = [g for g in games if g in probs]
    return round(sum((probs[g] - labels[g]) ** 2 for g in games) / len(games), 5) if games else None


def retro(con, out=None) -> dict:
    """Re-score the Phase-2 arms on the burned window with the mandatory labels."""
    rows = F.load_dataset(con, version="v3", feature_names=A7_FEATURES)
    fit_rows = [r for r in rows if r["season"] <= F.TRAIN_END]
    retro_rows = [r for r in rows if r["season"] in RETRO_SEASONS]
    labels = {r["game_id"]: r["home_win"] for r in retro_rows}
    dates = {r["game_id"]: r["game_date"] for r in retro_rows}
    games = [r["game_id"] for r in retro_rows]

    formula = F.fit_formula(fit_rows, tuple(A7_FEATURES))
    p_formula = {r["game_id"]: F.predict(formula, r) for r in retro_rows}
    gbm = S.fit_gbm([r for r in rows if r["season"] <= S.EARLY_TRAIN_END],
                    [r for r in rows if r["season"] == S.VALID_SEASON])
    recs, hedge = S.stack_probs(retro_rows, feats=A7_FEATURES, gbm=gbm, formula=formula)
    p_g1 = {r["game_id"]: r["p_stack"] for r in recs}

    mkt = {kind: F.market_probs(con, kind) for kind in ("open", "mid", "close")}

    def block(pa: dict[str, float], pb: dict[str, float]) -> list[str]:
        return [g for g in games if g in pa and g in pb]

    briers = {
        "A7_formula": _brier(p_formula, labels, games),
        "G1_stack": _brier(p_g1, labels, games),
        "market_open": _brier(mkt["open"], labels, games),
        "market_mid": _brier(mkt["mid"], labels, games),
        "market_close": _brier(mkt["close"], labels, games),
    }
    paired = {
        "formula_vs_open": F.paired_stats(p_formula, mkt["open"], labels,
                                         block(p_formula, mkt["open"]), dates),
        "formula_vs_mid": F.paired_stats(p_formula, mkt["mid"], labels,
                                        block(p_formula, mkt["mid"]), dates),
        "formula_vs_close": F.paired_stats(p_formula, mkt["close"], labels,
                                          block(p_formula, mkt["close"]), dates),
        "g1_vs_open": F.paired_stats(p_g1, mkt["open"], labels,
                                    block(p_g1, mkt["open"]), dates),
        "g1_vs_close": F.paired_stats(p_g1, mkt["close"], labels,
                                     block(p_g1, mkt["close"]), dates),
        "market_open_improves_to_close": F.paired_stats(
            mkt["open"], mkt["close"], labels, block(mkt["open"], mkt["close"]), dates),
    }
    rec = retro_record(len(games), briers, paired,
                       hedge_weights={k: round(v, 4) for k, v in hedge.table("ml").items()})
    (out or RETRO_PATH).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def retro_a9(con, out=None) -> dict:
    """Re-score the Phase-3 candidates (A9, A9b) on the burned window - diagnostics only.

    Same mandatory labels as the Phase-2 retro pass (retro_record), same paired stats.
    The frozen nba_phase2_retro.json is never touched by this path.
    """
    rows5 = F.load_dataset(con, version="v5", feature_names=P3.A9_FEATURES)
    fit5 = [r for r in rows5 if r["season"] <= F.TRAIN_END]
    retro5 = [r for r in rows5 if r["season"] in RETRO_SEASONS]
    labels = {r["game_id"]: r["home_win"] for r in retro5}
    dates = {r["game_id"]: r["game_date"] for r in retro5}
    games = [r["game_id"] for r in retro5]
    a9 = F.fit_formula(fit5, tuple(P3.A9_FEATURES))
    p_a9 = {r["game_id"]: F.predict(a9, r) for r in retro5}

    merged = P3.load_merged_dataset(con, P3.A9B_FEATURES)
    fit_m = [r for r in merged if r["season"] <= F.TRAIN_END]
    retro_m = [r for r in merged if r["season"] in RETRO_SEASONS]
    a9b = F.fit_formula(fit_m, tuple(P3.A9B_FEATURES))
    p_a9b = {r["game_id"]: F.predict(a9b, r) for r in retro_m}

    rows3 = F.load_dataset(con, version="v3", feature_names=A7_FEATURES)
    fit3 = [r for r in rows3 if r["season"] <= F.TRAIN_END]
    retro3 = [r for r in rows3 if r["season"] in RETRO_SEASONS]
    a7 = F.fit_formula(fit3, tuple(A7_FEATURES))
    p_a7 = {r["game_id"]: F.predict(a7, r) for r in retro3}

    mkt = {kind: F.market_probs(con, kind) for kind in ("open", "mid", "close")}

    def block(pa: dict[str, float], pb: dict[str, float]) -> list[str]:
        return [g for g in games if g in pa and g in pb]

    briers = {
        "A9_formula": _brier(p_a9, labels, games),
        "A9b_formula": _brier(p_a9b, labels, games),
        "A7_formula": _brier(p_a7, labels, games),
        "market_open": _brier(mkt["open"], labels, games),
        "market_mid": _brier(mkt["mid"], labels, games),
        "market_close": _brier(mkt["close"], labels, games),
    }
    paired = {
        "a9_vs_open": F.paired_stats(p_a9, mkt["open"], labels, block(p_a9, mkt["open"]), dates),
        "a9_vs_close": F.paired_stats(p_a9, mkt["close"], labels, block(p_a9, mkt["close"]), dates),
        "a9b_vs_open": F.paired_stats(p_a9b, mkt["open"], labels, block(p_a9b, mkt["open"]), dates),
        "a9b_vs_close": F.paired_stats(p_a9b, mkt["close"], labels, block(p_a9b, mkt["close"]), dates),
        "a9b_vs_a7": F.paired_stats(p_a9b, p_a7, labels, block(p_a9b, p_a7), dates),
    }
    rec = retro_record(len(games), briers, paired)
    (out or RETRO_A9_PATH).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def retro_a11(con, out=None) -> dict:
    """Re-score the A11 candidate (SRS team ratings, v6) on the burned window.

    Same mandatory labels as every retro path (retro_record), same paired stats.
    Frozen Phase-2/3 artifacts are never touched by this path.
    """
    rows6 = F.load_dataset(con, version="v6", feature_names=SG.A11_FEATURES)
    fit6 = [r for r in rows6 if r["season"] <= F.TRAIN_END]
    retro6 = [r for r in rows6 if r["season"] in RETRO_SEASONS]
    labels = {r["game_id"]: r["home_win"] for r in retro6}
    dates = {r["game_id"]: r["game_date"] for r in retro6}
    games = [r["game_id"] for r in retro6]
    a11 = F.fit_formula(fit6, tuple(SG.A11_FEATURES))
    p_a11 = {r["game_id"]: F.predict(a11, r) for r in retro6}

    rows3 = F.load_dataset(con, version="v3", feature_names=A7_FEATURES)
    fit3 = [r for r in rows3 if r["season"] <= F.TRAIN_END]
    retro3 = [r for r in rows3 if r["season"] in RETRO_SEASONS]
    a7 = F.fit_formula(fit3, tuple(A7_FEATURES))
    p_a7 = {r["game_id"]: F.predict(a7, r) for r in retro3}

    mkt = {kind: F.market_probs(con, kind) for kind in ("open", "close")}

    def block(pa: dict[str, float], pb: dict[str, float]) -> list[str]:
        return [g for g in games if g in pa and g in pb]

    briers = {
        "A11_formula": _brier(p_a11, labels, games),
        "A7_formula": _brier(p_a7, labels, games),
        "market_open": _brier(mkt["open"], labels, games),
        "market_close": _brier(mkt["close"], labels, games),
    }
    paired = {
        "a11_vs_a7": F.paired_stats(p_a11, p_a7, labels, block(p_a11, p_a7), dates),
        "a11_vs_open": F.paired_stats(p_a11, mkt["open"], labels, block(p_a11, mkt["open"]), dates),
        "a11_vs_close": F.paired_stats(p_a11, mkt["close"], labels, block(p_a11, mkt["close"]), dates),
    }
    rec = retro_record(len(games), briers, paired)
    (out or RETRO_A11_PATH).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main() -> int:
    con = build.init(verbose=False)
    if "--a11" in sys.argv:
        rec = retro_a11(con)
        print(f"burned_test_set={rec['burned_test_set']} claim_eligible={rec['claim_eligible']}")
        print(f"n_retro={rec['n_retro']}  -> {RETRO_A11_PATH.name}")
        print(json.dumps(rec["arms_brier"], indent=1))
        for k, v in rec["paired"].items():
            print(f"  {k}: mean_diff={v.get('mean_diff')} ci95={v.get('ci95_blocked')} n={v.get('n')}")
        return 0
    if "--a9" in sys.argv:
        rec = retro_a9(con)
        print(f"burned_test_set={rec['burned_test_set']} claim_eligible={rec['claim_eligible']}")
        print(f"n_retro={rec['n_retro']}  -> {RETRO_A9_PATH.name}")
        print(json.dumps(rec["arms_brier"], indent=1))
        for k, v in rec["paired"].items():
            print(f"  {k}: mean_diff={v.get('mean_diff')} ci95={v.get('ci95_blocked')} n={v.get('n')}")
        return 0
    rec = retro(con)
    print(f"burned_test_set={rec['burned_test_set']} claim_eligible={rec['claim_eligible']}")
    print(f"n_retro={rec['n_retro']}  -> {RETRO_PATH.name}")
    print(json.dumps(rec["arms_brier"], indent=1))
    for k, v in rec["paired"].items():
        print(f"  {k}: mean_diff={v.get('mean_diff')} ci95={v.get('ci95_blocked')} n={v.get('n')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
