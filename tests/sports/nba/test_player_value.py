"""Unit tests for the A9 player-value pass (synthetic data; no NBA DB needed)."""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest
from scipy import sparse

from sports.nba.features import player_value as PV

SCHEMA = """
CREATE TABLE games (game_id TEXT PRIMARY KEY, season TEXT, season_type TEXT, game_date TEXT,
                    home_team_id INTEGER, away_team_id INTEGER,
                    home_score INTEGER, away_score INTEGER);
CREATE TABLE game_traditional (game_id TEXT, team_id INTEGER, player_id INTEGER, minutes REAL,
                               pts INTEGER, ast INTEGER, reb INTEGER, tov INTEGER,
                               stl INTEGER, blk INTEGER, fg3m INTEGER);
CREATE TABLE game_inactives (game_id TEXT, team_id INTEGER, player_id INTEGER);
CREATE TABLE game_officials (game_id TEXT, official_id INTEGER);
"""


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _seed(con: sqlite3.Connection) -> None:
    """S1: six games among teams 1-4, all authoritative (officials rows), empty inactive
    lists except g3. S2: g7 (inactives row), g8 (empty list via officials only), g9 (no
    marker at all -> unknown). Player 100 plays exactly 36 minutes in every S1 game."""
    games = []
    for i in range(6):
        home, away = (1, 2) if i % 2 == 0 else (3, 4)
        games.append((f"g{i+1}", "S1", "regular", f"2020-01-0{i+1}", home, away, 110, 100))
    games.append(("g7", "S2", "regular", "2021-01-05", 1, 2, 108, 101))
    games.append(("g8", "S2", "regular", "2021-01-06", 3, 4, 99, 120))
    games.append(("g9", "S2", "regular", "2021-01-07", 1, 3, 105, 100))
    con.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?)", games)

    box = []
    for gid, _season, _st, _d, home, away, _hs, _as in games:
        for base, team in ((100, home), (110, away)):
            for k in range(5):
                pid = base + k
                minutes = 36.0 if pid == 100 else 28.0 + k
                box.append((gid, team, pid, minutes, 10 + k + pid % 9, 2 + pid % 3,
                            3 + pid % 4, 1, 1, 0, 1))
    con.executemany("INSERT INTO game_traditional VALUES (?,?,?,?,?,?,?,?,?,?,?)", box)

    # S1 games: officials rows make the (empty) inactive lists authoritative.
    for i in range(6):
        con.execute("INSERT INTO game_officials VALUES (?, ?)", (f"g{i+1}", 900 + i))
    con.execute("INSERT INTO game_officials VALUES ('g7', 906)")
    con.execute("INSERT INTO game_officials VALUES ('g8', 907)")

    con.execute("INSERT INTO game_inactives VALUES ('g3', 1, 100)")   # home side
    con.execute("INSERT INTO game_inactives VALUES ('g7', 1, 100)")
    con.execute("INSERT INTO game_inactives VALUES ('g7', 2, 114)")   # away side
    con.commit()


# ---------------------------------------------------------------- pure functions
def test_choose_lambda_picks_largest_within_tolerance():
    table = {1.0: 11.000, 3.0: 11.018, 10.0: 11.139, 30.0: 11.310, 100.0: 11.483}
    assert PV.choose_lambda(table) == 10.0


def test_choose_lambda_when_min_at_grid_edge():
    table = {1.0: 11.000, 3.0: 11.100, 10.0: 11.300}
    assert PV.choose_lambda(table) == 3.0


def test_fit_values_recovers_planted_values():
    rng = np.random.default_rng(7)
    V_true = np.array([5.0, 2.0, -3.0, 1.0, 0.0, 4.0])
    rows, cols, vals, ys = [], [], [], []
    for g in range(4000):
        x = np.zeros(len(V_true))
        for p in rng.choice(len(V_true), size=3, replace=False):
            x[p] += rng.uniform(10, 40) / 240.0
        for p in rng.choice(len(V_true), size=3, replace=False):
            x[p] -= rng.uniform(10, 40) / 240.0
        y = float(x @ V_true) + 2.5 + rng.normal(0, 1.0)
        for p in np.where(x != 0)[0]:
            rows.append(g); cols.append(int(p)); vals.append(float(x[p]))
        ys.append(y)
    X = sparse.csr_matrix((vals, (rows, cols)), shape=(4000, len(V_true)))
    V, intercept = PV.fit_values(X, np.array(ys), lam=1.0)
    assert np.allclose(V, V_true, atol=0.6)
    assert abs(intercept - 2.5) < 0.6


def test_archetype_shrinkage_pulls_low_exposure_toward_cluster_mean():
    V = np.array([10.0, 0.0, 8.0, 2.0, 10.0, 0.0, 7.0])
    exposure = np.array([24000.0, 24000.0, 24000.0, 24000.0, 480.0, 144.0, 0.0])
    profiles = np.zeros((7, 7))
    profiles[0:3, 0] = 20.0          # cluster A: players 0,1,2
    profiles[3:6, 0] = 5.0           # cluster B: players 3,4,5
    v = PV.archetype_shrink(V, exposure, profiles, prior=-1.0, k=2, seed=7)
    assert abs(v[0] - 10.0) < 0.2 and abs(v[1]) < 0.2            # high exposure ~ own value
    # cluster B's mean is exposure-weighted: (24000*2 + 480*10 + 144*0) / 24624
    vbar_b = (24000 * 2.0 + 480 * 10.0 + 144 * 0.0) / (24000 + 480 + 144)
    # 480 min in cluster B -> n=10: (10*10 + 16*vbar_b) / 26
    assert v[4] == pytest.approx((10 * 10.0 + 16 * vbar_b) / 26.0, abs=1e-9)
    # player 5 (own V=0, 144 min, cluster B) -> n=3: (3*0 + 16*vbar_b) / 19
    assert v[5] == pytest.approx((3 * 0.0 + 16 * vbar_b) / 19.0, abs=1e-9)
    assert v[5] < v[4] < 10.0
    assert v[6] == pytest.approx(-1.0)                            # no exposure -> prior


