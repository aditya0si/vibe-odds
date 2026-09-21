"""Guard: frozen evidence and every published number must never drift silently.

This repo publishes numbers (README, the site, ``backend/api/analytics.py`` prose)
that are derived from files under ``data/``. Those files are *evidence*: if one
changes, a published claim changed. This module makes that failure loud:

* ``frozen_manifest.json`` pins sha256[:16] of every git-tracked file under ``data/``.
* The ledger/prose tests re-derive the published numbers from the JSON and assert
  the prose still contains them, so a silent ledger edit fails even if someone
  forgets the hash.

REBASELINE RULE — a number may only move in a commit that says why:
    regenerate ``frozen_manifest.json``, update the README/site/prose literals,
    and put the reason in the message, e.g.
    ``retrain: gbm sha fd1cd137->9a2b..., sim re-run: brier 0.2141 -> 0.2139``.
    Silent drift is exactly what this guard exists to stop.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
MANIFEST = Path(__file__).resolve().parent / "frozen_manifest.json"
MINUS = "\u2212"  # the README/ledger prose uses a unicode minus, not ASCII "-"

MAN = json.loads(MANIFEST.read_text(encoding="utf-8"))
ARTIFACTS: dict[str, str] = MAN["files"]


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _signed(x: float, places: int = 5) -> str:
    """Format like the published prose: unicode minus, fixed places."""
    return f"{x:.{places}f}".replace("-", MINUS)


def _ledger() -> dict:
    return json.loads((DATA / "sim_2024_2025.json").read_text(encoding="utf-8"))


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("rel,expected", sorted(ARTIFACTS.items()))
def test_evidence_bytes_unchanged(rel: str, expected: str) -> None:
    p = ROOT / rel
    assert p.exists(), f"missing frozen artifact: {rel}"
    got = _sha16(p)
    assert got == expected, (
        f"{rel} changed ({expected} -> {got}). If this is a deliberate retrain or "
        "sim re-run, rebaseline tests/evidence/frozen_manifest.json in the same commit "
        "and update the README/site numbers it feeds."
    )


def test_frozen_manifest_matches_sources() -> None:
    man = json.loads((DATA / "sim_frozen" / "manifest.json").read_text(encoding="utf-8"))
    assert man["cutoff"] == 20240101
    for name, sha in man["files"].items():
        if sha is None:
            continue
        src = DATA / name
        assert hashlib.sha256(src.read_bytes()).hexdigest()[:12] == sha, name


def test_sim_ledger_matches_readme() -> None:
    """Every published number is re-derived from the frozen ledger, not trusted."""
    led, readme = _ledger(), _readme()
    arms = led["arms"]
    assert set(arms) == {"adaptive", "frozen", "gbm", "elo"}
    for arm, a in arms.items():
        assert f"{a['acc'] * 100:.2f}%" in readme, f"README lost {arm} accuracy"
        assert f"{a['brier']:.4f}" in readme, f"README lost {arm} Brier"

    for key in ("paired_mean", "gbm_vs_adaptive", "gbm_vs_frozen"):
        d = led[key]
        lo, hi = d["ci95"]
        assert _signed(d["mean_diff"]) in readme, f"README lost {key} mean diff"
        assert f"[{_signed(lo)},{_signed(hi)}]" in readme, f"README lost {key} CI"

    conf = led["confidence"]["adaptive"]
    for thr in (">=0.6", ">=0.65"):
        c = conf[thr]
        assert f"{c['acc'] * 100:.1f}%" in readme, f"README lost confidence acc {thr}"
        assert f"n={c['n']}" in readme, f"README lost confidence n {thr}"

    for policy in ("bandit", "ev003", "post_all"):
        assert f"+{led['policies'][policy]['profit_u']:.2f}u" in readme, policy

    t = json.loads((DATA / "tourney.json").read_text(encoding="utf-8"))
    b = t["improvement_vs_A"]["B"]
    assert f"+{b['mean']:.5f}" in readme, "README lost the column-set tournament gain"
    assert f"{b['positive_folds']}/{len(b['per_fold'])} folds positive" in readme

    bet = led["betting"]
    assert str(bet["n_posts"]) in readme, "README lost the paper-trading post count"
    assert f"{bet['roi_on_staked_pct']:.1f}%" in readme, "README lost ROI-on-staked"

    assert "49 modelling tests" in readme, "README must state the test-count claim"


def test_evidence_numbers_re_derived_from_files() -> None:
    cal = json.loads((DATA / "cal_validation.json").read_text(encoding="utf-8"))
    none = cal["methods"]["none"]
    assert none["brier"] == _ledger()["arms"]["gbm"]["brier"], "calibration ledger disagrees with sim ledger"
    assert f"ECE {none['ece']:.4f}" in _readme()
    for method in ("temp", "logistic", "isotonic"):
        assert cal["methods"][method]["adopt"] is False, f"{method} must stay killed"


def test_published_literals_in_code_and_site_are_consistent() -> None:
    """The same numbers are duplicated in prose: code and site must agree."""
    ana = (ROOT / "backend/api/analytics.py").read_text(encoding="utf-8")
    site = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    led, readme = _ledger(), _readme()
    cal = json.loads((DATA / "cal_validation.json").read_text(encoding="utf-8"))
    t = json.loads((DATA / "tourney.json").read_text(encoding="utf-8"))
    gain = f"+{t['improvement_vs_A']['B']['mean']:.5f}"

    assert f"{led['arms']['gbm']['brier']:.4f}" in ana
    assert f"ECE {cal['methods']['none']['ece']:.4f}" in ana
    assert gain in ana and gain in site

    hi = led["confidence"]["adaptive"][">=0.65"]
    assert f"{hi['acc'] * 100:.1f}%" in site
    assert f"n={hi['n']}" in site
    assert f"+{led['policies']['bandit']['profit_u']:.2f}u" in site
    assert f"+{led['policies']['ev003']['profit_u']:.2f}u" in site
    assert f"+{led['policies']['post_all']['profit_u']:.2f}u" in readme
