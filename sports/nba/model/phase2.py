"""Phase-2 development: arms beyond A6 and the pre-registered tune gate.

Registered (docs/preregistration.md §13, 2026-09-23): an arm enters the Phase-2
FREEZE only if it beats A6 on the 2021-22 tune season. Everything this module
writes is DEVELOPMENT evidence on windows already burned for claims; the gate
JSON carries ``claim_eligible: false`` and must never back a T2/T3 claim (claims
come from the prospective 2026-27 read only).

Arms:
  A6 - FORMULA_FEATURES + AVAIL_KEYS            (frozen exploratory arm, v2 rows)
  A7 - A6 + IMPACT_KEYS                         (Phase-2 C1, v3 rows): signed
       player-impact values for the inactive list (features/impact.py)

The A6 eligibility rule is generalised verbatim: rows where any arm feature is
None are EXCLUDED from fit and score - never imputed.

    python -m sports.nba.model.phase2
"""
from __future__ import annotations

import json
import sqlite3

from sports.nba.db import build, paths
from sports.nba.features.availability import AVAIL_KEYS
from sports.nba.features.build import AVAIL_VERSION, IMPACT_VERSION
from sports.nba.features.impact import IMPACT_KEYS
from sports.nba.model import formula as F

A7_FEATURES = F.FORMULA_FEATURES + tuple(AVAIL_KEYS) + tuple(IMPACT_KEYS)
GATE_PATH = "nba_phase2_gate.json"


def load_arm_rows(con: sqlite3.Connection, version: str,
                  feature_names: tuple[str, ...]) -> list[dict]:
    """Feature rows with row["x"] for exactly feature_names. Rows with any None
    arm feature are EXCLUDED (the A6 eligibility rule, generalised) - never
    imputed."""
    pass_through = set(AVAIL_KEYS) | set(IMPACT_KEYS)
    rows = []
    for r in con.execute("SELECT payload FROM features WHERE feature_version=?", (version,)):
        row = json.loads(r["payload"])
        x = {}
        for n in feature_names:
            if n in pass_through:
                v = row.get(n)
                x[n] = None if v is None else float(v)
            else:
                x[n] = F._feat(row, n)
        if any(v is None for v in x.values()):
            continue
        row["x"] = x
        rows.append(row)
    rows.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return rows


def gate_verdict(a7_brier: float, a6_brier: float) -> str:
    """§13 admission rule: strict improvement on the tune season or the arm does
    not enter the freeze. Ties lose."""
    return "enter" if a7_brier < a6_brier else "rejected"


def gate_json(arm: str, a7_brier: float, a6_brier: float, verdict: str,
              n_fit: int, n_tune: int, features: list, season_tune: str) -> dict:
    """The gate record. burned_test_set=False says the 2022-23+ window was NOT
    consulted; claim_eligible=False is permanent (development evidence only)."""
    return {
        "arm": arm,
        "gate_season": season_tune,
        "n_fit": n_fit,
        "n_tune": n_tune,
        "features": list(features),
        "baseline_arm": "A6",
        "baseline_brier": round(a6_brier, 5),
        "arm_brier": round(a7_brier, 5),
        "verdict": verdict,
        "burned_test_set": False,
        "claim_eligible": False,
    }


def _fit_and_score(rows: list[dict], feature_names: tuple[str, ...]) -> tuple[dict, float, int, int]:
    """Fit on <= TRAIN_END with the formula's exact protocol (standardised L2,
    C=1.0), score Brier on the tune season. No search, no tuning."""
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    tune = [r for r in rows if r["season"] == F.TUNE_SEASON]
    f = F.fit_formula(rows=train, feature_names=feature_names)
    brier = sum((F.predict(f, r) - r["home_win"]) ** 2 for r in tune) / len(tune)
    return f, brier, len(train), len(tune)


def run_gate(con: sqlite3.Connection, arm: str = "A7",
             arm_version: str = IMPACT_VERSION,
             arm_features: tuple[str, ...] = A7_FEATURES) -> dict:
    a6_rows = load_arm_rows(con, AVAIL_VERSION, F.A6_FEATURES)
    arm_rows = load_arm_rows(con, arm_version, arm_features)
    _, a6_brier, n_fit6, n_tune6 = _fit_and_score(a6_rows, F.A6_FEATURES)
    _, arm_brier, n_fit, n_tune = _fit_and_score(arm_rows, arm_features)
    verdict = gate_verdict(arm_brier, a6_brier)
    j = gate_json(arm=arm, a7_brier=arm_brier, a6_brier=a6_brier, verdict=verdict,
                  n_fit=n_fit, n_tune=n_tune, features=list(arm_features),
                  season_tune=F.TUNE_SEASON)
    out = paths.DATA / GATE_PATH
    out.write_text(json.dumps(j, indent=1), encoding="utf-8")
    print(f"A6  tune Brier: {a6_brier:.5f}  (n_fit={n_fit6}, n_tune={n_tune6})")
    print(f"{arm}  tune Brier: {arm_brier:.5f}  (n_fit={n_fit}, n_tune={n_tune})")
    print(f"verdict: {verdict} -> {out}")
    return j


def main() -> int:
    con = build.init(verbose=False)
    run_gate(con)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
