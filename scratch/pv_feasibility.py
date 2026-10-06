"""A9 Stage 0.2/0.3 - value-model pilot: reliability, disagreement, archetypes.

Fits the Stage-0 value model on completed regular games <= 2020-21:

    y_g = home margin (winsorised +/-20)
    x_jg = minutes_home/240 - minutes_away/240        (per player, sparse)
    y ~ x.V + h                                       via ridge

lambda by 5-fold time-blocked CV (the frozen protocol, run once at the first refit).

Measures (pre-stated decision gate from the plan):
  (a) split-half reliability of V (odd vs even games, exposure-weighted Pearson)
  (b) rank correlation of V vs the incumbent metrics (window-mean production score;
      shrunk window-mean plus/minus, mirroring impact.py's estimator)
  (c) top-3 disagreement: share of team-SEASONS whose top-3-by-V set differs from
      top-3-by-production (team-season rosters mirror the feature's as-of roster)
  (d) archetype pilot: K=8 k-means on per-36 profiles; silhouette, sizes, and the
      minutes-weighted spread of V across clusters

PROCEED iff reliability >= 0.6 AND top-3 disagreement >= 15%.

Read-only. Usage: .venv/Scripts/python.exe scratch/pv_feasibility.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.stats import pearsonr, spearmanr
from sklearn.cluster import KMeans
from sklearn.linear_model import Ridge
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
con = sqlite3.connect(str(ROOT / "sports" / "nba" / "data" / "nba.sqlite"))
con.row_factory = sqlite3.Row
t0 = time.time()
TRAIN_END = "2020-21"
COLS = ["pts", "ast", "reb", "tov", "stl", "blk", "fg3m"]

games = con.execute(
    "SELECT game_id, game_date, home_team_id, away_team_id, home_score, away_score FROM games "
    "WHERE season_type='regular' AND home_score IS NOT NULL AND away_score IS NOT NULL "
    "AND season <= ? ORDER BY game_date, game_id", (TRAIN_END,)).fetchall()
print(f"fit window: {len(games)} games <= {TRAIN_END}")

box = con.execute(
    "SELECT t.game_id, g.season, t.team_id, t.player_id, t.minutes, t.pts, t.ast, t.reb, t.tov, "
    "       t.stl, t.blk, t.fg3m, t.plus_minus "
    "FROM game_traditional t JOIN games g ON g.game_id = t.game_id "
    "WHERE g.season_type='regular' AND g.home_score IS NOT NULL AND g.season <= ? "
    "ORDER BY t.game_id", (TRAIN_END,)).fetchall()
print(f"player-rows: {len(box)}  ({time.time()-t0:.1f}s)")

players = sorted({r["player_id"] for r in box})
pidx = {p: i for i, p in enumerate(players)}
n_p = len(players)
print(f"players: {n_p}")

by_game = defaultdict(list)
for r in box:
    by_game[r["game_id"]].append(r)

# ---------------------------------------------------------------- design matrix
y = np.zeros(len(games))
ri, cj, dv = [], [], []
mins_total = np.zeros(n_p)
acc = defaultdict(lambda: defaultdict(float))          # per player: stat sums + minutes
team_season = defaultdict(lambda: defaultdict(float))  # (team, season) -> {player: minutes}
for i, g in enumerate(games):
    y[i] = np.clip(g["home_score"] - g["away_score"], -20, 20)
    for r in by_game.get(g["game_id"], []):
        m = r["minutes"] or 0.0
        if m <= 0:
            continue
        p = pidx[r["player_id"]]
        share = m / 240.0
        if r["team_id"] == g["home_team_id"]:
            pass
        elif r["team_id"] == g["away_team_id"]:
            share = -share
        else:
            continue
        ri.append(i); cj.append(p); dv.append(share)
        mins_total[p] += m
        team_season[(r["team_id"], r["season"])][p] += m
        a = acc[p]
        a["min"] += m
        for col in COLS:
            a[col] += (r[col] or 0)
X = sparse.csr_matrix((dv, (ri, cj)), shape=(len(games), n_p))
print(f"design: {X.shape}, nnz={X.nnz}  ({time.time()-t0:.1f}s)")

# ---------------------------------------------------------------- lambda CV (frozen protocol)
n = len(games)
nf = 5
bounds = [(k * n // nf, (k + 1) * n // nf) for k in range(nf)]
grid = [1.0, 3.0, 10.0, 30.0, 100.0, 300.0]
best = None
for lam in grid:
    errs = []
    for lo, hi in bounds:
        mask = np.ones(n, bool); mask[lo:hi] = False
        m = Ridge(alpha=lam, solver="sparse_cg", max_iter=1000, tol=1e-3).fit(X[mask], y[mask])
        errs.append(float(np.sqrt(np.mean((m.predict(X[~mask]) - y[~mask]) ** 2))))
    rmse = float(np.mean(errs))
    print(f"  lambda={lam:>7}: cv_rmse={rmse:.4f}")
    if best is None or rmse < best[1]:
        best = (lam, rmse)
lam = best[0]
print(f"chosen lambda={lam} (cv_rmse {best[1]:.4f})  ({time.time()-t0:.1f}s)")

model = Ridge(alpha=lam, solver="sparse_cg", max_iter=5000, tol=1e-6).fit(X, y)
V = model.coef_.copy()
print(f"intercept (HCA pts) = {model.intercept_:+.2f}")

top = sorted(range(n_p), key=lambda p: -V[p])[:8]
print("top-8 by V:", [(players[p], round(float(V[p]), 2), int(mins_total[p])) for p in top])

# ---------------------------------------------------------------- (a) split-half reliability
half = np.arange(n) % 2 == 0
ri_a, cj_a, dv_a = np.array(ri), np.array(cj), np.array(dv)


def half_exposure(sel):
    e = sel[ri_a]                                  # per-entry mask for this half
    # abs(): dv is signed (home +, away -); exposure must not cancel home vs away
    return np.bincount(cj_a[e], weights=np.abs(dv_a[e]) * 240.0, minlength=n_p)


exph = [half_exposure(half), half_exposure(~half)]
sel2 = (exph[0] >= 300) & (exph[1] >= 300)
w = np.minimum(exph[0], exph[1])[sel2]


def reliability(lam_):
    Vh = [Ridge(alpha=lam_, solver="sparse_cg", max_iter=5000, tol=1e-6).fit(X[s], y[s]).coef_.copy()
          for s in (half, ~half)]
    v1, v2 = Vh[0][sel2], Vh[1][sel2]
    m1, m2 = np.average(v1, weights=w), np.average(v2, weights=w)
    cv_ = np.average((v1 - m1) * (v2 - m2), weights=w)
    rw = float(cv_ / np.sqrt(np.average((v1 - m1) ** 2, weights=w) * np.average((v2 - m2) ** 2, weights=w)))
    return rw, float(pearsonr(v1, v2)[0])


print(f"(a) split-half reliability by lambda (n={int(sel2.sum())} players >=300 min per half):")
rel_at = {}
for lam_ in (1.0, 3.0, 10.0, 30.0, 100.0):
    rw, rp = reliability(lam_)
    rel_at[lam_] = rw
    print(f"    lambda={lam_:>6}: weighted={rw:.3f} plain={rp:.3f}")
rel_w = rel_at.get(lam, max(rel_at.values()))

# ---------------------------------------------------------------- (b) vs incumbents
prod_sum = np.zeros(n_p); prod_n = np.zeros(n_p)
pm_sum = np.zeros(n_p); pm_n = np.zeros(n_p)
for r in box:
    m = r["minutes"] or 0
    if m <= 0:
        continue
    p = pidx[r["player_id"]]
    prod_sum[p] += (r["pts"] or 0) + 1.5 * (r["ast"] or 0) + (r["reb"] or 0) - (r["tov"] or 0)
    prod_n[p] += 1
    if r["plus_minus"] is not None:
        pm_sum[p] += float(r["plus_minus"]); pm_n[p] += 1
prod = np.where(prod_n > 0, prod_sum / np.maximum(prod_n, 1), 0.0)
pm = np.where(pm_n > 0, (pm_sum / np.maximum(pm_n, 1)) * (pm_n / (pm_n + 12)), 0.0)
pm = np.clip(pm, -10, 10)
sel3 = mins_total >= 500
r_prod = float(spearmanr(V[sel3], prod[sel3])[0])
r_pm = float(spearmanr(V[sel3], pm[sel3])[0])
print(f"(b) rank corr over {int(sel3.sum())} players (>=500 min): V vs production={r_prod:.3f}, V vs plus/minus proxy={r_pm:.3f}")

# ---------------------------------------------------------------- (c) top-3 disagreement (team-seasons)
diff = 0; overlaps = []
for (tid, season), pmap in team_season.items():
    ps = [p for p in pmap if mins_total[p] >= 60]     # drop cameo-only players
    if len(ps) < 5:
        continue
    topV = {p for p in sorted(ps, key=lambda p: -V[p])[:3]}
    topP = {p for p in sorted(ps, key=lambda p: -prod[p])[:3]}
    if topV != topP:
        diff += 1
    overlaps.append(len(topV & topP) / 3.0)
n_ts = len(overlaps)
disagree = diff / n_ts if n_ts else 0.0
print(f"(c) top-3 disagreement over {n_ts} team-seasons: sets differ={diff} ({disagree:.1%}); mean overlap={np.mean(overlaps):.2f}")

# ---------------------------------------------------------------- (d) archetypes
sel4 = mins_total >= 500
idx4 = np.where(sel4)[0]
Xa = np.array([[acc[p][c] / (acc[p]["min"] / 36.0) for c in COLS] for p in idx4])
Xa = StandardScaler().fit_transform(Xa)
km = KMeans(n_clusters=8, random_state=7, n_init=10).fit(Xa)
sil = float(silhouette_score(Xa, km.labels_))
w4 = mins_total[idx4]
vmeans = []
for c in range(8):
    mm = km.labels_ == c
    if mm.sum():
        vmeans.append(float(np.average(V[idx4][mm], weights=w4[mm])))
overall = float(np.average(V[idx4], weights=w4))
spread = float(np.std(vmeans))
print(f"(d) archetypes: n={len(idx4)} players, K=8 silhouette={sil:.3f}")
for c in range(8):
    mm = km.labels_ == c
    if mm.sum():
        print(f"    cluster {c}: n={int(mm.sum()):>3}  meanV={float(np.average(V[idx4][mm], weights=w4[mm])):+.3f}")
print(f"    cluster-mean spread: std={spread:.3f} vs overall |V| spread (weighted sd)="
      f"{float(np.sqrt(np.average((V[idx4]-overall)**2, weights=w4))):.3f}")

# ---------------------------------------------------------------- verdict
proceed = (rel_w >= 0.6) and (disagree >= 0.15)
print(f"\nDECISION: reliability={rel_w:.3f} (need >=0.60); top-3 disagreement={disagree:.1%} (need >=15%)"
      f" -> {'PROCEED' if proceed else 'SHELVE'}")
print(f"({time.time()-t0:.1f}s)")
sys.exit(0)
