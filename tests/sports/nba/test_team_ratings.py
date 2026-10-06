"""Unit tests for the SRS-style team ratings (features v6 / arm A11). No DB writes."""
from __future__ import annotations

import sqlite3

import pytest

from sports.nba.db import paths
from sports.nba.features import team_ratings as TR

GAMES = [
    ("g1", "S1", "2020-01-01", 1, 2, 110, 100),
    ("g2", "S1", "2020-01-02", 2, 1, 95, 105),
    ("g3", "S1", "2020-01-03", 1, 3, 120, 90),
    ("g4", "S1", "2020-01-04", 3, 2, 101, 99),
    ("g5", "S2", "2021-01-01", 1, 2, 100, 100),
    ("g6", "S2", "2021-01-02", 3, 1, 80, 120),
]


def _con(games=GAMES):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE games(game_id TEXT PRIMARY KEY, season TEXT, game_date TEXT,
                  home_team_id INTEGER, away_team_id INTEGER, home_score INTEGER,
                  away_score INTEGER, season_type TEXT)""")
    con.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?, 'regular')", games)
    return con


def test_first_season_unrated_then_second_season_favours_the_stronger_team():
    out = TR.build_srs(_con())
    assert out["g1"]["srs_diff"] == 0.0 and out["g4"]["srs_diff"] == 0.0   # no refit yet
    assert out["g5"]["srs_home"] > out["g5"]["srs_away"]                   # team 1 > team 2
    assert out["g5"]["srs_diff"] > 5.0
    assert out["g6"]["srs_diff"] < 0.0                                     # team 3 < team 1


def test_diff_is_the_difference_of_the_sides():
    out = TR.build_srs(_con())
    for row in out.values():
        assert row["srs_diff"] == pytest.approx(row["srs_home"] - row["srs_away"], abs=2e-4)


def test_prefix_reproducibility_on_synthetic_games():
    """A cut before g6 must reproduce the full build's g1..g5 exactly."""
    con = _con()
    full = TR.build_srs(con)
    trunc = TR.build_srs(con, upto="2021-01-01")
    assert "g6" not in trunc
    for gid in ("g1", "g2", "g3", "g4", "g5"):
        assert full[gid] == trunc[gid]


def test_real_db_prefix_is_asof():
    if not paths.DB.exists():
        pytest.skip("NBA database not built")
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    try:
        games = list(con.execute("""SELECT game_date FROM games
                                    WHERE season_type='regular' AND home_score IS NOT NULL
                                    ORDER BY game_date, game_id"""))
        cut = games[int(len(games) * 0.6)][0]
        full = TR.build_srs(con)
        trunc = TR.build_srs(con, upto=cut)
    finally:
        con.close()
    assert len(trunc) > 1000
    mismatches = [gid for gid, row in trunc.items() if full[gid] != row]
    assert mismatches == []
