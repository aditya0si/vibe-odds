"""Freeze the 2026-27 claim arm BEFORE the season starts (plan Task 8).

    python tools/season_gate.py        # writes sports/nba/data/nba_phase2_arm_2026_27.json

The Phase-2 claim arm is the admitted A7 structure (docs/preregistration.md §13):
feature names frozen after 2021-22, weights refit on the EXPANDING window (all
seasons <= 2025-26). This tool performs that fit ONCE and freezes the result with
a digest, so the 2026-27 claim read (tools/phase2_read.py) can only ever score
against a model that predates the season. Re-running after any 2026-27 game has
been played must not be possible silently: the record carries fit_through and
the digest pins the bytes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):                   # script mode (cron / manual): repo importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build, paths
from sports.nba.model import formula as F
from sports.nba.model.phase2 import A7_FEATURES

FIT_THROUGH = "2025-26"          # expanding window: every completed season
CLAIM_SEASON = "2026-27"
ARM_PATH = paths.DATA / "nba_phase2_arm_2026_27.json"


def arm_digest(rec: dict) -> str:
    """sha256 over the record body (everything but the digest itself)."""
    body = {k: v for k, v in rec.items() if k != "digest"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def fit_claim_arm(rows: list[dict], features: tuple[str, ...] = tuple(A7_FEATURES)) -> dict:
    """Fit the frozen-structure arm on the expanding window and seal it.

    Deterministic: the same rows produce the same digest (asserted by tests).
    """
    fit_rows = [r for r in rows if r["season"] <= FIT_THROUGH]
    if not fit_rows:
        raise ValueError(f"no fit rows <= {FIT_THROUGH}")
    formula = F.fit_formula(fit_rows, features)
    rec = {
        "arm": "A7",
        "features": list(features),
        "fit_through": FIT_THROUGH,
        "fit_window": f"expanding (all seasons <= {FIT_THROUGH})",
        "claim_season": CLAIM_SEASON,
        "n_fit": len(fit_rows),
        "formula": formula,
    }
    rec["digest"] = arm_digest(rec)
    return rec


def freeze(con, out=None) -> dict:
    rows = F.load_dataset(con, version="v3", feature_names=A7_FEATURES)
    rec = fit_claim_arm(rows)
    (out or ARM_PATH).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main() -> int:
    rec = freeze(build.init(verbose=False))
    print(f"arm={rec['arm']} fit_through={rec['fit_through']} n_fit={rec['n_fit']}")
    print(f"claim_season={rec['claim_season']} digest={rec['digest']}")
    print(f"frozen -> {ARM_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
