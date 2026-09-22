"""Availability v2 tests (Step 2): completeness trap, v2 as-of leakage, sign sanity.

All tests run against throwaway in-memory sandbox DBs - never the real
``sports/nba/data/nba.sqlite``, which a box-score ingest may be writing to
concurrently. Only the four tables the two passes read are created
(games, game_inactives, game_traditional, game_team_stats).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from sports.nba.features import availability as AV
from sports.nba.features import build as B

HOME, AWAY = 1, 2
STAR = 29  # away team's star: 40 pts a night, firmly top-3 by any rolling mean


def make_con() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE games (
            game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT,
            game_date TEXT, home_team_id INTEGER, away_team_id INTEGER,
            home_score INTEGER, away_score INTEGER, is_neutral INTEGER DEFAULT 0);
        CREATE TABLE game_inactives (
            game_id TEXT, team_id INTEGER, player_id INTEGER,
            reason TEXT, asof_ts TEXT);
        CREATE TABLE game_officials (
            game_id TEXT, official_id INTEGER, first_name TEXT,
            last_name TEXT, jersey_num TEXT, asof_ts TEXT);
        CREATE TABLE game_traditional (
            game_id TEXT, team_id INTEGER, player_id INTEGER,
            minutes REAL, pts INTEGER, reb INTEGER, ast INTEGER, tov INTEGER);
        CREATE TABLE game_team_stats (
            game_id TEXT, team_id INTEGER, pts INTEGER,
            fga INTEGER, fta INTEGER, oreb INTEGER, tov INTEGER);
    """)
    return con


def add_game(con, gid, date, home=HOME, away=AWAY, hs=110, aws=105):
    con.execute(
        "INSERT INTO games(game_id, season, season_type, game_date, home_team_id,"
        " away_team_id, home_score, away_score, is_neutral)"
        " VALUES (?,?,?,?,?,?,?,?,0)",
        (gid, "2021-22", "regular", date, home, away, hs, aws))


def add_box(con, gid, tid, pid, pts, ast=0, reb=0, tov=0, minutes=30):
    con.execute(
        "INSERT INTO game_traditional(game_id, team_id, player_id, minutes,"
        " pts, reb, ast, tov) VALUES (?,?,?,?,?,?,?,?)",
        (gid, tid, pid, minutes, pts, reb, ast, tov))


def add_inactive(con, gid, tid, pid):
    con.execute(
        "INSERT INTO game_inactives(game_id, team_id, player_id, reason, asof_ts)"
        " VALUES (?,?,?,'knee','2021-10-01T00:00:00+00:00')",
        (gid, tid, pid))


def add_officials(con, gid, official_id=9001):
    """An officials row proves the pre-game summary parsed (same payload as the
    inactive list), even when that game's inactive list is empty."""
    con.execute(
        "INSERT INTO game_officials(game_id, official_id, first_name, last_name,"
        " jersey_num, asof_ts) VALUES (?,?,?,?,?,?)",
        (gid, official_id, "Test", "Ref", "1", "2021-10-01T00:00:00+00:00"))


def history(con):
    """Two prior games so every regular has a rated as-of roster history."""
    add_game(con, "G_hist1", "2021-10-19")
    add_game(con, "G_hist2", "2021-10-21", home=AWAY, away=HOME)
    for gid in ("G_hist1", "G_hist2"):
        add_box(con, gid, HOME, 11, 30, ast=4, reb=5)   # home's best
        add_box(con, gid, HOME, 12, 10, ast=2, reb=3)
        add_box(con, gid, HOME, 13, 8, ast=1, reb=2)
        add_box(con, gid, AWAY, 21, 20, ast=3, reb=4)
        add_box(con, gid, AWAY, 22, 12, ast=2, reb=3)
        add_box(con, gid, AWAY, STAR, 40, ast=5, reb=8)  # away's star
    con.commit()


