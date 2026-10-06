"""Daily season ops: window-keyed schedule emission, sealed-arm verify, refresh.

Uses a temp SQLite DB shaped like the real one (games/teams) so no production
data is touched and no network is needed. The removed `predict` subcommand is
guarded by a test: live prediction logging from the sealed arm is impossible by
construction (10/17 features are post-game-only), see tools/season_ops.py.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tools import season_ops as SO
from tools.season_gate import fit_claim_arm
from tests.tools.test_season_gate import synthetic_rows as syn


def make_con() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE teams(team_id INTEGER PRIMARY KEY, full_name TEXT);
      CREATE TABLE games(game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT,
                         game_date TEXT, tipoff_ts TEXT, home_team_id INTEGER,
                         away_team_id INTEGER, home_score INTEGER, away_score INTEGER);
      CREATE TABLE features(game_id TEXT, feature_version TEXT, built_at TEXT,
                            asof_ts TEXT, payload TEXT,
                            PRIMARY KEY(game_id, feature_version));
    """)
    con.executemany("INSERT INTO teams VALUES (?,?)",
                    [(1, "Boston Celtics"), (2, "Detroit Pistons"),
                     (3, "New York Knicks"), (4, "Philadelphia 76ers")])
    con.executemany(
        "INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?)",
        [("g1", "2026-27", "regular", "2026-10-27", "2026-10-27T19:00:00Z", 2, 1, None, None),
         ("g2", "2026-27", "regular", "2026-10-27", "2026-10-27T23:00:00Z", 3, 4, None, None),
         ("g3", "2026-27", "regular", "2026-10-28", "2026-10-28T00:30:00Z", 1, 2, None, None),
         ("g4", "2026-27", "regular", "2026-10-28", None, 1, 3, None, None),
         ("g5", "2025-26", "regular", "2026-10-27", "2026-10-27T23:10:00Z", 4, 3, 100, 99)])
    con.commit()
    return con


def test_games_near_window_includes_midnight_crosser():
    """The UTC-date bug: g3 tips at 00:30Z the NEXT UTC day and must be included."""
    con = make_con()
    got = SO.games_near(con, "2026-10-27T22:30:00Z")
    ids = [g["game_id"] for g in got]
    assert ids == ["g2", "g3"]                   # g1 too early; g4 no tipoff; g5 other season
    assert got[0]["home_team"] == "New York Knicks"
    assert got[0]["away_team"] == "Philadelphia 76ers"


def test_games_near_respects_window_edges():
    con = make_con()
    assert [g["game_id"] for g in SO.games_near(con, "2026-10-27T21:45:00Z")] == ["g2", "g3"]
    # g2 (23:00) falls out once it is more than 1h in the past
    assert [g["game_id"] for g in SO.games_near(con, "2026-10-28T00:15:00Z")] == ["g3"]


def test_schedule_writes_games_json(tmp_path):
    con = make_con()
    p = tmp_path / "games.json"
    out = SO.cmd_schedule(con, "2026-10-27T22:30:00Z", str(p))
    assert out["n_games"] == 2 and out["written_to"] == str(p)
    back = json.loads(p.read_text(encoding="utf-8"))
    assert [g["game_id"] for g in back] == ["g2", "g3"]
    assert back[0]["tipoff_ts"] == "2026-10-27T23:00:00Z"


def test_verify_accepts_sealed_arm_and_rejects_tamper():
    arm = fit_claim_arm(syn(40))
    out = SO.cmd_verify(arm)
    assert out["verified"] is True and out["digest"] == arm["digest"]
    with pytest.raises(RuntimeError, match="digest mismatch"):
        SO.cmd_verify(dict(arm, n_fit=arm["n_fit"] + 1))


def test_predict_subcommand_is_gone():
    """Structural: A7's avail/impact features are post-game-only; no live log exists."""
    assert not hasattr(SO, "cmd_predict")
    with pytest.raises(SystemExit):
        SO.main(["predict", "--date", "2026-10-27"])
