"""Player-prop capture discipline (props board, 2026-27).

See tools/props_capture.py, core/providers/sharpapi.py and
core/providers/sportsgameodds.py. Fixtures mirror the payload shapes each source
documents (SharpAPI odds schema read from docs.sharpapi.io 2026-10-13; SGO from
its documented odd-ID grammar `statID-statEntityID-period-betTypeID-sideID`).

What these tests defend, in order of how much a mistake would cost:
  1. a price is never invented (placeholder 0 / 1.0 / missing -> dropped, with a reason)
  2. a post-tip row is not a quote (dropped, mirroring require_pre_tip)
  3. an unresolved team/game is dropped, never matched onto a nearby game
  4. a MOVED line is a new row (the ladder), while a repeat capture is idempotent
  5. a missing key skips cleanly and touches no store
"""
from __future__ import annotations

import sqlite3

import pytest

from core.providers import sharpapi as SA
from core.providers import sportsgameodds as SGO
from tools import props_capture as P

SCHEMA = """
CREATE TABLE teams(team_id INTEGER PRIMARY KEY, abbr TEXT, full_name TEXT);
CREATE TABLE games(
  game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT, game_date TEXT,
  tipoff_ts TEXT, home_team_id INTEGER, away_team_id INTEGER,
  home_score INTEGER, away_score INTEGER);
CREATE TABLE prop_lines(
  game_id TEXT NOT NULL, source TEXT NOT NULL, book TEXT NOT NULL,
  player_name TEXT NOT NULL, player_key TEXT NOT NULL, market TEXT NOT NULL,
  market_type TEXT NOT NULL, side TEXT NOT NULL, line REAL NOT NULL,
  price_decimal REAL, price_raw TEXT, implied_prob REAL, is_alternate_line INTEGER,
  market_segment TEXT, event_start_time TEXT, source_ts TEXT, flags TEXT,
  captured_at TEXT, snapshot_kind TEXT NOT NULL, asof_ts TEXT NOT NULL,
  PRIMARY KEY (game_id, source, book, player_key, market_type, side, line, snapshot_kind));
"""

TIP = "2027-01-15T00:30:00Z"          # future-dated: a pre-tip capture is legal
PAST = "2026-01-01T00:30:00Z"         # already tipped
ASOF = "2027-01-15T00:10:00Z"


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.execute("INSERT INTO teams VALUES (2,'BOS','Boston Celtics')")
    con.execute("INSERT INTO teams VALUES (13,'LAL','Los Angeles Lakers')")
    con.execute("INSERT INTO games VALUES ('g_bos_lal','2026-27','regular','2027-01-15',?,2,13,NULL,NULL)", (TIP,))
    con.commit()
    return con


def _row(**kw) -> dict:
    base = {
        "market_type": "player_points", "player_name": "LeBron James",
        "selection": "Over", "selection_type": "over", "line": 25.5,
        "odds_decimal": 1.909, "odds_american": -110, "odds_probability": 0.5238,
        "sportsbook": "DraftKings", "event_id": "nba_lal_bos_2027-01-15",
        "event_start_time": TIP, "is_player_prop": True, "is_active": True,
        "is_alternate_line": False, "market_segment": "full_game",
        "timestamp": "2027-01-15T00:05:00Z",
        "home_team": "Boston Celtics", "away_team": "Los Angeles Lakers",
        "home": {"abbreviation": "BOS"}, "away": {"abbreviation": "LAL"},
    }
    base.update(kw)
    return base


def _payload(*rows) -> dict:
    return {"data": list(rows)}


# ---------------------------------------------------------------- 1. pricing
def test_sharpapi_keeps_only_priced_player_rows():
    """Game lines and line-less rows leave the board with a stated reason."""
    rows, drops = SA.normalize_rows(_payload(
        _row(),
        _row(selection="Under", selection_type="under", line=25.5),
        _row(market_type="moneyline", is_player_prop=False, line=None),
        _row(line=None),
    ))
    assert len(rows) == 2, "over + under of one player"
    assert {r["side"] for r in rows} == {"over", "under"}
    assert [d["reason"] for d in drops] == ["not_player_prop", "no_line"]


def test_sharpapi_american_only_price_is_converted():
    rows, _ = SA.normalize_rows(_payload(_row(odds_decimal=None, odds_american=150)))
    assert rows[0]["price_decimal"] == pytest.approx(2.5)
    rows, _ = SA.normalize_rows(_payload(_row(odds_decimal=None, odds_american=-200)))
    assert rows[0]["price_decimal"] == pytest.approx(1.5)


def test_placeholder_prices_and_suspended_markets_are_never_stored():
    """0 and exactly 1.0 are what a dead feed sends; a frozen price is not a quote."""
    rows, drops = SA.normalize_rows(_payload(
        _row(odds_decimal=0, odds_american=None),
        _row(odds_decimal=1.0, odds_american=None),
        _row(is_active=False),
    ))
    assert rows == []
    assert [d["reason"] for d in drops] == ["no_usable_price", "no_usable_price", "inactive_market"]


def test_market_and_side_normalization_covers_combos():
    rows, _ = SA.normalize_rows(_payload(
        _row(market_type="player_points_rebounds_assists"),
        _row(market_type="player_three_pointers_made"),
    ))
    assert [r["market"] for r in rows] == ["pra", "threes"]


