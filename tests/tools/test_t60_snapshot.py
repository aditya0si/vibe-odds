"""T-60 odds capture discipline (plan Task 5). See tools/t60_snapshot.py."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sports.nba.live_log import LatePrediction, _ts
from tools.t60_snapshot import (MOCK_NBA_EVENTS, h2h_rows, run, store_t60, upcoming_t60)

SCHEMA = """CREATE TABLE odds_snapshots(
  game_id TEXT, source TEXT, book TEXT, market TEXT, side TEXT,
  price_decimal REAL, price_raw TEXT, line REAL,
  captured_at TEXT, snapshot_kind TEXT, asof_ts TEXT)"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(SCHEMA)
    con.commit()
    return con


TIP = "2026-10-27T23:30:00Z"
GAME = {"game_id": "g2026_1027_lal_gsw", "tipoff_ts": TIP,
        "home_team": "Los Angeles Lakers", "away_team": "Golden State Warriors"}


def test_late_t60_row_is_rejected():
    """The named test (plan): a t60 row with asof_ts >= tipoff is not evidence."""
    con = _db()
    rows = h2h_rows(MOCK_NBA_EVENTS[0], captured_at="2026-10-27T23:00:00Z")  # T-30 parse ok
    with pytest.raises(LatePrediction):
        store_t60(con, GAME["game_id"], TIP, rows, captured_at="2026-10-27T23:30:00Z")  # at tip
    with pytest.raises(LatePrediction):
        store_t60(con, GAME["game_id"], TIP, rows, captured_at="2026-10-27T23:45:00Z")  # after
    # the parse step enforces the same rule
    with pytest.raises(LatePrediction):
        h2h_rows(MOCK_NBA_EVENTS[0], captured_at="2026-10-28T00:00:00Z")
    assert con.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0] == 0


def test_pre_tip_rows_land_with_asof_ts():
    con = _db()
    at = "2026-10-27T22:30:00Z"     # T-60
    rows = h2h_rows(MOCK_NBA_EVENTS[0], captured_at=at)
    n = store_t60(con, GAME["game_id"], TIP, rows, captured_at=at)
    assert n == 4                    # 2 books x 2 sides
    got = list(con.execute("SELECT game_id, book, side, snapshot_kind, asof_ts, price_decimal "
                           "FROM odds_snapshots ORDER BY book, side"))
    assert all(r[3] == "t60" and _ts(r[4]) == _ts(at) for r in got)
    assert {(r[1], r[2]) for r in got} == {("draftkings", "home"), ("draftkings", "away"),
                                           ("pinnacle", "home"), ("pinnacle", "away")}
    assert dict((r[1] + r[2], r[5]) for r in got)["pinnaclehome"] == 1.95


def test_window_selects_only_t60_games():
    now = "2026-10-27T22:30:00Z"
    ev = dict(MOCK_NBA_EVENTS[0])
    near = dict(ev, id="near", commence_time="2026-10-27T23:30:00Z")      # T-60: in
    early = dict(ev, id="early", commence_time="2026-10-28T01:00:00Z")    # T-150: out
    late = dict(ev, id="late", commence_time="2026-10-27T23:05:00Z")      # T-35: out
    got = upcoming_t60([early, near, late], now)
    assert [e["id"] for e in got] == ["near"]


def test_run_captures_in_window_and_skips_unmatched():
    now = "2026-10-27T22:30:00Z"
    con = _db()
    stranger = dict(MOCK_NBA_EVENTS[0], id="stranger",
                    home_team="Chicago Bulls", away_team="Miami Heat",
                    commence_time="2026-10-27T23:35:00Z")     # T-65: in window, no match
    out = run(con, [GAME], now, events=[MOCK_NBA_EVENTS[0], stranger], dry_run=False)
    assert out["stored_rows"] == 4
    assert any(s["reason"].startswith("no scheduled game") for s in out["skipped"])
    dry = run(_db(), [GAME], now, events=[MOCK_NBA_EVENTS[0]], dry_run=True)
    assert dry["stored_rows"] == 0 and dry["quota"] is None


def test_mock_events_parse_to_bounded_probabilities():
    rows = h2h_rows(MOCK_NBA_EVENTS[0], captured_at="2026-10-27T22:30:00Z")
    dec = [r["price_decimal"] for r in rows]
    assert all(d >= 1.0 for d in dec)          # decimal odds sanity
