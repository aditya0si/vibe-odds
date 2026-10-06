"""Retrospective harness labels (plan Task 7): burned means burned.

The 2022-23..2025-26 window can only ever be re-scored as a retrospective
estimate (docs/preregistration.md §13). These tests pin the mandatory labels.
"""
from __future__ import annotations

import json

from sports.nba.model.retro import LABEL, RETRO_SEASONS, retro_record

OK_PAIRED = {"formula_vs_close": {"n": 10, "mean_diff": -0.01, "sigma_d": 0.08,
                                  "ci95_blocked": [-0.02, 0.0], "blocks": 1}}


def test_retro_header_is_mandatory():
    rec = retro_record(10, {"A7_formula": 0.21}, OK_PAIRED)
    assert rec["burned_test_set"] is True
    assert rec["claim_eligible"] is False
    assert "RETROSPECTIVE" in rec["label"] and "may ever be published as a claim test" in LABEL


def test_retro_record_cannot_produce_a_claim():
    """Even a caller passing claim-ish junk cannot flip the labels: they are set
    inside retro_record, after any caller input."""
    rec = retro_record(5, {"A7_formula": 0.2}, OK_PAIRED,
                       hedge_weights={"formula": 0.5})
    assert rec["claim_eligible"] is False and rec["burned_test_set"] is True
    assert rec["arm_fits"]["retro_seasons"] == list(RETRO_SEASONS)
    # the label names the exact burned window
    assert "2022-23" in rec["label"] and "2025-26" in rec["label"]


def test_written_json_carries_the_labels(tmp_path):
    path = tmp_path / "nba_phase2_retro.json"
    rec = retro_record(3, {"A7_formula": 0.21}, OK_PAIRED)
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back["claim_eligible"] is False
    assert back["burned_test_set"] is True
    assert back["paired"]["formula_vs_close"]["n"] == 10


def test_retro_only_names_the_four_burned_seasons():
    assert RETRO_SEASONS == ("2022-23", "2023-24", "2024-25", "2025-26")


def test_retro_a9_writes_a_labeled_record(tmp_path, monkeypatch):
    """The Phase-3 retro path goes through the same labeling builder (no DB needed:
    the fits/markets are stubbed; the labels and the record shape are the real ones)."""
    import sports.nba.model.retro as R
    from sports.nba.model import formula as F
    from sports.nba.model import phase3 as P3

    rows = [{"game_id": f"g{i}", "season": "2022-23", "game_date": "2023-01-01",
             "home_win": 1, "x": {}} for i in range(3)]
    monkeypatch.setattr(F, "load_dataset", lambda con, version=None, feature_names=None: rows)
    monkeypatch.setattr(P3, "load_merged_dataset", lambda con, feature_names=None: rows)
    monkeypatch.setattr(F, "fit_formula", lambda rows_, feats=None: {})
    monkeypatch.setattr(F, "predict", lambda formula, row: 0.5)
    monkeypatch.setattr(F, "market_probs", lambda con, kind=None, books_only=True: {})
    monkeypatch.setattr(F, "paired_stats",
                        lambda pa, pb, labels, games, dates, block=10: {"n": 0})

    out = tmp_path / "nba_phase3_retro_a9.json"
    rec = R.retro_a9(None, out=out)
    assert rec["burned_test_set"] is True and rec["claim_eligible"] is False
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["burned_test_set"] is True
    assert set(back["arms_brier"]) >= {"A9_formula", "A9b_formula", "A7_formula"}
    assert back["arms_brier"]["A9_formula"] == 0.25          # (0.5-1)^2 per game


def test_retro_a11_writes_a_labeled_record(tmp_path, monkeypatch):
    """The A11 retro path goes through the same labeling builder (stubbed fits; real labels)."""
    import sports.nba.model.retro as R
    from sports.nba.model import formula as F

    rows = [{"game_id": f"g{i}", "season": "2022-23", "game_date": "2023-01-01",
             "home_win": 1, "x": {}} for i in range(3)]
    monkeypatch.setattr(F, "load_dataset", lambda con, version=None, feature_names=None: rows)
    monkeypatch.setattr(F, "fit_formula", lambda rows_, feats=None: {})
    monkeypatch.setattr(F, "predict", lambda formula, row: 0.5)
    monkeypatch.setattr(F, "market_probs", lambda con, kind=None, books_only=True: {})
    monkeypatch.setattr(F, "paired_stats",
                        lambda pa, pb, labels, games, dates, block=10: {"n": 0})

    out = tmp_path / "nba_phase3_retro_a11.json"
    rec = R.retro_a11(None, out=out)
    assert rec["burned_test_set"] is True and rec["claim_eligible"] is False
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["burned_test_set"] is True
    assert set(back["arms_brier"]) >= {"A11_formula", "A7_formula"}
    assert back["arms_brier"]["A11_formula"] == 0.25
