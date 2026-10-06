"""Upcoming-season schedule ingest (scheduleleaguev2): the T-60 cron's input.

Pure parsing/upsert tests against a temp DB - no network. The 2026-27 schedule
shape was probed live (1,200 real games + 6 TBD Cup placeholders, all tipoffs UTC).
"""
from __future__ import annotations

import sqlite3

import pytest

from sports.nba.ingest import schedule as SCH


def make_con() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE seasons(season TEXT PRIMARY KEY, start_year INT, end_year INT,
                           n_games_known INT, asof_ts TEXT);
      CREATE TABLE teams(team_id INTEGER PRIMARY KEY, abbr TEXT, full_name TEXT, asof_ts TEXT);
      CREATE TABLE games(game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT,
                         game_date TEXT, tipoff_ts TEXT, home_team_id INT, away_team_id INT,
                         home_score INT, away_score INT, ot_count INT, arena TEXT,
                         attendance INT, is_neutral INT, source TEXT,
                         source_updated_at TEXT, asof_ts TEXT);
    """)
    con.executemany("INSERT INTO teams(team_id, full_name) VALUES (?,?)",
                    [(1, "Boston Celtics"), (2, "Detroit Pistons")])
    con.commit()
    return con


def _game(gid, hid=2, aid=1, label="", tip="2026-10-20T19:00:00Z",
          date="2026-10-20T04:00:00Z", neutral=False):
    return {"gameId": gid, "gameLabel": label, "gameDateTimeUTC": tip,
            "gameDateUTC": date, "isNeutral": neutral,
            "homeTeam": {"teamId": hid, "teamCity": "Detroit", "teamName": "Pistons"},
            "awayTeam": {"teamId": aid, "teamCity": "Boston", "teamName": "Celtics"}}


def test_to_game_rows_keeps_regular_with_teams_skips_preseason_and_tbd():
    games = [
        _game("0022600001"),                                  # regular: keep
        _game("0012600001", tip="2026-10-04T00:00:00Z"),      # preseason: drop
        _game("0062600001"),                                  # cup final: drop
        {"gameId": "0022601201", "gameLabel": "Emirates NBA Cup",
         "gameDateTimeUTC": "2026-12-04T05:00:00Z", "gameDateUTC": "2026-12-04T05:00:00Z",
         "homeTeam": {"teamId": None}, "awayTeam": {"teamId": None}},   # TBD: skip
    ]
    rows, skipped = SCH.to_game_rows(games, "2026-27")
    assert [r[0] for r in rows] == ["0022600001"]
    assert len(skipped) == 1 and skipped[0]["game_id"] == "0022601201"


def test_row_shape_and_neutral_flag():
    rows, _ = SCH.to_game_rows([_game("0022600002", neutral=True)], "2026-27")
    (gid, season, stype, gdate, tip, hid, aid, hs, as_, neutral, source, ts) = rows[0]
    assert gid == "0022600002" and season == "2026-27" and stype == "regular"
    assert gdate == "2026-10-20" and tip == "2026-10-20T19:00:00Z"
    assert (hid, aid) == (2, 1) and hs is None and as_ is None     # unsettled
    assert neutral == 1 and source == "scheduleleaguev2"


def test_iso_utc_normalizes_and_passes_none():
    assert SCH._iso_utc("2026-10-20T19:00:00Z") == "2026-10-20T19:00:00Z"
    assert SCH._iso_utc("2026-10-20T19:00:00") == "2026-10-20T19:00:00Z"
    assert SCH._iso_utc(None) is None


def test_upsert_seeds_season_fk_and_is_idempotent():
    con = make_con()
    rows, _ = SCH.to_game_rows([_game("0022600001"), _game("0022600002")], "2026-27")
    SCH.ensure_season(con, "2026-27", len(rows))
    SCH.upsert_games(con, rows)
    SCH.upsert_games(con, rows)                                  # re-run: no dup
    n = con.execute("SELECT COUNT(*) FROM games WHERE season='2026-27'").fetchone()[0]
    assert n == 2
    fk = con.execute("SELECT n_games_known FROM seasons WHERE season='2026-27'").fetchone()[0]
    assert fk == 2
    # scores stay NULL (unsettled), tipoff populated
    r = con.execute("SELECT tipoff_ts, home_score FROM games WHERE game_id='0022600001'").fetchone()
    assert r["tipoff_ts"] == "2026-10-20T19:00:00Z" and r["home_score"] is None


def test_upsert_updates_tipoff_on_conflict():
    con = make_con()
    rows, _ = SCH.to_game_rows([_game("0022600001")], "2026-27")
    SCH.ensure_season(con, "2026-27", 1)
    SCH.upsert_games(con, rows)
    # league moves the game: re-ingest with a new tipoff
    moved, _ = SCH.to_game_rows([_game("0022600001", tip="2026-10-20T20:30:00Z")], "2026-27")
    SCH.upsert_games(con, moved)
    tip = con.execute("SELECT tipoff_ts FROM games WHERE game_id='0022600001'").fetchone()[0]
    assert tip == "2026-10-20T20:30:00Z"
