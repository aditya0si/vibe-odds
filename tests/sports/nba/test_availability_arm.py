"""Availability arm A6 tests (Step 3): fit window, None-exclusion, A5 ledger intact.

All DB-backed tests read the real ``sports/nba/data/nba.sqlite`` (skipped when
v2 rows are absent) and never write: the A6 artifact writers are exercised by
``python -m sports.nba.model.formula --availability``, not here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from sports.nba.db import paths
from sports.nba.features.availability import AVAIL_KEYS
from sports.nba.model import formula as F

ROOT = Path(__file__).resolve().parents[3]
NBA_DATA = ROOT / "sports" / "nba" / "data"

# sha256 of the published A5 ledger (and its siblings) at the time A6 was
# added. A6 must never rewrite these files; any byte drift fails loudly here.
FROZEN_SHA256 = {
    "nba_walkforward_v1.json": "3940dc22bb209f4e55653a39451be004a4644eb877d0b927087f637d70a181de",
    "formula_v1.json": "cfe5d4804cea7b41adbd5122143312ca97d25277c6095c51015fd81fcf9af1c6",
    "nba_market_structure_v1.json": "bbf6103ec923b6acf07385deeeec9c9c86a6b08c611a7a11ef81b3ec59c2dee1",
}


def _full_payload(**overrides) -> dict:
    """A synthetic v2 payload with every A6 input present."""
    p = {
        "form_margin_home": 1.5, "form_margin_away": -0.5,
        "ortg_home": 112.0, "drtg_home": 109.0,
        "ortg_away": 110.0, "drtg_away": 110.5,
        "rest_home": 2, "rest_away": 1,
        "elo_diff": 25.0, "hca": 65.0, "b2b_away": 0, "is_neutral": 0,
        "avail_missing_home": 0.0, "avail_missing_away": 0.12,
        "avail_diff": 0.12,
        "avail_top_out_home": 0, "avail_top_out_away": 1,
        "avail_n_inactive_home": 0, "avail_n_inactive_away": 2,
    }
    p.update(overrides)
    return p


@pytest.fixture(scope="module")
def a6_dataset():
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        n_v2 = con.execute(
            "SELECT COUNT(*) n FROM features WHERE feature_version='v2'").fetchone()["n"]
    finally:
        pass
    if not n_v2:
        con.close()
        pytest.skip("feature_version=v2 rows not built yet "
                    "(python -m sports.nba.features.build --version v2)")
    rows, counts = F.load_availability_dataset(con)
    con.close()
    assert rows, "v2 rows exist but none are A6-eligible"
    return rows, counts


# ---- 1. fit window ----------------------------------------------------------

def test_a6_feature_set_is_a5_plus_availability() -> None:
    assert F.A6_FEATURES[: len(F.FORMULA_FEATURES)] == F.FORMULA_FEATURES
    assert tuple(F.A6_FEATURES[len(F.FORMULA_FEATURES):]) == tuple(AVAIL_KEYS)
    assert len(F.A6_FEATURES) == len(F.FORMULA_FEATURES) + 7


def test_a6_splits_respect_frozen_windows(a6_dataset) -> None:
    rows, _ = a6_dataset
    parts = F.split_a6(rows)
    assert set(parts) == {"train", "tune", "test"}
    assert all(r["season"] <= F.TRAIN_END for r in parts["train"]), \
        "A6 fit window leaked past 2020-21"
    assert all(r["season"] == F.TUNE_SEASON for r in parts["tune"])
    assert all(r["season"] in F.TEST_SEASONS for r in parts["test"])
    assert len(parts["train"]) > 10_000, "A6 fit must use the full training block"
    covered = sorted({r["season"] for r in parts["test"]})
    assert covered == sorted(F.TEST_SEASONS), f"test block incomplete: {covered}"
    train_ids = {r["game_id"] for r in parts["train"]}
    assert not (train_ids & {r["game_id"] for r in parts["tune"]}), "train/tune overlap"
    assert not (train_ids & {r["game_id"] for r in parts["test"]}), "train/test overlap"


def test_a6_fit_input_is_train_only(a6_dataset) -> None:
    """The model the artifact ships must be fitted on the train split alone."""
    rows, _ = a6_dataset
    train = F.split_a6(rows)["train"]
    model = F.fit_formula(train, F.A6_FEATURES, c=F.A6_C)
    assert model["n_train"] == len(train)
    assert model["train_end"] == F.TRAIN_END
    artifact = json.loads((NBA_DATA / "nba_availability_v1.json").read_text(encoding="utf-8"))
    assert artifact["formula_a6"]["n_train"] == len(train), \
        "published A6 coefficients were not fitted on the train window alone"


# ---- 2. None rows excluded, never imputed -----------------------------------

@pytest.mark.parametrize("key", list(AVAIL_KEYS))
def test_single_none_avail_key_excludes_row(key: str) -> None:
    assert F.is_a6_eligible(_full_payload()) is True
    payload = _full_payload(**{key: None})
    assert F.is_a6_eligible(payload) is False, \
        f"{key}=None must exclude the row, never be imputed"
    assert payload[key] is None, "eligibility check must not fill in (impute) the None"


def test_loader_excludes_none_rows_without_imputing(a6_dataset) -> None:
    rows, counts = a6_dataset
    for r in rows:
        assert all(r["x"][k] is not None for k in F.A6_FEATURES)
        for k in AVAIL_KEYS:
            assert r[k] is not None, f"{r['game_id']}: None {k} slipped into the A6 set"
    assert counts["excluded_avail_none"] >= 0
    assert counts["eligible"] == len(rows)
    assert counts["eligible"] + counts["excluded_no_history"] + counts["excluded_avail_none"] \
        == counts["v2_rows_scanned"]


def test_zero_is_signal_not_unknown() -> None:
    """0.0 (authoritative empty list) is eligible; None (unknown) is not."""
    assert F.is_a6_eligible(_full_payload(avail_missing_home=0.0,
                                          avail_n_inactive_home=0)) is True
    assert F.is_a6_eligible(_full_payload(avail_missing_home=None)) is False


# ---- 3. published A5 artifacts byte-identical --------------------------------

@pytest.mark.parametrize("name,expected", sorted(FROZEN_SHA256.items()))
def test_published_a5_artifacts_byte_identical(name: str, expected: str) -> None:
    p = NBA_DATA / name
    assert p.exists(), f"missing published artifact: {name}"
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    assert got == expected, (
        f"{name} changed ({expected} -> {got}). The A6 work must never rewrite "
        "the published A5 ledger, coefficients, or market-structure files."
    )


def test_a6_artifact_labels_itself_exploratory() -> None:
    for name in ("nba_availability_v1.json", "nba_ablation_v1.json"):
        artifact = json.loads((NBA_DATA / name).read_text(encoding="utf-8"))
        assert artifact["disclaimer"] == "exploratory arm, not a registered claim", name
        assert artifact["exploratory"] is True, name
    avail = json.loads((NBA_DATA / "nba_availability_v1.json").read_text(encoding="utf-8"))
    assert "T2-prime" in avail["paired_tests"]["A6_vs_market_open"]["comparison"]
    assert "T3-prime" in avail["paired_tests"]["A6_vs_market_close"]["comparison"]
    assert isinstance(avail["verdict"], str) and avail["verdict"]
