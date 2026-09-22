"""Phase-2 C3: schedule spots (plan Task 3). See sports/nba/features/spots.py."""
from __future__ import annotations

import sqlite3

from sports.nba.features.spots import SPOTS_KEYS, build_spots, haversine_miles

SCHEMA = """
CREATE TABLE games (game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT,
                    game_date TEXT, home_team_id INTEGER, away_team_id INTEGER,
                    home_score INTEGER, away_score INTEGER, tipoff_ts TEXT, is_neutral INTEGER);
CREATE TABLE game_team_stats (game_id TEXT, team_id INTEGER, pts REAL, fga REAL, fta REAL, oreb REAL, tov REAL);
"""

# two synthetic arenas ~1,000 miles apart, and one ~2,450 miles away
COORDS = {1: (34.05, -118.24), 2: (41.88, -87.63), 3: (40.75, -74.00), 99: None}


def _db(games) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for g in games:
        con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?)", g)
    return con


def _g(gid, date, home, away, hs=100, as_=95, neutral=0):
    return (gid, "2019-20", "regular", date, home, away, hs, as_, date + "T00:00:00Z", neutral)


def test_haversine_sanity():
    """~2,451 mi LA<->NY; a typo in the formula shows up here first."""
    d = haversine_miles(COORDS[1], COORDS[3])
    assert abs(d - 2451) < 30, d


def test_four_in_5_and_three_in_4():
    # team 1 plays every day d1..d5 (all home vs rotating away)
    games = [_g(f"g{i:02d}", f"2019-12-{i+1:02d}", 1, 2) for i in range(5)]
    con = _db(games)
    f = build_spots(con, coords=COORDS)
    assert f["g00"]["four_in_5_home"] == 0
    assert f["g04"]["four_in_5_home"] == 1      # 5th game in 5 days
    assert f["g03"]["four_in_5_home"] == 0      # only 4 in 4
    # away team 2 also plays daily here: its d5 is also a 5-in-5
    assert f["g04"]["four_in_5_away"] == 1

    # three_in_4_away: away team 2 plays only d2, d3, d5
    games2 = [_g("a00", "2019-12-01", 1, 3),
              _g("a01", "2019-12-02", 1, 2),
              _g("a02", "2019-12-03", 1, 2),
              _g("a03", "2019-12-04", 1, 3),
              _g("a04", "2019-12-05", 1, 2)]
    f2 = build_spots(_db(games2), coords=COORDS)
    assert f2["a04"]["three_in_4_away"] == 1    # 3rd in 4 days (d2, d3 prior)
    assert f2["a03"]["three_in_4_away"] == 0    # team 3's 2nd in 4 - not 3
    assert f2["a04"]["four_in_5_away"] == 0


def test_travel_rolls_in_five_day_window():
    # g0: team 1 home (venue A). g1: team 2 hosts team 1 (venue B) next day.
    # g2: team 1 home again (A) next day -> A->B + B->A accumulated.
    games = [_g("t00", "2019-12-01", 1, 2),
             _g("t01", "2019-12-02", 2, 1),
             _g("t02", "2019-12-03", 1, 2),
             _g("t03", "2019-12-20", 1, 2)]   # 17 days later: window is empty
    f = build_spots(_db(games), coords=COORDS)
    leg = haversine_miles(COORDS[1], COORDS[2])
    assert f["t00"]["travel_5d_home"] == 0.0           # at home arena, no prior trip
    assert abs(f["t01"]["travel_5d_home"] - leg) < 0.2  # team 2 comes home A->B... both sides take the leg
    assert abs(f["t01"]["travel_5d_away"] - leg) < 0.2  # team 1 travels A->B to visit
    # at t02 both teams are back at A: A->B leg inside the window + B->A arrival
    assert abs(f["t02"]["travel_5d_home"] - 2 * leg) < 0.3
    assert abs(f["t02"]["travel_5d_away"] - 2 * leg) < 0.3
    # t03 (17 days later): prior legs aged out; team 1 at its own arena
    assert f["t03"]["travel_5d_home"] == 0.0


def test_homestand_and_roadtrip():
    games = [_g(f"h{i:02d}", f"2019-12-{i+1:02d}", 1, 2) for i in range(3)]
    f = build_spots(_db(games), coords=COORDS)
    assert [f[f"h{i:02d}"]["homestand_home"] for i in range(3)] == [1, 2, 3]
    assert [f[f"h{i:02d}"]["roadtrip_away"] for i in range(3)] == [1, 2, 3]
    # team 2 finally goes home
    games2 = [_g("r00", "2019-12-01", 1, 2), _g("r01", "2019-12-02", 2, 1)]
    f2 = build_spots(_db(games2), coords=COORDS)
    assert f2["r01"]["roadtrip_away"] == 1       # team 1 on the road at team 2
    assert f2["r01"]["homestand_home"] == 1      # team 2 back home: run resets to 1


def test_unknown_team_travel_is_none_never_zero():
    games = [_g("u00", "2019-12-01", 99, 2), _g("u01", "2019-12-02", 2, 99)]
    f = build_spots(_db(games), coords={k: v for k, v in COORDS.items() if v})
    assert f["u00"]["travel_5d_home"] is None    # venue unknown -> None, never 0.0
    assert f["u01"]["travel_5d_away"] is None


def test_alt_venue_flag():
    games = [_g("v00", "2019-12-01", 1610612743, 2),    # at Denver
             _g("v01", "2019-12-02", 1610612743, 2, neutral=1)]  # neutral "home" at Denver
    f = build_spots(_db(games), coords={**COORDS, 1610612743: (39.75, -105.01)})
    assert f["v00"]["alt_venue"] == 1
    assert f["v01"]["alt_venue"] == 0   # neutral games are not played in that air


def test_truncated_rebuild_matches_full_prefix():
    games = [_g(f"s{i:02d}", f"2019-12-{i+1:02d}", 1 if i % 2 == 0 else 2,
                2 if i % 2 == 0 else 1) for i in range(6)]
    con = _db(games)
    full = build_spots(con, coords=COORDS)
    cut = build_spots(con, coords=COORDS, upto="2019-12-04")
    for gid in ("s00", "s01", "s02", "s03"):
        assert cut[gid] == full[gid], (gid, cut[gid], full[gid])


def test_spots_keys_stable():
    assert SPOTS_KEYS == ("travel_5d_home", "travel_5d_away", "alt_venue",
                          "four_in_5_home", "four_in_5_away", "three_in_4_away",
                          "homestand_home", "roadtrip_away")