@pytest.fixture()
def con() -> sqlite3.Connection:
    c = make_con()
    history(c)
    # G_both: published list naming players on BOTH teams -> numeric everywhere.
    add_game(c, "G_both", "2021-10-25")
    add_inactive(c, "G_both", AWAY, STAR)
    add_inactive(c, "G_both", HOME, 12)
    # G_home_ok: published list, only the away star is out -> home is healthier.
    add_game(c, "G_home_ok", "2021-10-27")
    add_inactive(c, "G_home_ok", AWAY, STAR)
    # G_away_ok: mirror image -> away is healthier (negative diff).
    add_game(c, "G_away_ok", "2021-10-28")
    add_inactive(c, "G_away_ok", HOME, 11)
    # G_dark: no summary was ever parsed (no inactive rows AND no officials) ->
    # a parse gap, so unknown: every avail_* key must be None, never 0.0.
    add_game(c, "G_dark", "2021-10-29")
    # G_empty: the summary parsed (officials present) but BOTH inactive lists are
    # legitimately empty -> authoritative, so avail_* is numeric (0.0), not None.
    add_game(c, "G_empty", "2021-10-30")
    add_officials(c, "G_empty")
    c.commit()
    return c


# ---- 1. completeness trap -------------------------------------------------

def test_no_inactive_rows_yields_none_never_zero(con) -> None:
    feats = AV.build_availability(con)
    dark = feats["G_dark"]
    assert set(dark) == set(AV.AVAIL_KEYS)
    for k, v in dark.items():
        assert v is None, f"{k} scored {v!r} for a game whose summary never parsed - must be None, never 0.0"


def test_unrated_roster_uses_replacement_level_prior(con) -> None:
    """A missing denominator must not yield None: every roster player with no prior
    games counts at the documented REPLACEMENT_LEVEL, so an authoritative game is
    always scored (never silently dropped as unknown)."""
    c = make_con()
    # One game only: neither team has any appearance history. The roster is defined
    # by the game's own box score at update time (after the row is emitted), so the
    # as-of roster is empty here - the empty-union branch defines the denominator.
    add_game(c, "G_open", "2021-10-19")
    add_officials(c, "G_open")            # summary parsed => authoritative
    add_inactive(c, "G_open", HOME, 11)   # one listed out, no prior rating
    c.commit()
    feats = AV.build_availability(c)["G_open"]
    assert all(v is not None for v in feats.values())
    assert feats["avail_missing_home"] == round(AV.REPLACEMENT_LEVEL / AV.REPLACEMENT_LEVEL, 4)
    assert feats["avail_missing_away"] == 0.0
    assert feats["avail_diff"] == round(0.0 - 1.0, 4)
    assert feats["avail_n_inactive_home"] == 1 and feats["avail_n_inactive_away"] == 0


def test_replacement_level_is_a_fixed_documented_constant() -> None:
    assert isinstance(AV.REPLACEMENT_LEVEL, float) and AV.REPLACEMENT_LEVEL > 0
    assert "REPLACEMENT_LEVEL" in AV.__doc__
    assert "REPLACEMENT" in AV.PUBLICATION_RULE or "replacement" in AV.PUBLICATION_RULE


def test_published_lists_yield_numeric_values(con) -> None:
    feats = AV.build_availability(con)
    both = feats["G_both"]
    assert both["avail_missing_home"] is not None and both["avail_missing_away"] is not None
    assert both["avail_diff"] is not None
    assert both["avail_n_inactive_home"] == 1 and both["avail_n_inactive_away"] == 1
    assert both["avail_top_out_away"] == 1  # the away star is a top-3 player
    one_sided = feats["G_home_ok"]
    # Game-level publication covers BOTH teams: the side with zero rows in a
    # published game is "nobody is out" (0.0), not unknown.
    assert one_sided["avail_missing_home"] == 0.0
    assert one_sided["avail_n_inactive_home"] == 0
    assert one_sided["avail_top_out_home"] == 0
    assert one_sided["avail_missing_away"] is not None and one_sided["avail_missing_away"] > 0


