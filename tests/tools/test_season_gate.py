"""The season gate seals the claim arm before a game is played (plan Task 8)."""
from __future__ import annotations

import random

from sports.nba.model.phase2 import A7_FEATURES
from tools.season_gate import arm_digest, fit_claim_arm


def synthetic_rows(n: int = 80) -> list[dict]:
    rng = random.Random(42)
    rows = []
    for i in range(n):
        home = int(i % 3 != 0)
        r = {"game_id": f"g{i:04d}", "game_date": f"2025-11-{(i % 28) + 1:02d}",
             "season": "2025-26", "home_win": home,
             "x": {f: round(rng.uniform(-3, 3) + (0.5 if (home and j % 2) else 0), 4)
                   for j, f in enumerate(A7_FEATURES)}}
        rows.append(r)
    return rows


def test_digest_exists_and_is_stable_across_two_calls():
    rows = synthetic_rows()
    a, b = fit_claim_arm(rows), fit_claim_arm(rows)
    assert a["digest"] and a["digest"] == b["digest"]       # the plan's named test
    assert a["n_fit"] == 80 and a["claim_season"] == "2026-27"


def test_digest_changes_when_the_fit_changes():
    rows = synthetic_rows()
    a = fit_claim_arm(rows)
    perturbed = [dict(r) for r in rows]
    perturbed[0]["x"]["elo_diff"] = perturbed[0]["x"]["elo_diff"] + 10.0
    b = fit_claim_arm(perturbed)
    assert a["digest"] != b["digest"]


def test_arm_is_the_frozen_a7_structure():
    rec = fit_claim_arm(synthetic_rows())
    assert rec["arm"] == "A7"
    assert rec["features"] == list(A7_FEATURES)
    assert rec["fit_through"] == "2025-26"                   # expanding window
    assert "formula" in rec and rec["formula"]["n_train"] == 80


def test_digest_is_recomputable_from_the_body():
    rec = fit_claim_arm(synthetic_rows())
    assert arm_digest(rec) == rec["digest"]
    bodyless = dict(rec, digest=None)
    assert arm_digest(bodyless) == rec["digest"]             # digest excluded from its own hash
