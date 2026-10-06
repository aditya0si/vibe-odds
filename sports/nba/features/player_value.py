"""A9 (Phase-3 candidate): player-value availability features (v5).

The absence channel's third generation. availability.py (v2/A6) weights absences by a
box production share; impact.py (v3/A7) by a rolling plus/minus mean. Both are single
metric proxies. This module estimates each player's value V_j directly from outcomes:

    y_g  = home margin, winsorised to +/-MARGIN_CLIP (blowout noise)
    x_jg = minutes_home/240 - minutes_away/240          (sparse design)
    V    = argmin ||y - X V||^2 + lambda * ||V||^2      (sparse ridge)

Ridge is the variance reduction the rolling metrics lack: every teammate and opponent
appearance constrains V_j, with collinearity handled by the penalty.

LAMBDA PROTOCOL (frozen; pilot evidence: scratch/pv_feasibility_out.txt):
  * grid G = (1, 3, 10, 30, 100, 300);
  * chosen ONCE, at the FIRST refit that has data (the 2006-07 boundary - strictly
    prior-only, so no future information enters any earlier row), by 5-fold
    TIME-BLOCKED CV (contiguous blocks of the date-ordered window), then frozen;
  * the chosen lambda is the LARGEST grid point whose CV-RMSE is within LAMBDA_TOL
    (2%, relative) of the grid minimum - a stability-preferring rule: the pilot's
    split-half reliability rises 0.675 -> 0.872 -> 0.908 across lambda 1/10/100 while
    CV-RMSE is flat at the low end.

ARCHETYPE SHRINKAGE (the k-means step; seed 7, K=8): each player's as-of per-36
profile (pts/ast/reb/tov/stl/blk/fg3m) over the refit window is clustered; V_j is
shrunk toward its cluster's minutes-weighted mean:

    V_hat_j = (n_j V_j + m Vbar_c) / (n_j + m),   n_j = minutes/48,  m = ARCH_SHRINK

Players below ARCH_MIN_MINUTES of window exposure are not clustered and shrink toward
the window prior instead. Empirical-Bayes regularization of a sparse estimation
problem - NOT a partition of the signal (see docs/phase3-candidates.md).

PRIOR: a player with no rated history takes PRIOR_PCTL (25th percentile) of that
refit window's fitted V - computed per refit from strictly prior data, never from the
game being scored. Before any refit exists (the first season), the prior is 0.0.

AS-OF DISCIPLINE: refits happen at season boundaries on games with season < S
(strictly before S's first tip-off; seasons do not overlap). State (minutes
histories, team rosters, cumulative exposure/profiles) is updated only after a game's
features are emitted. `build_player_value(upto=...)` reproduces the full build's
prefix exactly (proved by the deletion test, test_player_value_leakage.py).

FEATURES (PV_KEYS; same publication rule as availability.py, reused verbatim - a game
is authoritative iff it has >=1 game_inactives row OR >=1 game_officials row; a
non-authoritative game is all-None, never 0.0):

  pv_missing_home/away      sum of V_hat over the inactive list (signed: a bad player
                            out is positive news, which a >=0 share cannot express)
  pv_missing_diff           away - home (positive favours home; house convention)
  pv_share_home/away        sum of max(V_hat,0) over the inactive list / sum of
                            max(V_hat,0) over (as-of roster + inactive) - in [0,1]
  pv_share_diff             away - home
  pv_top_out_home/away      1 iff a roster top-3 by V_hat is inactive
  pv_minutes_out_home/away  sum of the inactive players' rolling-mean minutes (last
                            MIN_WINDOW appearances; unknown history -> 0.0)
  pv_minutes_out_diff       away - home
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict, deque

import numpy as np
from scipy import sparse

from sports.nba.features.availability import games_with_authoritative_inactives

MARGIN_CLIP = 20.0
LAMBDA_GRID = (1.0, 3.0, 10.0, 30.0, 100.0, 300.0)
LAMBDA_TOL = 0.02
CV_FOLDS = 5
ARCH_K = 8
ARCH_SEED = 7
ARCH_SHRINK = 16.0
ARCH_MIN_MINUTES = 100.0
PRIOR_PCTL = 25.0
ROSTER_WINDOW = 10          # team games defining the as-of roster (availability.py parity)
MIN_WINDOW = 20             # appearances for the rolling-mean minutes
PROFILE_COLS = ("pts", "ast", "reb", "tov", "stl", "blk", "fg3m")

PV_KEYS = (
    "pv_missing_home", "pv_missing_away", "pv_missing_diff",
    "pv_share_home", "pv_share_away", "pv_share_diff",
    "pv_top_out_home", "pv_top_out_away",
    "pv_minutes_out_home", "pv_minutes_out_away", "pv_minutes_out_diff",
)


def _mean_minutes(hist) -> float:
    """Rolling-mean minutes for one player's appearance history (empty -> 0.0)."""
    return (sum(hist) / len(hist)) if len(hist) else 0.0


