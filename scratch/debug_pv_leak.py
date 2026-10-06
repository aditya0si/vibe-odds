"""Debug: which pv keys differ between the full build and the 2013-cut truncated build."""
import json
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sports.nba.db import paths                      # noqa: E402
from sports.nba.features import player_value as PV   # noqa: E402

full_con = sqlite3.connect(str(paths.DB), timeout=120)
full_con.row_factory = sqlite3.Row
full = PV.build_player_value(full_con)
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
trunc = PV.build_player_value(con)
con.close()

mism = [g for g in trunc if json.dumps(full[g], sort_keys=True) != json.dumps(trunc[g], sort_keys=True)]
print("mismatches:", len(mism))

keyc = Counter()
for g in mism:
    for k, v in full[g].items():
        if trunc[g][k] != v:
            keyc[k] += 1
print("differing keys:", dict(keyc))

seasons = Counter()
gseason = {}
for r in sqlite3.connect(str(paths.DB)).execute("SELECT game_id, season FROM games"):
    gseason[r[0]] = r[1]
for g in mism:
    seasons[gseason.get(g, "?")] += 1
print("by season:", dict(sorted(seasons.items())))

for g in mism[:6]:
    print(g, gseason.get(g))
    f, t = full[g], trunc[g]
    for k in f:
        if f[k] != t[k]:
            print(f"   {k}: full={f[k]} trunc={t[k]}")
