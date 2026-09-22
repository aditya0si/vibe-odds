"""Sim core: reducer contracts + the paper-trading leg (map step 12)."""

from __future__ import annotations

from core.sim import EvModel, block_ci, conf_table, per_surface, sign_test, summ

# 7-tuple rows: (date, surface, brier, acc, p, y, tourney)
HIST_A = [(20240101, "hard", 0.10, True, 0.9, 1, "AO"),
          (20240201, "clay", 0.40, False, 0.4, 1, "RG"),
          (20240301, "hard", 0.25, True, 0.8, 1, "IW")]
HIST_B = [(20240101, "hard", 0.20, True, 0.8, 1, "AO"),
          (20240201, "clay", 0.30, True, 0.7, 1, "RG"),
          (20240301, "hard", 0.25, True, 0.7, 1, "IW")]


def test_summ_reduces_hist():
    s = summ(HIST_A)
    assert s["n"] == 3
    assert s["brier"] == round((0.10 + 0.40 + 0.25) / 3, 4)
    assert s["acc"] == round(2 / 3, 4)
    assert summ([None, None]) == {"n": 0, "acc": 0, "brier": 0}


def test_summ_handles_market_3tuples():
    """Market rows are (date, brier, acc) — the pre-step-12 summ crashed on them."""
    s = summ([(20240101, 0.20, True), (20240201, 0.40, False)])
    assert s == {"n": 2, "acc": 0.5, "brier": 0.3}


def test_per_surface_and_conf_table():
    ps = per_surface(HIST_A)
    assert ps["hard"]["n"] == 2 and ps["clay"]["n"] == 1
    ct = conf_table(HIST_A)
    # conf = max(p, 1-p): 0.9/0.4/0.8 -> 0.9/0.6/0.8
    assert ct[">=0.55"]["n"] == 3
    assert ct[">=0.6"]["n"] == 3     # f"{0.60}" renders "0.6"
    assert ct[">=0.65"]["n"] == 2


def test_block_ci_is_seeded_and_labeled():
    r1 = block_ci(HIST_A, HIST_B, "a-vs-b")
    r2 = block_ci(HIST_A, HIST_B, "a-vs-b")
    assert r1 == r2                    # deterministic seed
    assert r1["arms"] == "a-vs-b" and r1["n"] == 3
    assert len(r1["ci95"]) == 2


def test_sign_test_shape():
    st = sign_test(HIST_A, HIST_B)
    assert st["n"] == 3 and st["wins"] == 1 and st["ties"] == 1
    assert set(st) == {"n", "wins", "ties", "z", "test", "verdict"}


def test_ev_model_line_and_score_row():
    ev = EvModel()
    assert ev.line_for(0.6, 0.5) == (0.6, "market")
    assert ev.line_for(None, 0.5) == (0.5, "elo")
    assert ev.line_for(0.99, 0.5) == (0.97, "market")   # clamp
    t = ev.score_row(0.7, 0.6, y=1)
    assert t["pick_a"] and t["p_pick"] == 0.7
    assert t["odds"] == 1 / 0.6
    assert abs(t["edge"] - (0.7 / 0.6 - 1)) < 1e-12
    assert t["conf"] == 0.7 and t["won"] is True
    assert abs(t["profit"] - t["stake"] * (t["odds"] - 1.0)) < 1e-12
    lose = ev.score_row(0.7, 0.6, y=0)
    assert lose["won"] is False and lose["profit"] == -lose["stake"]
