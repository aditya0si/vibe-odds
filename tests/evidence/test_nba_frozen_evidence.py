"""Guard the NBA evidence exactly like the tennis evidence.

The NBA artifacts live in ``sports/nba/data/``, which is **gitignored**. Freezing
therefore works in two layers:

* ``nba_frozen_manifest.json`` (tracked) pins the sha256 and byte size of every
  NBA evidence artifact on disk. The bytes are rebuilt deterministically by the
  pipeline, so any drift makes the guard fail loudly.
* The published numbers (``nba_published_numbers.py``) are **re-derived** from
  the JSON here, never typed twice. A ledger edit that changes a headline number
  fails even if someone forgets to rebaseline the hash.

REBASELINE RULE — a number may only move in a commit that says why:
    regenerate ``nba_frozen_manifest.json`` and update the published literals
    (docs/site import them), e.g.
    ``refit: sigma_d locked, T2 mean -0.00681 -> -0.0065``.
    Silent drift is exactly what this guard exists to stop.

A pure hash rebaseline can still launder a *stale* artifact (one regenerated
mid-ingest or before a data-arm fix) that no longer agrees with the claim
ledger. ``test_formula_report_agrees_with_ledger`` therefore checks internal
consistency between the frozen artifacts without re-running the fit: every
metric shared by ``formula_v1_report.json`` and ``nba_walkforward_v1.json`` must
be equal, and the report's per-season market row counts must sum to the ledger's
closing-arm game count. A full determinism re-fit remains Step 9 work.

The protected *tennis* files are already byte-checked by
``test_frozen_evidence.py`` and are deliberately not duplicated here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from tests.evidence import nba_published_numbers as NB

ROOT = Path(__file__).resolve().parents[2]
NBA_DATA = ROOT / "sports" / "nba" / "data"
MANIFEST = Path(__file__).resolve().parent / "nba_frozen_manifest.json"
PREREG = ROOT / "docs" / "preregistration.md"

WF = "nba_walkforward_v1.json"
MS = "nba_market_structure_v1.json"
AV = "nba_availability_v1.json"
FV = "formula_v1.json"
FR = "formula_v1_report.json"

MAN = json.loads(MANIFEST.read_text(encoding="utf-8"))
ARTIFACTS: dict[str, dict] = MAN["files"]


def _load(name: str) -> dict:
    return json.loads((NBA_DATA / name).read_text(encoding="utf-8"))


# ---- layer 1: the tracked manifest pins the bytes --------------------------

def verify_artifact(path: Path, meta: dict) -> None:
    """Fail loudly if ``path`` is missing or not the frozen bytes."""
    assert path.exists(), f"missing frozen NBA artifact: {path}"
    assert path.stat().st_size == meta["bytes"], (
        f"{path.name} size changed ({meta['bytes']} -> {path.stat().st_size})"
    )
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    assert got == meta["sha256"], (
        f"{path.name} changed ({meta['sha256']} -> {got}). If a published number "
        "moved, rebaseline tests/evidence/nba_frozen_manifest.json in the same "
        "commit and update nba_published_numbers.py (docs/site import it)."
    )


@pytest.mark.parametrize("rel,meta", sorted(ARTIFACTS.items()))
def test_nba_artifact_bytes_unchanged(rel: str, meta: dict) -> None:
    verify_artifact(ROOT / rel, meta)


def test_manifest_covers_every_nba_evidence_json() -> None:
    """A new *.json artifact cannot appear without being frozen."""
    present = {
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in NBA_DATA.rglob("*.json")
        if "cache" not in p.parts
    }
    unknown = sorted(present - set(ARTIFACTS))
    assert not unknown, (
        f"unfrozen NBA evidence artifact(s): {unknown}. Add them to "
        "tests/evidence/nba_frozen_manifest.json (cache/ and *.failures.jsonl "
        "are operational logs and must not be frozen)."
    )
    missing = sorted(set(ARTIFACTS) - present)
    assert not missing, f"manifest lists absent artifacts: {missing}"


# ---- layer 2: published numbers are re-derived, not typed -------------------
#
# Each checker takes the expected literals as arguments so the negative tests
# can hand it a deliberately wrong value and prove the guard trips.

def check_t1(wf: dict, *, passes: bool, formula_brier: float, clim_brier: float,
             elo_brier: float, clim_diff: float, clim_ci: tuple,
             elo_diff: float, elo_ci: tuple) -> None:
    t = wf["tiers"]["T1_beats_naive_baselines"]
    assert t["passes"] is passes
    assert t["formula_brier"] == formula_brier
    assert t["climatological_brier"] == clim_brier
    assert t["elo_only_brier"] == elo_brier
    assert formula_brier < clim_brier, "T1 requires the formula to beat climatology"
    assert formula_brier < elo_brier, "T1 requires the formula to beat Elo-only"
    s = t["supporting_paired_tests"]
    assert s["vs_climatological"]["mean_diff"] == clim_diff
    assert tuple(s["vs_climatological"]["ci95_blocked"]) == tuple(clim_ci)
    assert s["vs_elo_only"]["mean_diff"] == elo_diff
    assert tuple(s["vs_elo_only"]["ci95_blocked"]) == tuple(elo_ci)


def check_tier(wf: dict, key: str, *, passes: bool, n_games: int,
               mean_diff: float, ci: tuple) -> None:
    t = wf["tiers"][key]
    assert t["passes"] is passes
    assert t["n_games"] == n_games
    assert t["formula_vs"]["n"] == n_games
    assert t["formula_vs"]["mean_diff"] == mean_diff
    assert tuple(t["formula_vs"]["ci95_blocked"]) == tuple(ci)
    assert (mean_diff > 0) is passes, "verdict must follow the CI sign, not prose"


def check_market_structure(ms: dict, *, formula_brier: float, open_brier: float,
                           close_brier: float, o2c_diff: float,
                           o2c_ci: tuple) -> None:
    lv = ms["brier_levels"]
    assert lv["formula"]["brier"] == formula_brier
    assert lv["market_open"]["brier"] == open_brier
    assert lv["market_close"]["brier"] == close_brier
    assert close_brier < open_brier < formula_brier, (
        "the published ordering is close < open < formula"
    )
    o2c = ms["market_open_improves_to_close"]
    assert o2c["mean_diff"] == o2c_diff
    assert tuple(o2c["ci95_blocked"]) == tuple(o2c_ci)
    assert o2c_diff < 0, "the market must improve (Brier falls) from open to close"


def check_availability(av: dict, *, verdict: str, a5_gap: float,
                       a6_gap: float) -> None:
    g = av["brier_gap_to_open"]
    assert round(g["A5_minus_open"], 5) == a5_gap
    assert round(g["A6_minus_open"], 5) == a6_gap
    assert a6_gap < a5_gap, "availability must narrow, not widen, the gap"
    assert av["verdict"] == verdict
    assert f"{a5_gap:.5f}" in av["verdict"], "verdict must quote the A5 gap"
    assert f"{a6_gap:.5f}" in av["verdict"], "verdict must quote the A6 gap"


def check_windows(fv: dict, fr: dict, wf: dict, *, train_end: str, tune: str,
                  test_seasons: tuple, sigma_d_current: float, sigma_d_n: int) -> None:
    """The report's 2021-22 sigma is the CURRENT books-only recompute.

    The registered lock (``NB.SIGMA_D_LOCK``) is checked against its source
    (docs/preregistration.md §5) in ``test_sigma_d_lock_matches_preregistration``;
    it is deliberately NOT re-derived from this artifact, which moved when the
    market arm was decontaminated.
    """
    assert fv["train_end"] == train_end
    assert fv["tuned_on"] == tune
    assert wf["formula"]["train_end"] == train_end
    assert wf["formula"]["tuned_on"] == tune
    assert tuple(wf["formula"]["test_seasons"]) == tuple(test_seasons)
    current = fr["tuning_season_paired_formula_vs_market"]
    assert current["sigma_d"] == sigma_d_current
    assert current["n"] == sigma_d_n


def check_report_ledger_consistency(fr: dict, wf: dict) -> None:
    """Cheap staleness guard: no re-fit, only cross-artifact consistency.

    ``formula_v1_report.json`` and ``nba_walkforward_v1.json`` are produced by
    the same ``--fit``/``--evaluate`` code path on the same frozen test block, so
    every arm metric they share must be identical. A report regenerated
    mid-ingest (e.g. odds for the newest seasons not yet loaded) or before the
    market-arm contamination fix silently disagrees with the ledger — and would
    otherwise be laundered by a hash rebaseline. A full determinism re-fit is
    Step 9 work.
    """
    shared_arms = ("formula", "market_close", "elo_only", "climatological")
    for arm in shared_arms:
        assert fr["pooled_test"][arm] == wf["pooled_with_close"][arm], (
            f"formula_v1_report pooled_test[{arm}] disagrees with the ledger's "
            "pooled_with_close (same arm, same test block): stale report?"
        )
    assert fr["pooled_test_paired_formula_vs_market"] == (
        wf["tiers"]["T3_beats_closing_line"]["formula_vs"]
    ), "report vs-ledger T3 paired test drifted (same arm, same test block)"

    test_seasons = tuple(wf["formula"]["test_seasons"])
    market_n = {
        season: fr["by_season"][season]["market_close (market)"]["n"]
        for season in test_seasons
    }
    assert all(n > 0 for n in market_n.values()), (
        f"a test season has no market rows in the report: {market_n}"
    )
    assert sum(market_n.values()) == wf["n_test_games"]["with_close"], (
        f"per-season market rows {market_n} sum to {sum(market_n.values())}, but "
        f"the ledger closes {wf['n_test_games']['with_close']} games"
    )


def test_formula_report_agrees_with_ledger() -> None:
    """A stale (mid-ingest / pre-fix) report fails this even after a rebaseline."""
    check_report_ledger_consistency(_load(FR), _load(WF))


def test_t1_verdict_re_derived() -> None:
    check_t1(
        _load(WF),
        passes=NB.T1_PASSES,
        formula_brier=NB.T1_FORMULA_BRIER,
        clim_brier=NB.T1_CLIMATOLOGICAL_BRIER,
        elo_brier=NB.T1_ELO_ONLY_BRIER,
        clim_diff=NB.T1_VS_CLIMATOLOGICAL_MEAN_DIFF,
        clim_ci=NB.T1_VS_CLIMATOLOGICAL_CI,
        elo_diff=NB.T1_VS_ELO_MEAN_DIFF,
        elo_ci=NB.T1_VS_ELO_CI,
    )


def test_t2_t3_verdicts_re_derived() -> None:
    wf = _load(WF)
    check_tier(wf, "T2_beats_opening_line", passes=NB.T2_PASSES,
               n_games=NB.T2_N_GAMES, mean_diff=NB.T2_MEAN_DIFF, ci=NB.T2_CI)
    check_tier(wf, "T3_beats_closing_line", passes=NB.T3_PASSES,
               n_games=NB.T3_N_GAMES, mean_diff=NB.T3_MEAN_DIFF, ci=NB.T3_CI)


def test_market_structure_re_derived() -> None:
    check_market_structure(
        _load(MS),
        formula_brier=NB.MARKET_FORMULA_BRIER,
        open_brier=NB.MARKET_OPEN_BRIER,
        close_brier=NB.MARKET_CLOSE_BRIER,
        o2c_diff=NB.MARKET_OPEN_TO_CLOSE_MEAN_DIFF,
        o2c_ci=NB.MARKET_OPEN_TO_CLOSE_CI,
    )


def test_availability_verdict_re_derived() -> None:
    check_availability(_load(AV), verdict=NB.A6_VERDICT,
                       a5_gap=NB.A5_GAP_TO_OPEN, a6_gap=NB.A6_GAP_TO_OPEN)


def test_availability_pooled_briers_re_derived() -> None:
    pooled = _load(AV)["pooled_test_A5_vs_A6"]
    assert pooled["A5"]["brier"] == NB.A5_POOLED_BRIER
    assert pooled["A6"]["brier"] == NB.A6_POOLED_BRIER
    assert pooled["A6"]["brier"] < pooled["A5"]["brier"], "A6 must improve on A5"


def test_sigma_d_lock_and_windows_re_derived() -> None:
    check_windows(
        _load(FV), _load(FR), _load(WF),
        train_end=NB.TRAIN_END, tune=NB.TUNE_SEASON,
        test_seasons=NB.TEST_SEASONS,
        sigma_d_current=NB.SIGMA_D_CURRENT_CODE, sigma_d_n=NB.SIGMA_D_LOCK_N,
    )


def test_sigma_d_lock_matches_preregistration() -> None:
    """The registered lock is asserted against docs/preregistration.md §5, not
    against the artifact: the report now carries the books-only recompute
    (``SIGMA_D_CURRENT_CODE``) after the market-arm decontamination. This test
    deliberately does not edit the pre-registration (the owner logs that)."""
    text = PREREG.read_text(encoding="utf-8")
    m = re.search(
        r"σ_d \(formula A5 vs market close\)\s*\|\s*\*\*([0-9.]+)\*\*\s*"
        r"\(n\s*=\s*([0-9,]+)\)",
        text,
    )
    assert m, "docs/preregistration.md §5 no longer records the sigma_d lock"
    assert float(m.group(1)) == NB.SIGMA_D_LOCK
    assert int(m.group(2).replace(",", "")) == NB.SIGMA_D_LOCK_N
    assert NB.SIGMA_D_CURRENT_CODE != NB.SIGMA_D_LOCK, (
        "the lock and the current recompute are expected to differ here; if they "
        "converge, collapse the two names in nba_published_numbers.py deliberately"
    )


# ---- layer 3: prove the guard trips on demand ------------------------------

@pytest.mark.parametrize("rel", sorted(ARTIFACTS))
def test_negative_corrupt_tmp_copy_fails_hash(rel: str, tmp_path: Path) -> None:
    """A one-byte edit in a tmp copy must fail the hash check, never touch disk."""
    meta = ARTIFACTS[rel]
    dst = tmp_path / Path(rel).name
    dst.write_bytes((ROOT / rel).read_bytes())
    verify_artifact(dst, meta)  # a clean copy is byte-identical (so the test is real)
    corrupted = bytearray(dst.read_bytes())
    corrupted[len(corrupted) // 2] ^= 0x01
    dst.write_bytes(bytes(corrupted))
    with pytest.raises(AssertionError):
        verify_artifact(dst, meta)


def test_negative_inconsistent_report_trips() -> None:
    """A report that drops a season's market rows must fail the consistency check."""
    bad = json.loads(json.dumps(_load(FR)))
    bad["by_season"]["2024-25"]["market_close (market)"] = {"n": 0}
    with pytest.raises(AssertionError):
        check_report_ledger_consistency(bad, _load(WF))


def test_negative_wrong_published_literal_trips() -> None:
    """The re-derivation checks really compare against the artifact."""
    wf = _load(WF)
    with pytest.raises(AssertionError):
        check_tier(wf, "T2_beats_opening_line", passes=False,
                   n_games=NB.T2_N_GAMES,
                   mean_diff=NB.T2_MEAN_DIFF + 0.001, ci=NB.T2_CI)
    with pytest.raises(AssertionError):
        check_availability(_load(AV), verdict=NB.A6_VERDICT,
                           a5_gap=0.0, a6_gap=NB.A6_GAP_TO_OPEN)
