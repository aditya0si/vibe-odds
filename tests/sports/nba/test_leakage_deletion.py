"""GAP F regression: the as-of guarantee is proved by DELETION, not by truncation.

``test_asof.py`` compares ``build(upto=cut)`` against the full build. Both runs
read the same database, so any future state that the builder pre-loads ahead of
its chronological loop leaks into BOTH and the comparison stays green - that was
GAP F (mutating ``build_availability`` to pre-load ``player_hist`` from every
box-score row passed all 28 existing tests).

This test removes the future instead of hiding it: it copies the source DB into a
scratch file, deletes every game/line/box/inactive/official row dated after a cut,
and rebuilds. The compiler then loads only the past, so any pre-loaded-future bug
produces different rows for games <= the cut and is caught. Two cuts in different
eras (one before 2020-21, one after) exercise different roster/player regimes.

GAP D's fit-window guard is separate: tests/sports/nba/test_fit_window.py.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from sports.nba.db import paths
from sports.nba.features import build as B

# One cut in 2012-13, one in 2020-21. Literal dates, chosen at ~30% and ~72% of
# games, so the "future" being deleted is large (thousands of games) in both eras.
CUTS = ("2013-01-02", "2021-01-11")

# Tables that carry a game's as-of inputs, in FK-safe deletion order. games itself
# goes last; children reference it.
CHILD_TABLES = (
    "game_traditional", "game_advanced", "game_inactives", "game_officials",
    "game_team_stats", "features", "odds_snapshots", "results", "predictions",
)


def _copy_db(dst: Path) -> None:
    """Copy the source DB (main file only; features has no WAL sidecar) to dst."""
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
def full_build():
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        rows, _ = B.build_rows(con, verbose=False, version="v2")
    finally:
        con.close()
    return rows


@pytest.mark.parametrize("cut", CUTS)
def test_future_deletion_reproduces_past_rows(tmp_path: Path, full_build: dict, cut: str) -> None:
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    scratch = tmp_path / "nba_cut.sqlite"
    _copy_db(scratch)

    con = sqlite3.connect(scratch, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        deleted = _delete_after(con, cut)
        assert deleted > 1000, f"cut {cut} deleted only {deleted} future games (not a real test)"
        trunc, _ = B.build_rows(con, verbose=False, version="v2")
    finally:
        con.close()

    overlap = [gid for gid, row in trunc.items() if row["game_date"] <= cut]
    assert len(overlap) > 1000, f"cut {cut} left only {len(overlap)} past rows to compare"
    mismatches = [
        gid for gid in overlap
        if json.dumps(full_build[gid], sort_keys=True) != json.dumps(trunc[gid], sort_keys=True)
    ]
    assert not mismatches, (
        f"{len(mismatches)} rows for games <= {cut} changed when future games were "
        f"physically deleted (e.g. {mismatches[:3]}) - state is being pre-loaded from "
        "the future (GAP F)."
    )


@pytest.mark.parametrize("cut", CUTS)
def test_future_deletion_reproduces_availability_pass(tmp_path: Path, cut: str) -> None:
    """Same proof at the availability-pass level, where player ratings live."""
    from sports.nba.features import availability as AV

    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    full_con = sqlite3.connect(paths.DB, timeout=120)
    full_con.row_factory = sqlite3.Row
    try:
        full = AV.build_availability(full_con)
    finally:
        full_con.close()

    scratch = tmp_path / "nba_cut_avail.sqlite"
    _copy_db(scratch)
    con = sqlite3.connect(scratch, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        _delete_after(con, cut)
        trunc = AV.build_availability(con)
    finally:
        con.close()

    overlap = [gid for gid, f in trunc.items()
               if any((full.get(gid) or {}).values()) or all(v is None for v in f.values())]
    assert len(overlap) > 1000
    mismatches = [
        gid for gid in trunc
        if json.dumps(full[gid], sort_keys=True) != json.dumps(trunc[gid], sort_keys=True)
    ]
    assert not mismatches, (
        f"{len(mismatches)} availability rows for games <= {cut} changed when future "
        f"games were deleted (e.g. {mismatches[:3]}) - player_hist is leaking (GAP F)."
    )