def test_authoritative_membership_is_game_level(con) -> None:
    # G_empty has no inactive rows but an officials row: the summary parsed, so its
    # (empty) lists are authoritative. G_dark has neither marker -> unknown.
    assert AV.games_with_authoritative_inactives(con) == {
        "G_both", "G_home_ok", "G_away_ok", "G_empty"}


def test_published_empty_list_is_numeric_zero_not_none(con) -> None:
    """The marker distinction: a parsed-but-empty list is "nobody out", not unknown."""
    feats = AV.build_availability(con)
    empty = feats["G_empty"]
    assert empty["avail_missing_home"] == 0.0 and empty["avail_missing_away"] == 0.0
    assert empty["avail_n_inactive_home"] == 0 and empty["avail_n_inactive_away"] == 0
    assert empty["avail_diff"] == 0.0
    assert all(v is not None for v in empty.values())


# ---- 2. leakage: truncated rebuild reproduces v2 rows ---------------------

def test_v2_pass_is_as_of(con) -> None:
    cut = "2021-10-27"
    full, _ = B.build_rows(con, verbose=False, version="v2")
    trunc, _ = B.build_rows(con, upto=cut, verbose=False, version="v2")
    overlap = [gid for gid, row in trunc.items() if row["game_date"] <= cut]
    assert len(overlap) >= 3
    mismatches = [gid for gid in overlap
                  if json.dumps(full[gid], sort_keys=True) != json.dumps(trunc[gid], sort_keys=True)]
    assert not mismatches, (
        f"{len(mismatches)} v2 rows changed when the future was removed "
        f"(e.g. {mismatches[:3]}) - that is leakage")
    # Same guarantee at the availability-pass level.
    full_a = AV.build_availability(con)
    trunc_a = AV.build_availability(con, upto=cut)
    bad_a = [gid for gid in trunc_a
             if json.dumps(full_a[gid], sort_keys=True) != json.dumps(trunc_a[gid], sort_keys=True)]
    assert not bad_a, f"availability pass leaked: {bad_a[:3]}"


def test_v2_is_v1_plus_avail_keys_and_v1_untouched(con) -> None:
    rows1, _ = B.build_rows(con, verbose=False, version="v1")
    rows2, _ = B.build_rows(con, verbose=False, version="v2")
    assert set(rows1) == set(rows2)
    for gid in rows1:
        assert rows1[gid]["feature_version"] == "v1"
        assert rows2[gid]["feature_version"] == "v2"
        assert not (set(rows1[gid]) & set(AV.AVAIL_KEYS)), "v1 row carries availability keys"
        assert set(rows2[gid]) - set(rows1[gid]) == set(AV.AVAIL_KEYS)
        v1_part = {k: v for k, v in rows2[gid].items()
                   if k not in AV.AVAIL_KEYS and k != "feature_version"}
        v1_ref = {k: v for k, v in rows1[gid].items() if k != "feature_version"}
        assert json.dumps(v1_part, sort_keys=True) == json.dumps(v1_ref, sort_keys=True), (
            f"v2 changed a v1 key for {gid} - the A5 arm depends on v1 being byte-identical")


def test_unknown_version_rejected(con) -> None:
    with pytest.raises(ValueError):
        B.build_rows(con, verbose=False, version="v9")


# ---- 3. sign sanity --------------------------------------------------------

def test_positive_avail_diff_means_home_healthier(con) -> None:
    feats = AV.build_availability(con)
    home_ok = feats["G_home_ok"]
    assert home_ok["avail_missing_away"] > home_ok["avail_missing_home"]
    assert home_ok["avail_diff"] > 0
    away_ok = feats["G_away_ok"]
    assert away_ok["avail_missing_home"] > away_ok["avail_missing_away"]
    assert away_ok["avail_diff"] < 0
    # The None case stays None (never a signed zero masquerading as signal).
    assert feats["G_dark"]["avail_diff"] is None