# ------------------------------------------------------- 2/3. guards + join
def test_post_tip_rows_are_dropped_not_stored():
    con = _db()
    rows, drops = SA.normalize_rows(_payload(_row(event_start_time=PAST)))
    kept, rejected = P.prepare_rows(con, rows, asof=ASOF)
    assert kept == []
    assert rejected[0]["reason"] == "post_tip"
    assert con.execute("SELECT COUNT(*) FROM prop_lines").fetchone()[0] == 0


def test_unknown_team_is_dropped_rather_than_matched():
    con = _db()
    rows, _ = SA.normalize_rows(_payload(_row(
        home_team="Fake Team", away_team="Los Angeles Lakers",
        home={"abbreviation": "ZZZ"}, away={"abbreviation": "LAL"})))
    kept, rejected = P.prepare_rows(con, rows, asof=ASOF)
    assert kept == []
    assert rejected[0]["reason"] == "unresolved_game"


def test_team_resolution_accepts_aliases_and_names():
    con = _db()
    assert P.resolve_team_id(con, "Boston Celtics", None) == 2
    assert P.resolve_team_id(con, None, "bos") == 2
    assert P.resolve_team_id(con, "The Lakers", "LAL") == 13
    assert P.resolve_team_id(con, "Nowhere", None) is None


# ------------------------------------------------------------- 4. the ladder
def test_store_is_idempotent_and_a_moved_line_is_a_new_row():
    con = _db()
    rows, _ = SA.normalize_rows(_payload(_row()))
    kept, _ = P.prepare_rows(con, rows, asof=ASOF)
    assert P.store_rows(con, kept) == 1
    assert P.store_rows(con, kept) == 0, "the same price twice must not double-count"

    moved, _ = SA.normalize_rows(_payload(_row(line=27.5, odds_decimal=1.87)))
    kept_moved, _ = P.prepare_rows(con, moved, asof=ASOF)
    assert P.store_rows(con, kept_moved) == 1, "a moved line is new evidence"
    lines = [r[0] for r in con.execute("SELECT line FROM prop_lines ORDER BY line")]
    assert lines == [25.5, 27.5]


def test_player_key_is_canonical_for_the_history_join():
    con = _db()
    rows, _ = SA.normalize_rows(_payload(_row(player_name="Nikola Joki\u0107")))
    kept, _ = P.prepare_rows(con, rows, asof=ASOF)
    assert kept[0]["player_key"] == "Nikola Jokic"


# -------------------------------------------------------- sportsgameodds side
def test_sgo_odd_id_grammar_separates_players_from_teams():
    part = SGO.parse_odd_id("assists-LEBRON_JAMES_NBA-game-ou-over")
    assert part and part["is_player_prop"] and part["side"] == "over"
    assert part["stat_id"] == "assists"
    assert SGO.parse_odd_id("points-all-game-ou-over")["is_player_prop"] is False
    assert SGO.parse_odd_id("points-all-ou-over") is None          # 4 segments
    assert SGO.parse_odd_id("points-all-game-ml-home") is None      # not an over/under


def test_sgo_documented_price_container_parses():
    payload = {"data": [{
        "eventID": "sgo_1", "startsAt": TIP,
        "teams": {"BOS": {"teamID": "BOS", "isHome": True,
                          "names": {"long": "Boston Celtics", "short": "BOS"}},
                  "LAL": {"teamID": "LAL", "isHome": False,
                          "names": {"long": "Los Angeles Lakers", "short": "LAL"}}},
        "players": {"LEBRON_JAMES_NBA": {"names": {"long": "LeBron James"}}},
        "odds": {"assists-LEBRON_JAMES_NBA-game-ou-over":
                 {"byBookmaker": {"fanduel": {"bookOverUnder": 5.5, "bookOdds": 1.87}}}},
    }]}
    rows, drops = SGO.normalize_events(payload)
    assert not drops
    assert rows[0]["player_name"] == "LeBron James"
    assert rows[0]["market"] == "assists" and rows[0]["side"] == "over"
    assert rows[0]["line"] == 5.5 and rows[0]["price_decimal"] == 1.87
    assert rows[0]["book"] == "fanduel"


def test_sgo_unverified_shape_is_dropped_never_guessed():
    """Docs could not be read from this network for the per-book container, so an
    unknown shape must fail loudly as a drop — never invent a price."""
    payload = {"data": [{
        "eventID": "sgo_2", "startsAt": TIP,
        "teams": {}, "players": {"X_NBA": {"names": {"long": "X Player"}}},
        "odds": {"points-X_NBA-game-ou-over": {"somethingNew": {"line": 20.5}}},
    }]}
    rows, drops = SGO.normalize_events(payload)
    assert rows == []
    assert drops and drops[0]["reason"].startswith("unverified_shape")


# ------------------------------------------------------------------ 5. no key
def test_missing_key_skips_without_touching_a_store(monkeypatch, capsys):
    monkeypatch.delenv(SA.ENV_KEY, raising=False)
    monkeypatch.delenv(SGO.ENV_KEY, raising=False)

    def _explode():
        raise AssertionError("capture must not open the DB when the key is missing")

    monkeypatch.setattr(P.build, "connect", _explode)
    assert P.main(["capture", "--source", "sharpapi"]) == 2
    out = capsys.readouterr().out
    assert SA.ENV_KEY in out and "SKIP" in out
