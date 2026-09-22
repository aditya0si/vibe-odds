"""Report + trainer core contracts (map step 13)."""

from __future__ import annotations

from core.report import ablation_rows, log_json_list, recent_json, upset_patterns
from core.trainers import (brier_pairs, fit_logistic_scale, logit, refit_isotonic,
                           sigmoid)


def test_log_json_list_caps_and_reads_back(tmp_path):
    p = tmp_path / "u.json"
    log_json_list(p, {"i": 1}, cap=2)
    log_json_list(p, {"i": 2}, cap=2)
    log_json_list(p, {"i": 3}, cap=2)
    assert recent_json(p, 5) == [{"i": 3}, {"i": 2}]   # newest first, cap=2
    assert recent_json(tmp_path / "missing.json", 5) == []


def test_upset_patterns_counts():
    reports = [{"surface": "hard", "wrong": [{"signal": "gbm"}, {"signal": "elo"}]},
               {"surface": "hard", "wrong": [{"signal": "gbm"}]}]
    pat = upset_patterns(reports)
    assert pat["n_upsets"] == 2
    assert pat["by_surface"] == {"hard": 2}
    assert pat["wrong_counts"] == {"gbm": 2, "elo": 1}
    assert list(pat["wrong_counts"]) == ["gbm", "elo"]  # sorted by count desc


def test_logit_sigmoid_roundtrip():
    assert abs(sigmoid(logit(0.7)) - 0.7) < 1e-3
    assert logit(0.5) == 0.0


def test_brier_pairs():
    assert abs(brier_pairs([0.7, 0.4], [1, 0]) - (0.3 ** 2 + 0.4 ** 2) / 2) < 1e-12


def test_fit_logistic_scale_shapes():
    ps = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9] * 20
    ys = [0, 0, 0, 1, 1, 1] * 20
    a, b = fit_logistic_scale(ps, ys, 1e6)
    assert a > 0.0                      # monotone direction survives
    at, bt = fit_logistic_scale(ps, ys, 1.0)
    assert isinstance(at, float) and isinstance(bt, float)


def test_refit_isotonic_report_and_thin():
    ps = [0.1, 0.2, 0.8, 0.9] * 30
    ys = [0, 0, 1, 1] * 30
    report, fn = refit_isotonic(ps, ys, 50)
    assert report["n"] == 120 and fn is not None
    assert "ece_raw" in report and "ece_cal" in report
    thin, fn2 = refit_isotonic([0.5], [1], 50)
    assert fn2 is None and thin == {"n": 1, "status": "fallback-global (too few)"}


def test_ablation_rows_delta_vs_full():
    acc = {"full": [8, 10], "no_x": [7, 10]}
    bri = {"full": [2.0, 10], "no_x": [2.5, 10]}
    rows, base = ablation_rows(acc, bri)
    assert base == 0.2
    assert rows[0]["variant"] == "no_x" and rows[0]["delta_brier"] == 0.05
