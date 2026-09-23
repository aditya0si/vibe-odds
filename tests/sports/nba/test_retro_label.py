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
