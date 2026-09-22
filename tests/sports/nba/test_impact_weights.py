"""Phase-2 C1: player-IMPACT value weights for inactive players (A7 arm inputs).

A6 weights absences by a box production score (pts+1.5ast+reb-tov). That has two
blind spots this module fixes:

  1. plus_minus is team-context impact - what a rolling production total is a
     proxy for at best;
  2. SIGN. Missing a BAD player helps. A quality-share can never express that
     (shares are >= 0); an impact sum can (negative value out -> positive news).

As-of discipline identical to features/availability.py: state updates only after
a row is emitted, so build_impact(upto=...) reproduces the full build's prefix.
Unknown games (no published inactive/officials marker) score None everywhere.
"""
from __future__ import annotations

import sqlite3

from sports.nba.features.impact import IMPACT_KEYS, build_impact

SCHEMA = """
CREATE TABLE games (game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT,
                    game_date TEXT, home_team_id INTEGER, away_team_id INTEGER,
                    home_score INTEGER, away_score INTEGER, tipoff_ts TEXT, payload TEXT);
CREATE TABLE game_traditional (game_id TEXT, team_id INTEGER, player_id INTEGER,
                    started INTEGER, minutes REAL, pts REAL, reb REAL, ast REAL,
                    stl REAL, blk REAL, tov REAL, pf REAL, fgm REAL, fga REAL,
                    fg3m REAL, fg3a REAL, ftm REAL, fta REAL, plus_minus REAL, asof_ts TEXT);
CREATE TABLE game_inactives (game_id TEXT, team_id INTEGER, player_id INTEGER);
CREATE TABLE game_officials (game_id TEXT, person_id INTEGER);
"""


def _db(games, boxes, inactives=(), officials=()) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row  # production connections name their columns
    con.executescript(SCHEMA)
    for g in games:
        con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?)", g)
    for b in boxes:
        con.execute("INSERT INTO game_traditional VALUES ("
                    "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", b)
    for i in inactives:
        con.execute("INSERT INTO game_inactives VALUES (?,?,?)", i)
    for o in officials:
        gid, pid = o if isinstance(o, tuple) else (o, 1)
        con.execute("INSERT INTO game_officials VALUES (?,?)", (gid, pid))
    return con


def _box(gid, tid, pid, pm, minutes=20.0):
    return (gid, tid, pid, 0, minutes, 5, 2, 1, 0, 0, 1, 2, 2, 4, 0, 0, 1, 2, pm, "2020-01-01")


def _game(gid, date, hs=100, as_=95):
    return (gid, "2019-20", "regular", date, 1, 2, hs, as_, date + "T00:00:00Z", "{}")


def test_missing_good_player_hurts_missing_bad_player_helps():
    """The sign asymmetry: value summed over inactives may be negative."""
    games = [_game(f"g{i:02d}", f"2019-12-{i+1:02d}") for i in range(3)]
    boxes = []
    # player 10 = good (+8 every game); player 20 = bad (-5 every game). Both on team 1.
    for i in range(3):
        boxes.append(_box(f"g{i:02d}", 1, 10, 8.0))
        boxes.append(_box(f"g{i:02d}", 1, 20, -5.0))
        boxes.append(_box(f"g{i:02d}", 2, 30, 0.0))
    # game 3 (last): both players OUT for team 1.
    inactives = [("g02", 1, 10), ("g02", 1, 20)]
    con = _db(games, boxes, inactives=inactives, officials=[("g02", 99),
              ("g01", 99), ("g00", 99)])
    feats = build_impact(con)
    # good player out -> positive; the pair nets to 8-5-ish after shrinkage
    assert feats["g02"]["impact_out_home"] > 0
    # nobody is out for team 2 (authoritative game) and the diff sign convention
    # matches avail_diff: away_out - home_out (positive favours home)
    assert feats["g02"]["impact_out_away"] == 0.0
    assert feats["g02"]["impact_out_diff"] == round(-feats["g02"]["impact_out_home"], 4)
    # now only the BAD player is out for game 2 (after 1 game of history): helps team 1
    inactives2 = [("g01", 1, 20)]
    con2 = _db(games, boxes, inactives=inactives2, officials=[("g00", 99), ("g01", 99), ("g02", 99)])
    f2 = build_impact(con2)
    assert f2["g01"]["impact_out_home"] < 0


def test_unknown_game_scores_none():
    games = [_game("g00", "2019-12-01")]
    boxes = [_box("g00", 1, 10, 8.0)]
    con = _db(games, boxes)  # no inactives rows AND no officials rows
    feats = build_impact(con)
    assert all(feats["g00"][k] is None for k in IMPACT_KEYS)


def test_truncated_rebuild_matches_full_prefix():
    games = [_game(f"g{i:02d}", f"2019-12-{i+1:02d}") for i in range(4)]
    boxes = [_box(f"g{i:02d}", 1, 10, float(i)) for i in range(4)]
    boxes += [_box(f"g{i:02d}", 2, 30, -2.0) for i in range(4)]
    inactives = [("g02", 1, 10), ("g03", 2, 30)]
    officials = [(f"g{i:02d}", 99) for i in range(4)]
    con = _db(games, boxes, inactives=inactives, officials=officials)
    full = build_impact(con)
    cut = build_impact(con, upto="2019-12-03")  # games g00..g02 inclusive
    for gid in ("g00", "g01", "g02"):
        assert cut[gid] == full[gid], (gid, cut[gid], full[gid])