# ------------------------------------------------------------------ pure pieces
def choose_lambda(table: dict[float, float]) -> float:
    """Frozen rule: the largest grid lambda within LAMBDA_TOL (relative) of the minimum."""
    best = min(table.values())
    ok = [lam for lam, rmse in table.items() if rmse <= best * (1.0 + LAMBDA_TOL)]
    return max(ok)


def time_blocked_cv(X, y, grid=LAMBDA_GRID, folds: int = CV_FOLDS) -> dict[float, float]:
    """5-fold contiguous-block CV over the date-ordered window -> {lambda: mean RMSE}."""
    from sklearn.linear_model import Ridge

    n = X.shape[0]
    bounds = [(k * n // folds, (k + 1) * n // folds) for k in range(folds)]
    table: dict[float, float] = {}
    for lam in grid:
        errs = []
        for lo, hi in bounds:
            mask = np.ones(n, bool)
            mask[lo:hi] = False
            m = Ridge(alpha=lam, solver="sparse_cg", max_iter=1000, tol=1e-3).fit(X[mask], y[mask])
            errs.append(float(np.sqrt(np.mean((m.predict(X[~mask]) - y[~mask]) ** 2))))
        table[float(lam)] = float(np.mean(errs))
    return table


def fit_values(X, y, lam: float) -> tuple[np.ndarray, float]:
    """Sparse ridge solve -> (V, intercept)."""
    from sklearn.linear_model import Ridge

    m = Ridge(alpha=lam, solver="sparse_cg", max_iter=5000, tol=1e-6).fit(X, y)
    return m.coef_.copy(), float(m.intercept_)


def archetype_shrink(V, exposure, profiles, prior, k: int = ARCH_K, seed: int = ARCH_SEED,
                     m_shrink: float = ARCH_SHRINK, min_minutes: float = ARCH_MIN_MINUTES) -> np.ndarray:
    """k-means archetype shrinkage: V_hat = (n V + m Vbar_c)/(n+m); unclustered -> prior."""
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    V = np.asarray(V, dtype=float)
    exposure = np.asarray(exposure, dtype=float)
    v_hat = np.full(V.shape, float(prior))

    def shrink(v, n_eq, target):
        return (n_eq * v + m_shrink * target) / (n_eq + m_shrink)

    n_eq = exposure / 48.0
    for j in np.where(exposure > 0)[0]:
        v_hat[j] = shrink(V[j], n_eq[j], prior)

    idx_cl = np.where(exposure >= min_minutes)[0]
    if len(idx_cl) >= k:
        Xa = StandardScaler().fit_transform(np.asarray(profiles, dtype=float)[idx_cl])
        labels = KMeans(n_clusters=k, random_state=seed, n_init=10).fit_predict(Xa)
        for c in range(k):
            members = idx_cl[labels == c]
            if len(members) == 0:
                continue
            vbar = float(np.average(V[members], weights=exposure[members]))
            for j in members:
                v_hat[j] = shrink(V[j], n_eq[j], vbar)
    return v_hat


# ------------------------------------------------------------------ the pass
def build_player_value(con: sqlite3.Connection, upto: str | None = None,
                       verbose: bool = False, info_out: dict | None = None) -> dict[str, dict]:
    """Chronological pass over games -> {game_id: pv_* features}."""
    sql = """SELECT g.game_id, g.season, g.game_date, g.home_team_id, g.away_team_id,
                    g.home_score, g.away_score
             FROM games g
             WHERE g.season_type='regular' AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL"""
    params: tuple = ()
    if upto:
        sql += " AND g.game_date <= ?"
        params = (upto,)
    sql += " ORDER BY g.game_date, g.game_id"
    games = list(con.execute(sql, params))

    box_sql = ("SELECT t.game_id, t.team_id, t.player_id, t.minutes, "
               + ", ".join(f"t.{c}" for c in PROFILE_COLS) + " FROM game_traditional t")
    box_params: tuple = ()
    if upto:
        box_sql += " WHERE t.game_id IN (SELECT game_id FROM games WHERE game_date <= ?)"
        box_params = (upto,)
    box = list(con.execute(box_sql, box_params))

    players = sorted({r[2] for r in box})
    pcol = {p: i for i, p in enumerate(players)}
    n_p = len(players)
    gidx = {g[0]: i for i, g in enumerate(games)}

    entries: list[list] = [[] for _ in games]      # per game: (player_col, side, minutes, stats)
    for r in box:
        i = gidx.get(r[0])
        mins = r[3] or 0.0
        if i is None or mins <= 0:
            continue
        g = games[i]
        side = 1.0 if r[1] == g[3] else (-1.0 if r[1] == g[4] else 0.0)
        if side == 0.0:
            continue
        stats = tuple(float(r[4 + k] or 0) for k in range(len(PROFILE_COLS)))
        entries[i].append((pcol[r[2]], side, float(mins), stats))

    y = np.array([np.clip(g[5] - g[6], -MARGIN_CLIP, MARGIN_CLIP) for g in games], dtype=float)
    ri, cj, dv = [], [], []
    for i in range(len(games)):
        for (pc, side, mins, _stats) in entries[i]:
            ri.append(i); cj.append(pc); dv.append(side * mins / 240.0)
    X = sparse.csr_matrix((dv, (ri, cj)), shape=(len(games), n_p))

    first_idx: dict[str, int] = {}
    for i, g in enumerate(games):
        first_idx.setdefault(g[1], i)

    inactive_by_game: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for r in con.execute("SELECT game_id, team_id, player_id FROM game_inactives"):
        inactive_by_game[r[0]][r[1]].add(r[2])
    authoritative = games_with_authoritative_inactives(con)

    exposure_run = np.zeros(n_p)
    stats_run = np.zeros((n_p, len(PROFILE_COLS)))
    minutes_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=MIN_WINDOW))
    team_recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=ROSTER_WINDOW))

    lam: float | None = None
    lam_table: dict[float, float] | None = None
    refits: list[dict] = []
    out: dict[str, dict] = {}
    cur_season: str | None = None
    Vd: dict[int, float] = defaultdict(float)
    prior = 0.0

    for i, g in enumerate(games):
        gid, season = g[0], g[1]
        if season != cur_season:
            cur_season = season
            b = first_idx[season]
            if b > 0:
                exposure = exposure_run.copy()
                active = np.where(exposure > 0)[0]
                # Active columns only: a superset of all-zero columns makes sparse_cg
                # converge along a slightly different float path, which is enough to
                # flip 4th-decimal rounding in early seasons - and that leaks the future
                # into past rows (caught by the deletion test). Restricting the design to
                # players who have actually appeared makes the refit system identical in
                # every build.
                Xa = X[:b][:, active]
                if lam is None:
                    lam_table = time_blocked_cv(Xa, y[:b])
                    lam = choose_lambda(lam_table)
                V_a, intercept = fit_values(Xa, y[:b], lam)
                V_raw = np.zeros(n_p)
                V_raw[active] = V_a
                prior = float(np.percentile(V_a, PRIOR_PCTL)) if len(active) else 0.0
                profiles = np.zeros((n_p, len(PROFILE_COLS)))
                with np.errstate(divide="ignore", invalid="ignore"):
                    profiles[active] = stats_run[active] / (exposure[active, None] / 36.0)
                v_hat = archetype_shrink(V_raw, exposure, profiles, prior)
                # Keyed by PLAYER ID: rosters and inactive lists carry player ids, not
                # column positions (the two coincide only by accident in tiny fixtures;
                # a position-keyed dict silently degrades to the prior everywhere else).
                Vd = defaultdict(lambda: prior)
                for pc in active:
                    Vd[players[pc]] = float(v_hat[pc])
                refits.append({
                    "season": season, "n_prior_games": b,
                    "n_players_fitted": int(len(active)),
                    "prior": round(prior, 4), "intercept_hca": round(intercept, 3),
                })
            else:
                Vd = defaultdict(float)
                prior = 0.0

        home, away = g[3], g[4]
        if gid not in authoritative:
            out[gid] = {k: None for k in PV_KEYS}
        else:
            feats: dict[str, float | int] = {}
            for side, tid in (("home", home), ("away", away)):
                roster: set[int] = set()
                for players_ in team_recent.get(tid, []):
                    roster |= players_
                inactive = inactive_by_game.get(gid, {}).get(tid, set())
                v_inact = {p: Vd[p] for p in inactive}
                missing = round(float(sum(v_inact.values())), 4)
                pos_union = sum(max(v, 0.0) for v in
                                {**{p: Vd[p] for p in roster}, **v_inact}.values())
                pos_inact = sum(max(v, 0.0) for v in v_inact.values())
                share = round(pos_inact / pos_union, 4) if pos_union > 0 else 0.0
                top = sorted(((p, Vd[p]) for p in roster), key=lambda kv: -kv[1])[:3]
                top_out = 1 if any(p in inactive for p, _ in top) else 0
                minutes_out = round(float(sum(_mean_minutes(minutes_hist.get(p, ()))
                                              for p in inactive)), 4)
                feats[f"pv_missing_{side}"] = missing
                feats[f"pv_share_{side}"] = share
                feats[f"pv_top_out_{side}"] = top_out
                feats[f"pv_minutes_out_{side}"] = minutes_out
            for stem in ("missing", "share", "minutes_out"):
                feats[f"pv_{stem}_diff"] = round(feats[f"pv_{stem}_away"] - feats[f"pv_{stem}_home"], 4)
            out[gid] = feats

        # ---- state updates, after emitting the row ----
        for (pc, _side, mins, stats) in entries[i]:
            exposure_run[pc] += mins
            stats_run[pc] += np.asarray(stats)
            minutes_hist[players[pc]].append(mins)
        played: dict[int, set[int]] = defaultdict(set)
        for (pc, side, mins, _stats) in entries[i]:
            played[home if side > 0 else away].add(players[pc])
        for tid, players_ in played.items():
            team_recent[tid].append(players_)

    if info_out is not None:
        info_out.update({
            "n_games": len(games), "n_players": n_p,
            "lambda_table_first_refit": {str(k): round(v, 4) for k, v in (lam_table or {}).items()},
            "lambda": lam,
            "n_refits": len(refits),
            "refits": refits,
            "n_authoritative": sum(1 for gid in out if gid in authoritative),
            "n_unknown": sum(1 for gid in out if gid not in authoritative),
        })
    if verbose:
        known = sum(1 for f in out.values() if f["pv_missing_home"] is not None)
        print(f"player value built for {len(out)} games; usable on {known}")
    return out
