"""The as-of property test: rebuilding features from a history truncated at an earlier date must
reproduce every overlapping row exactly. This is the mechanical proof that a feature row cannot
see the game it describes (or anything after it)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from sports.nba.db import paths
from sports.nba.features import build as B


@pytest.fixture(scope="module")
def con() -> sqlite3.Connection:
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    c = sqlite3.connect(paths.DB, timeout=60)
    c.row_factory = sqlite3.Row          # load_games() indexes rows by column name
    return c


def test_feature_pass_is_as_of(con) -> None:
    games = B.load_games(con)
    if len(games) < 100:
        pytest.skip("not enough games ingested")
    cut = games[int(len(games) * 0.6)]["game_date"]
    full, _ = B.build_rows(con, verbose=False)
    truncated, _ = B.build_rows(con, upto=cut, verbose=False)

    overlap = [gid for gid, row in truncated.items() if row["game_date"] <= cut]
    assert overlap, "truncated build produced no overlapping rows"
    mismatches = [gid for gid in overlap
                  if json.dumps(full[gid], sort_keys=True) != json.dumps(truncated[gid], sort_keys=True)]
    assert not mismatches, (
        f"{len(mismatches)} feature rows changed when the future was removed "
        f"(e.g. {mismatches[:3]}) - that is leakage")