def test_archetype_seed_stability():
    rng = np.random.default_rng(0)
    n = 60
    V = rng.normal(0, 3, n)
    exposure = rng.uniform(200, 5000, n)
    profiles = rng.normal(0, 1, (n, 7))
    a = PV.archetype_shrink(V, exposure, profiles, prior=0.0, k=8, seed=7)
    b = PV.archetype_shrink(V, exposure, profiles, prior=0.0, k=8, seed=7)
    assert np.array_equal(a, b)


def test_time_blocked_cv_table_structure():
    rng = np.random.default_rng(1)
    X = sparse.random(200, 30, density=0.1, random_state=1).tocsr()
    y = rng.normal(0, 10, 200)
    table = PV.time_blocked_cv(X, y, grid=(1.0, 100.0))
    assert set(table) == {1.0, 100.0}
    assert all(np.isfinite(v) for v in table.values())


# ---------------------------------------------------------------- synthetic pass
def test_keys_and_publication_rule():
    con = _con(); _seed(con)
    out = PV.build_player_value(con)
    assert set(out) == {f"g{i}" for i in range(1, 10)}
    for row in out.values():
        assert set(row) == set(PV.PV_KEYS)
    # g9: no inactives and no officials rows -> unknown, never 0.0
    assert all(v is None for v in out["g9"].values())
    # g8: officials-only -> published empty list -> numeric zeros
    g8 = out["g8"]
    assert g8["pv_missing_home"] == 0.0 and g8["pv_share_home"] == 0.0
    assert g8["pv_top_out_home"] == 0 and g8["pv_minutes_out_home"] == 0.0


def test_first_season_is_neutral_and_minutes_track_history():
    con = _con(); _seed(con)
    out = PV.build_player_value(con)
    # before any refit exists the value prior is 0.0 (documented neutral)
    assert out["g3"]["pv_missing_home"] == 0.0
    # player 100 played exactly 36 minutes in g1 and g2 -> rolling mean 36.0 at g3
    assert out["g3"]["pv_minutes_out_home"] == pytest.approx(36.0, abs=1e-6)


def test_second_season_uses_refit_and_house_sign_convention():
    con = _con(); _seed(con)
    info: dict = {}
    out = PV.build_player_value(con, info_out=info)
    g7 = out["g7"]
    assert g7["pv_missing_home"] is not None
    assert g7["pv_missing_diff"] == pytest.approx(
        g7["pv_missing_away"] - g7["pv_missing_home"], abs=1e-9)
    assert 0.0 <= g7["pv_share_home"] <= 1.0
    assert g7["pv_top_out_home"] in (0, 1)
    assert g7["pv_minutes_out_home"] == pytest.approx(36.0, abs=1e-6)
    assert info["n_refits"] == 1
    assert info["refits"][0]["season"] == "S2"
    assert info["lambda"] in PV.LAMBDA_GRID


def test_emitted_values_are_keyed_by_player_id(monkeypatch):
    """Regression: the value lookup must be keyed by player_id (what rosters and
    inactive lists carry), not by the internal column position. The two coincide
    only by accident in tiny fixtures, so pin the emitted numbers with a fake
    shrinker that returns position + 0.5: the sorted id list is 100..104, 110..114,
    120..124, 130..134 (gaps), so player 100 -> 0.5 and player 114 -> 9.5."""

    def fake_shrink(V, exposure, profiles, prior, **kw):
        return np.arange(len(V), dtype=float) + 0.5

    con = _con(); _seed(con)
    monkeypatch.setattr(PV, "archetype_shrink", fake_shrink)
    out = PV.build_player_value(con)
    g7 = out["g7"]
    assert g7["pv_missing_home"] == pytest.approx(0.5)     # inactive home: player 100 (pos 0)
    assert g7["pv_missing_away"] == pytest.approx(9.5)     # inactive away: player 114 (pos 9)
    assert g7["pv_missing_diff"] == pytest.approx(9.0)
    # S1 has no refit yet -> the documented neutral prior of 0.0, not a fake value
    assert out["g3"]["pv_missing_home"] == pytest.approx(0.0)


def test_prefix_property_under_upto_cut():
    con = _con(); _seed(con)
    full = PV.build_player_value(con)
    trunc = PV.build_player_value(con, upto="2020-12-31")
    assert set(trunc) == {f"g{i}" for i in range(1, 7)}
    for gid in trunc:
        assert json.dumps(full[gid], sort_keys=True) == json.dumps(trunc[gid], sort_keys=True)


def test_deterministic_rebuild():
    con = _con(); _seed(con)
    a = PV.build_player_value(con)
    b = PV.build_player_value(con)
    assert all(json.dumps(a[g], sort_keys=True) == json.dumps(b[g], sort_keys=True) for g in a)
