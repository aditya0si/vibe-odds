"""Debug v2: compare refit internals (lambda, intercept, V head) full vs 2013-cut build,
and dump the remaining mismatches in detail."""
import json
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sports.nba.db import paths                      # noqa: E402
from sports.nba.features import player_value as PV   # noqa: E402


def run_build(con, tag, log):
    orig_fit, orig_cv = PV.fit_values, PV.time_blocked_cv

    def fit_spy(X, y, lam):
        V, b = orig_fit(X, y, lam)
        log.append((tag, "fit", lam, round(b, 6), [round(float(v), 5) for v in V[:4]]))
        return V, b

    def cv_spy(X, y, grid=PV.LAMBDA_GRID, folds=PV.CV_FOLDS):
        t = orig_cv(X, y, grid, folds)
        log.append((tag, "cv", {str(k): round(v, 4) for k, v in t.items()}))
        return t

    PV.fit_values, PV.time_blocked_cv = fit_spy, cv_spy
    try:
        return PV.build_player_value(con)
    finally:
        PV.fit_values, PV.time_blocked_cv = orig_fit, orig_cv


log: list = []
full_con = sqlite3.connect(str(paths.DB), timeout=120)
full_con.row_factory = sqlite3.Row
full = run_build(full_con, "full", log)
full_con.close()

scratch = ROOT / "scratch" / "nba_cut_dbg.sqlite"
shutil.copyfile(paths.DB, scratch)
con = sqlite3.connect(str(scratch), timeout=120)
con.row_factory = sqlite3.Row
cut = "2013-01-02"
future = [r[0] for r in con.execute("SELECT game_id FROM games WHERE game_date > ?", (cut,))]
con.execute("CREATE TEMP TABLE _f(game_id TEXT PRIMARY KEY)")
con.executemany("INSERT INTO _f VALUES (?)", [(g,) for g in future])
for t in ("game_traditional", "game_advanced", "game_inactives", "game_officials",
          "game_team_stats", "features", "odds_snapshots", "results", "predictions"):
    con.execute(f"DELETE FROM {t} WHERE game_id IN (SELECT game_id FROM _f)")
con.execute("DELETE FROM games WHERE game_id IN (SELECT game_id FROM _f)")
con.commit()
trunc = run_build(con, "trunc", log)
con.close()

fl = [e for e in log if e[0] == "full"]
tl = [e for e in log if e[0] == "trunc"]
print(f"refit calls: full={len(fl)} trunc={len(tl)}")
diffs = 0
for i, (f, t) in enumerate(zip(fl, tl)):
    if f[1:] != t[1:]:
        diffs += 1
        if diffs <= 12:
            print("DIFF at call", i)
            print("  full :", f)
            print("  trunc:", t)
print(f"refit-call diffs: {diffs} / {len(fl)}")

mism = [g for g in trunc if json.dumps(full[g], sort_keys=True) != json.dumps(trunc[g], sort_keys=True)]
print("mismatches:", len(mism))
keyc = Counter()
for g in mism:
    for k, v in full[g].items():
        if trunc[g][k] != v:
            keyc[k] += 1
print("differing keys:", dict(keyc))
gseason = {r[0]: r[1] for r in sqlite3.connect(str(paths.DB)).execute("SELECT game_id, season FROM games")}
print("by season:", dict(sorted(Counter(gseason.get(g, "?") for g in mism).items())))
for g in mism[:8]:
    print(g, gseason.get(g))
    f, t = full[g], trunc[g]
    for k in f:
        if f[k] != t[k]:
            print(f"   {k}: full={f[k]} trunc={t[k]}")
