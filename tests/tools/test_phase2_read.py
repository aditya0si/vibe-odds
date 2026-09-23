"""The 2026-27 claim read: guards first, verdict rule frozen (plan Task 9)."""
from __future__ import annotations

import pytest

from tools.phase2_read import (NOT_RUN, read, read_record, score, tier_verdict, verify_arm)
from tools.season_gate import fit_claim_arm
from tests.tools.test_season_gate import synthetic_rows as syn


def test_registered_verdict_rule():
    assert tier_verdict({"n": 10, "mean_diff": 0.01, "ci95_blocked": [0.002, 0.02]}) is True
    assert tier_verdict({"n": 10, "mean_diff": -0.01, "ci95_blocked": [-0.02, -0.004]}) is False
    assert tier_verdict({"n": 10, "mean_diff": 0.001, "ci95_blocked": [-0.005, 0.006]}) is False  # straddles 0
    assert tier_verdict({"n": 0}) is False


def test_read_record_is_the_prospective_shape():
    rec = read_record("abc", {"T3_formula_at_T60_vs_closing": {}}, {"all": 10})
    assert rec["claim_eligible"] is True and rec["read_once"] is True
    assert rec["sealed_arm_digest"] == "abc" and rec["season"] == "2026-27"
    assert rec["g_tier"] == {"T2g": NOT_RUN, "T3g": NOT_RUN}
    assert "2027-28" in rec["extension_rule"]


def test_tampered_arm_is_refused():
    arm = fit_claim_arm(syn())
    verify_arm(arm)                                    # intact: fine
    with pytest.raises(RuntimeError, match="digest mismatch"):
        verify_arm(dict(arm, n_fit=arm["n_fit"] + 1))


def test_read_refuses_without_confirm_final():
    arm = fit_claim_arm(syn())
    with pytest.raises(RuntimeError, match="single claim read"):
        read(None, arm, confirm_final=False)


def test_read_refuses_unsettled_games(tmp_path):
    arm = fit_claim_arm(syn())

    class FakeCon:                                     # not a real DB: load path only
        pass

    import tools.phase2_read as P
    rows = syn()
    for r in rows:
        r["season"] = "2026-27"                        # the read's season
    rows[0]["home_win"] = None                         # one game still live
    orig = P.F.load_dataset
    P.F.load_dataset = lambda *a, **k: rows
    try:
        with pytest.raises(RuntimeError, match="unsettled"):
            read(FakeCon(), arm, confirm_final=True, out=tmp_path / "x.json")
    finally:
        P.F.load_dataset = orig


def test_score_produces_the_three_pre_registered_tiers():
    arm = fit_claim_arm(syn())
    rows = syn()
    labels_flip = {"open": {}, "t60": {}, "close": {}}
    for i, r in enumerate(rows):
        p = 0.7 if r["home_win"] else 0.3
        for kind, jitter in (("open", 0.0), ("t60", 0.01), ("close", -0.01)):
            labels_flip[kind][r["game_id"]] = min(0.99, max(0.01, p + jitter))
    tiers = score(rows, arm["formula"], labels_flip)
    assert set(tiers) == {"T2_prime_formula_at_T60_vs_opening",
                          "T2_double_prime_formula_at_T60_vs_market_at_T60",
                          "T3_formula_at_T60_vs_closing"}
    assert all(t["formula_vs"].get("n") == len(rows) for t in tiers.values())
