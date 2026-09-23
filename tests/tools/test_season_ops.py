"""Daily season ops: schedule emission, sealed-arm prediction logging, refresh.

Uses a temp SQLite DB shaped like the real one (games/teams/features) so no
production data is touched and no network is needed.
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
         ("g3", "2026-27", "regular", "2026-10-28", "2026-10-28T00:30:00Z", 1, 2, None, None)])
    con.commit()
    return con


def test_schedule_emits_full_names_and_today_only():
    con = make_con()
    out = SO.cmd_schedule(con, "2026-10-27", None)
    assert out["n_games"] == 2                                   # g3 is next day
    assert out["games"][0]["home_team"] == "Detroit Pistons"
    assert out["games"][0]["away_team"] == "Boston Celtics"
    assert out["games"][0]["tipoff_ts"] == "2026-10-27T19:00:00Z"


def test_schedule_writes_games_json(tmp_path):
    con = make_con()
    p = tmp_path / "games.json"
    SO.cmd_schedule(con, "2026-10-27", str(p))
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back[0]["game_id"] == "g1" and len(back) == 2


def _seed_features(con, rows):
    for gid, r in rows.items():
        con.execute("INSERT INTO features VALUES (?,?,?,?,?)",
                    (gid, "v3", "2026-10-27T00:00:00Z", "2026-10-27T00:00:00Z",
                     json.dumps(r)))
    con.commit()


def test_predict_logs_sealed_arm_before_tip(tmp_path, monkeypatch):
    con = make_con()
    rows = syn(40)
    arm = fit_claim_arm(rows)
    # features keyed by the schedule's game_ids
    _seed_features(con, {"g1": rows[0], "g2": rows[1]})
    monkeypatch.setattr(SO.live_log, "LOG_PATH", tmp_path / "log.jsonl")
    out = SO.cmd_predict(con, "2026-10-27", arm)
    assert out["n_logged"] == 2 and not out["skipped"]
    for e in out["logged"]:
        assert 0.0 < e["prob_home"] < 1.0
    # rows actually landed in the log, before tip
    events = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines() if l.strip()]
    assert len(events) == 2 and all(e["type"] == "predict" for e in events)
    assert all(e["source"] == "sealed_a7_2026_27" for e in events)


def test_predict_skips_games_without_features(tmp_path, monkeypatch):
    con = make_con()
    rows = syn(10)
    arm = fit_claim_arm(rows)
    _seed_features(con, {"g1": rows[0]})                 # g2 has no features
    monkeypatch.setattr(SO.live_log, "LOG_PATH", tmp_path / "log.jsonl")
    out = SO.cmd_predict(con, "2026-10-27", arm)
    assert out["n_logged"] == 1
    assert out["skipped"] == [{"game_id": "g2", "reason": "no v3 features yet"}]


def test_predict_dry_run_writes_nothing(tmp_path, monkeypatch):
    con = make_con()
    rows = syn(10)
    arm = fit_claim_arm(rows)
    _seed_features(con, {"g1": rows[0]})
    monkeypatch.setattr(SO.live_log, "LOG_PATH", tmp_path / "log.jsonl")
    out = SO.cmd_predict(con, "2026-10-27", arm, dry_run=True)
    assert out["n_logged"] == 1 and out["logged"][0]["dry_run"] is True
    assert not (tmp_path / "log.jsonl").exists()


def test_predict_refuses_tampered_arm(tmp_path, monkeypatch):
    con = make_con()
    rows = syn(10)
    arm = fit_claim_arm(rows)
    _seed_features(con, {"g1": rows[0]})
    monkeypatch.setattr(SO.live_log, "LOG_PATH", tmp_path / "log.jsonl")
    with pytest.raises(RuntimeError, match="digest mismatch"):
        SO.cmd_predict(con, "2026-10-27", dict(arm, n_fit=arm["n_fit"] + 1))
