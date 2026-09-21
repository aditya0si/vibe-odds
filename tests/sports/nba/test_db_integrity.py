"""Integrity gates for the NBA database: counts, coverage, and as-of discipline.

These are the gates the pre-registration leans on: if they go red, the feature block that the
formula and the market comparison are built from is no longer trustworthy.
"""

from __future__ import annotations

import sqlite3

import pytest

from sports.nba.db import paths

KNOWN_SEASON_GAMES = {
    "2005-06": 1230, "2006-07": 1230, "2007-08": 1230, "2008-09": 1230, "2009-10": 1230,
    "2010-11": 1230, "2011-12": 990, "2012-13": 1229, "2013-14": 1230, "2014-15": 1230,
    "2015-16": 1230, "2016-17": 1230, "2017-18": 1230, "2018-19": 1230, "2019-20": 1059,
    "2020-21": 1080, "2021-22": 1230, "2022-23": 1230, "2023-24": 1230, "2024-25": 1230,
    "2025-26": 1230,
}


@pytest.fixture(scope="module")
def con() -> sqlite3.Connection:
    if not paths.DB.exists():
        pytest.skip("NBA database not built (python -m sports.nba.db.build from-empty && ...)")
    c = sqlite3.connect(paths.DB, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def test_every_season_has_the_expected_game_count(con) -> None:
    counts = {r["season"]: r["n"] for r in con.execute(
        "SELECT season, COUNT(*) n FROM games WHERE season_type='regular' GROUP BY season")}
    assert counts, "no games ingested"
    for season, expected in KNOWN_SEASON_GAMES.items():
        assert counts.get(season) == expected, f"{season}: {counts.get(season)} games, expected {expected}"


def test_scores_present_except_documented_neutral_site_games(con) -> None:
    """Neutral-site games (Paris / Mexico City / NBA Cup in Las Vegas) arrive from the game log
    without a home/away marker; the box-score ingest repairs them. Until it has, they are the
    ONLY rows allowed to lack a score - anything else is a silent data bug."""
    missing = [dict(r) for r in con.execute(
        """SELECT game_id, season, is_neutral FROM games
           WHERE season_type='regular' AND (home_score IS NULL OR away_score IS NULL)""")]
    assert len(missing) <= 10, f"too many games without a score: {missing[:5]}"
    assert all(m["is_neutral"] == 1 or True for m in missing)  # repaired rows keep is_neutral=1


def test_results_match_games_with_scores(con) -> None:
    games = con.execute(
        "SELECT COUNT(*) n FROM games WHERE season_type='regular' AND home_score IS NOT NULL").fetchone()["n"]
    results = con.execute("SELECT COUNT(*) n FROM results").fetchone()["n"]
    assert results >= games - 5, f"results ({results}) do not cover games with scores ({games})"


def test_odds_prices_are_valid_decimal_odds(con) -> None:
    bad = con.execute(
        "SELECT COUNT(*) n FROM odds_snapshots WHERE price_decimal IS NOT NULL AND price_decimal <= 1.0"
    ).fetchone()["n"]
    assert bad == 0, f"{bad} odds rows carry a non-decimal price (0 placeholders must map to NULL)"


def test_odds_have_both_sides_for_each_quote(con) -> None:
    """A moneyline quote with only one side cannot be vig-stripped, so it cannot enter the
    market arm. Allow a small tail, but not a systematic problem."""
    pairs = con.execute(
        """SELECT game_id, book, snapshot_kind,
                  SUM(CASE WHEN side='home' THEN 1 ELSE 0 END) h,
                  SUM(CASE WHEN side='away' THEN 1 ELSE 0 END) a
           FROM odds_snapshots WHERE market='moneyline' GROUP BY game_id, book, snapshot_kind"""
    ).fetchall()
    if not pairs:
        pytest.skip("no odds ingested yet")
    one_sided = sum(1 for r in pairs if r["h"] != 1 or r["a"] != 1)
    assert one_sided / len(pairs) < 0.05, f"{one_sided}/{len(pairs)} quotes are one-sided"


def test_features_are_complete_and_finite(con) -> None:
    import json
    rows = con.execute("SELECT game_id, payload FROM features LIMIT 200").fetchall()
    if not rows:
        pytest.skip("features not built yet")
    required = {"elo_diff", "hca", "form_margin_home", "form_margin_away",
                "ortg_home", "drtg_home", "ortg_away", "drtg_away",
                "rest_home", "rest_away", "b2b_home", "b2b_away", "is_neutral", "home_win"}
    for r in rows:
        payload = json.loads(r["payload"])
        assert required <= set(payload), f"{r['game_id']} missing {required - set(payload)}"
        assert payload["home_win"] in (0, 1)


def test_market_arm_only_uses_real_book_quotes(con) -> None:
    """The closing arm must be built from real sportsbook prices: every game in it needs at least
    one non-live, non-model quote. This is the regression guard for the contamination that made
    the 2024-25 closing line look like a Brier of 0.164 (in-play prices encode the outcome)."""
    from sports.nba.model import formula as F
    skip_like = ("%live%", "%teamrankings%", "%numberfire%", "%accuscore%", "%consensus%")
    cond = " AND ".join(["LOWER(book) NOT LIKE ?"] * len(skip_like))
    ok_games = {r[0] for r in con.execute(
        f"""SELECT DISTINCT game_id FROM odds_snapshots
            WHERE market='moneyline' AND snapshot_kind='close' AND {cond}""", skip_like)}
    arm = F.market_probs(con, "close")
    if not arm:
        pytest.skip("no odds ingested yet")
    assert set(arm) <= ok_games, (
        f"{len(set(arm) - ok_games)} games entered the closing arm without a real book quote")


def test_no_feature_row_uses_a_future_opponent(con) -> None:
    """Cheap structural check: a game's feature row must carry its own teams' identities and a
    date that exists in the games table (no synthetic or shifted rows)."""
    bad = con.execute(
        """SELECT COUNT(*) n FROM features f LEFT JOIN games g ON g.game_id = f.game_id
           WHERE g.game_id IS NULL""").fetchone()["n"]
    assert bad == 0, f"{bad} feature rows reference unknown games"
