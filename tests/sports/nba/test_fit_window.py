"""GAP D regression: the frozen fit/tune/test window is a TESTED property, not prose.

Two independent layers, either of which catches a widened training window:

  * ``test_split_rows_respect_frozen_windows`` calls the real ``split_rows``
    helper on the real dataset and asserts the literal pre-registered windows
    (max(train season) <= "2020-21", tune == {"2021-22"}, test == the frozen
    four seasons). Touching the split constants fails here.
  * ``test_refit_reproduces_*`` refits A5 / A6 with the real ``fit_formula`` on
    the real training split (sklearn lbfgs is deterministic) and asserts every
    coefficient matches the frozen artifact to 1e-6. Widening the window changes
    the fit, so it fails here too - even if the split assertions were deleted.

The mutation probe used to find this gap replaced the first ``"2020-21"``
literal (the ``TRAIN_END`` constant) with ``"2022-23"``; that put a test season
into the training block and was previously invisible to the suite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sports.nba.db import paths
from sports.nba.model import formula as F

ROOT = Path(__file__).resolve().parents[3]
NBA_DATA = ROOT / "sports" / "nba" / "data"
A5_ARTIFACT = NBA_DATA / "formula_v1.json"
A6_ARTIFACT = NBA_DATA / "nba_availability_v1.json"

# Literal copies of the pre-registered windows (docs/preregistration.md §2). They
# are deliberately NOT read from F.TRAIN_END: a test that reads the constant it is
# meant to pin would move with the mutation it must catch.
TRAIN_END = "2020-21"
TUNE_SEASON = "2021-22"
TEST_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")


@pytest.fixture(scope="module")
def con() -> sqlite3.Connection:
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    c = sqlite3.connect(paths.DB, timeout=120)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_split_rows_respect_frozen_windows(con) -> None:
    rows = F.load_dataset(con)
    assert len(rows) > 10_000, "not enough feature rows to prove anything"
    parts = F.split_rows(rows)

    assert max(r["season"] for r in parts["train"]) <= TRAIN_END, \
        "a season past the frozen train end entered the fit window"
    assert {r["season"] for r in parts["tune"]} == {TUNE_SEASON}, \
        "the tuning window is not exactly the frozen season"
    assert {r["season"] for r in parts["test"]} == set(TEST_SEASONS), \
        "the test block is not exactly the frozen four seasons"

    train_ids = {r["game_id"] for r in parts["train"]}
    tune_ids = {r["game_id"] for r in parts["tune"]}
    test_ids = {r["game_id"] for r in parts["test"]}
    assert not (train_ids & tune_ids), "train/tune overlap"
    assert not (train_ids & test_ids), "train/test overlap - the forbidden peek"
    assert not (tune_ids & test_ids), "tune/test overlap"
    assert len(train_ids) + len(tune_ids) + len(test_ids) == len(rows), \
        "the three windows do not partition the dataset"


def test_refit_reproduces_published_a5_formula(con) -> None:
    """A widened window changes the coefficients, so they stop matching the artifact."""
    rows = F.load_dataset(con)
    train = F.split_rows(rows)["train"]
    model = F.fit_formula(train, F.FORMULA_FEATURES, c=1.0)
    artifact = json.loads(A5_ARTIFACT.read_text(encoding="utf-8"))

    assert model["n_train"] == artifact["n_train"], \
        "the published A5 coefficients were not fitted on the frozen train split"
    for name, published in artifact["coef_raw"].items():
        assert abs(model["coef_raw"][name] - published) <= 1e-6, \
            f"A5 coef {name} drifted from formula_v1.json ({published} -> {model['coef_raw'][name]})"
    assert abs(model["intercept_raw"] - artifact["intercept_raw"]) <= 1e-6, \
        "A5 intercept drifted from formula_v1.json"
    for name, published in artifact["means"].items():
        assert model["means"][name] == published, f"A5 standardiser mean {name} drifted"
    for name, published in artifact["sds"].items():
        assert model["sds"][name] == published, f"A5 standardiser sd {name} drifted"


def test_refit_reproduces_published_a6_formula(con) -> None:
    """The A6 arm must also be fitted on the frozen train split alone."""
    rows, _ = F.load_availability_dataset(con)
    assert rows, "no A6-eligible rows to refit"
    train = F.split_a6(rows)["train"]
    model = F.fit_formula(train, F.A6_FEATURES, c=F.A6_C)
    artifact = json.loads(A6_ARTIFACT.read_text(encoding="utf-8"))["formula_a6"]

    assert model["n_train"] == artifact["n_train"], \
        "the published A6 coefficients were not fitted on the frozen train split"
    for name, published in artifact["coef_raw"].items():
        assert abs(model["coef_raw"][name] - published) <= 1e-6, \
            f"A6 coef {name} drifted from nba_availability_v1.json ({published} -> {model['coef_raw'][name]})"
    assert abs(model["intercept_raw"] - artifact["intercept_raw"]) <= 1e-6, \
        "A6 intercept drifted from nba_availability_v1.json"
