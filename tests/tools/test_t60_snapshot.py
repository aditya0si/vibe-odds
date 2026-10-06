"""Multi-kind ESPN odds capture discipline (plan Task 5, ESPN edition).

See tools/t60_snapshot.py. Fixtures mimic the live ESPN core-API odds payload
(probed 2026-10-06: Draft Kings items carry moneyLine + .open/.close objects).
"""
from __future__ import annotations

import sqlite3

import pytest

from sports.nba.live_log import LatePrediction, _ts
from tools import t60_snapshot as T

SCHEMA = """
CREATE TABLE odds_snapshots(
  game_id TEXT, source TEXT, book TEXT, market TEXT, side TEXT,
  price_decimal REAL, price_raw TEXT, line REAL,
  captured_at TEXT, snapshot_kind TEXT, asof_ts TEXT);
CREATE TABLE games(
  game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT, game_date TEXT,
  tipoff_ts TEXT, home_team_id INTEGER, away_team_id INTEGER,
  home_score INTEGER, away_score INTEGER);
"""

TIP = "2026-10-27T23:30:00Z"
GAME = {"game_id": "g_det_bos", "tipoff_ts": TIP,
        "home_team": "Boston Celtics", "away_team": "Detroit Pistons"}
EID = "401909088"

ODDS = {"items": [
    {"provider": {"name": "Draft Kings"},
     "homeTeamOdds": {"moneyLine": -125.0, "open": {"moneyLine": -110.0},
                      "close": {"moneyLine": -135.0}},
     "awayTeamOdds": {"moneyLine": 105.0, "open": {"moneyLine": -105.0},
                      "close": {"moneyLine": 115.0}}},
    {"provider": {"name": "ESPN BET"},
     "homeTeamOdds": {"moneyLine": 0.0, "open": {"moneyLine": 0.0}, "close": None},
     "awayTeamOdds": {"moneyLine": 0.0, "open": {"moneyLine": 0.0}, "close": None}},
    {"provider": {"name": "ESPN Bet - Live Odds"},
     "homeTeamOdds": {"moneyLine": -400.0, "open": {"moneyLine": 0.0}, "close": None},
     "awayTeamOdds": {"moneyLine": 320.0, "open": {"moneyLine": 0.0}, "close": None}},
]}


def _db(with_game: bool = False, completed: bool = False) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    if with_game:
        con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?)",
                    (GAME["game_id"], "2026-27", "regular", "2026-10-27", TIP, 1, 2,
                     110 if completed else None, 100 if completed else None))
    con.commit()
    return con


def test_late_t60_row_is_rejected():
    """The named test (plan): a t60 capture at/after tip-off is not evidence."""
    with pytest.raises(LatePrediction):
        T.parse_kinds(EID, ODDS, captured_at=TIP, tipoff_ts=TIP)          # at tip
    with pytest.raises(LatePrediction):
        T.parse_kinds(EID, ODDS, captured_at="2026-10-28T00:15:00Z", tipoff_ts=TIP)
    con = _db()
    assert con.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0] == 0


def test_parse_kinds_open_t60_close_and_exclusions():
    at = "2026-10-27T22:30:00Z"     # T-60
    rows = T.parse_kinds(EID, ODDS, captured_at=at, tipoff_ts=TIP)
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["snapshot_kind"], []).append(r)
    # Draft Kings: 2 sides x 3 kinds. ESPN BET (moneyLine=0) and the live feed are out.
    assert sorted(by_kind) == ["close", "open", "t60"]
    assert {r["book"] for r in rows} == {"Draft Kings"}
    t60 = {(r["side"]): r for r in by_kind["t60"]}
    assert t60["home"]["price_decimal"] == pytest.approx(1.80)     # -125
    assert t60["away"]["price_decimal"] == pytest.approx(2.05)     # +105
    assert all(_ts(r["captured_at"]) == _ts(at) and _ts(r["asof_ts"]) == _ts(at) for r in rows
               if r["snapshot_kind"] == "t60")
    # provider records carry captured_at=None (historical-ingest convention)
    assert all(r["captured_at"] is None for r in by_kind["open"] + by_kind["close"])


