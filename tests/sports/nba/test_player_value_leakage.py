"""GAP F regression for the v5 player-value pass: the as-of guarantee is proved by DELETION.

``build_player_value(upto=cut)`` shares its DB with the full build, so a state pre-loaded
from the future can hide in both runs. This test removes the future instead: it copies the
source DB, physically deletes every row dated after a cut (games + children), and rebuilds.
Any future state (box rows, inactive lists, season refits, cumulative exposure) folded in
ahead of the chronological loop changes the rows for games <= the cut and is caught.

Two cuts in different eras (small refit windows vs large), same as the v2 deletion test.
Each truncated build is a full player-value pass (~40 s), so this is among the slowest
tests in the NBA suite - by design.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from sports.nba.db import paths
from sports.nba.features import player_value as PV

CUTS = ("2013-01-02", "2021-01-11")

# Tables that carry a game's as-of inputs, in FK-safe deletion order (games itself last).
CHILD_TABLES = (
    "game_traditional", "game_advanced", "game_inactives", "game_officials",
    "game_team_stats", "features", "odds_snapshots", "results", "predictions",
)


def _copy_db(dst: Path) -> None:
    shutil.copyfile(paths.DB, dst)


def _delete_after(con: sqlite3.Connection, cut: str) -> int:
    """Physically delete every game past ``cut`` and its dependent rows."""
    future = [r[0] for r in con.execute(
        "SELECT game_id FROM games WHERE game_date > ?", (cut,))]
    if not future:
        return 0
    con.execute("CREATE TEMP TABLE _future(game_id TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO _future VALUES (?)", [(g,) for g in future])
    for table in CHILD_TABLES:
        con.execute(f"DELETE FROM {table} WHERE game_id IN (SELECT game_id FROM _future)")
    con.execute("DELETE FROM games WHERE game_id IN (SELECT game_id FROM _future)")
    con.commit()
    return len(future)


@pytest.fixture(scope="module")
def full_pv_build() -> dict:
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        return PV.build_player_value(con)
    finally:
        con.close()


@pytest.mark.parametrize("cut", CUTS)
def test_future_deletion_reproduces_past_rows(tmp_path: Path, full_pv_build: dict, cut: str) -> None:
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    scratch = tmp_path / "nba_cut_pv.sqlite"
    _copy_db(scratch)

    con = sqlite3.connect(scratch, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        deleted = _delete_after(con, cut)
        assert deleted > 1000, f"cut {cut} deleted only {deleted} future games (not a real test)"
        trunc = PV.build_player_value(con)
    finally:
        con.close()

    assert len(trunc) > 1000, f"cut {cut} left only {len(trunc)} past rows to compare"
    mismatches = [
        gid for gid in trunc
        if json.dumps(full_pv_build[gid], sort_keys=True) != json.dumps(trunc[gid], sort_keys=True)
    ]
    assert not mismatches, (
        f"{len(mismatches)} player-value rows for games <= {cut} changed when future games "
        f"were physically deleted (e.g. {mismatches[:3]}) - state is being pre-loaded from "
        "the future (GAP F)."
    )