def test_window_bounds():
    now = "2026-10-27T22:30:00Z"
    assert T.in_window("2026-10-27T23:30:00Z", now)              # T-60
    assert T.in_window("2026-10-27T23:15:00Z", now)              # T-45: edge in
    assert T.in_window("2026-10-27T23:45:00Z", now)              # T-75: edge in
    assert not T.in_window("2026-10-27T23:14:00Z", now)          # T-44: out
    assert not T.in_window("2026-10-27T23:46:00Z", now)          # T-76: out


def test_run_captures_once_then_skips(monkeypatch):
    con = _db()
    fetches = {"n": 0}
    monkeypatch.setattr(T, "load_event_map", lambda c, s, refresh=False: {GAME["game_id"]: {"eid": EID}})

    def fake_fetch(eid):
        fetches["n"] += 1
        return ODDS
    monkeypatch.setattr(T, "fetch_event_odds", fake_fetch)

    now = "2026-10-27T22:30:00Z"
    out = T.run(con, [GAME], now, "2026-27")
    assert out["fetched"] == 1
    assert out["stored_rows"] == 4            # Draft Kings: 2 sides x (t60 + open)
    kinds = [r[0] for r in con.execute("SELECT DISTINCT snapshot_kind FROM odds_snapshots")]
    assert sorted(kinds) == ["open", "t60"]
    out2 = T.run(con, [GAME], now, "2026-27")     # second tick: no fetch, no write
    assert out2["fetched"] == 0 and out2["stored_rows"] == 0
    assert out2["skipped"][0]["reason"] == "t60 already captured"
    assert fetches["n"] == 1


def test_run_dry_run_fetches_nothing(monkeypatch):
    con = _db()
    monkeypatch.setattr(T, "load_event_map", lambda c, s, refresh=False: {GAME["game_id"]: {"eid": EID}})
    monkeypatch.setattr(T, "fetch_event_odds", lambda eid: pytest.fail("dry-run fetched!"))
    out = T.run(con, [GAME], "2026-10-27T22:30:00Z", "2026-27", dry_run=True)
    assert out["stored_rows"] == 0 and out["fetched"] == 0


def test_run_skips_unmapped_game(monkeypatch):
    con = _db()
    monkeypatch.setattr(T, "load_event_map", lambda c, s, refresh=False: {})
    out = T.run(con, [GAME], "2026-10-27T22:30:00Z", "2026-27")
    assert out["stored_rows"] == 0 and out["skipped"][0]["reason"] == "no ESPN event mapped"


def test_postgame_harvests_close_once(monkeypatch):
    con = _db(with_game=True, completed=True)
    fetches = {"n": 0}

    def fake_fetch(eid):
        fetches["n"] += 1
        return ODDS
    monkeypatch.setattr(T, "load_event_map", lambda c, s, refresh=False: {GAME["game_id"]: {"eid": EID}})
    monkeypatch.setattr(T, "fetch_event_odds", fake_fetch)
    out = T.postgame(con, "2026-27", date="2026-10-28T04:00:00Z")
    assert out["stored_rows"] == 4            # close (2 sides) + open (2 sides)
    kinds = sorted(r[0] for r in con.execute("SELECT DISTINCT snapshot_kind FROM odds_snapshots"))
    assert kinds == ["close", "open"]
    out2 = T.postgame(con, "2026-27", date="2026-10-28T04:00:00Z")
    assert out2["stored_rows"] == 0 and fetches["n"] == 1
    assert out2["skipped"][0]["reason"] == "close already captured"


def test_postgame_never_writes_t60(monkeypatch):
    """A t60 row must be OUR pre-tip capture - a post-game fetch cannot mint one."""
    con = _db(with_game=True, completed=True)
    monkeypatch.setattr(T, "load_event_map", lambda c, s, refresh=False: {GAME["game_id"]: {"eid": EID}})
    monkeypatch.setattr(T, "fetch_event_odds", lambda eid: ODDS)
    T.postgame(con, "2026-27", date="2026-10-28T04:00:00Z")
    assert con.execute("SELECT COUNT(*) FROM odds_snapshots WHERE snapshot_kind='t60'").fetchone()[0] == 0
