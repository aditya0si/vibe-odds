"""Debug: why does load_merged_dataset return nothing?"""
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build
from sports.nba.model import formula as F
from sports.nba.model import phase3 as P3

con = build.init(verbose=False)

v3 = {}
for r in con.execute("SELECT payload FROM features WHERE feature_version='v3'"):
    row = json.loads(r["payload"])
    v3[row["game_id"]] = row

v5_rows = []
for r in con.execute("SELECT payload FROM features WHERE feature_version='v5'"):
    v5_rows.append(json.loads(r["payload"]))

print("v3 rows:", len(v3))
print("v5 rows:", len(v5_rows))
print("v3 sample id:", next(iter(v3)))
print("v5 sample id:", v5_rows[0]["game_id"])
print("v5[0] in v3:", v5_rows[0]["game_id"] in v3)

hits = sum(1 for row in v5_rows[:200] if row["game_id"] in v3)
print("first-200 v5 ids present in v3:", hits)

row = v5_rows[0]
base = v3.get(row["game_id"])
if base is not None:
    merged = {**base, **row}
    xs = {name: F._feat(merged, name) for name in P3.A9B_FEATURES}
    none_keys = [k for k, v in xs.items() if v is None]
    print("merged[0] none keys:", none_keys)

import collections

none_counter = collections.Counter()
passed = 0
for row in v5_rows:
    base = v3.get(row["game_id"])
    if base is None:
        continue
    merged = {**base, **row}
    xs = {name: F._feat(merged, name) for name in P3.A9B_FEATURES}
    nk = [k for k, v in xs.items() if v is None]
    if nk:
        for k in nk:
            none_counter[k] += 1
    else:
        passed += 1
print("passed filter:", passed)
print("none-key counts (top):", none_counter.most_common(12))
print("total rows with any None:", sum(1 for _ in [1]) and sum(none_counter.values()))

gid = "0021200001"
b = v3[gid]
print("v3 avail_missing_home:", repr(b.get("avail_missing_home")))
print("v3 has avail key:", "avail_missing_home" in b)
print("v3 sample keys:", sorted(b.keys())[:12])
v5row = next(r for r in v5_rows if r["game_id"] == gid)
print("v5 has avail key:", "avail_missing_home" in v5row)
print("v5 avail value:", repr(v5row.get("avail_missing_home")))
merged = {**b, **v5row}
print("merged avail value:", repr(merged.get("avail_missing_home")))

rows = P3.load_merged_dataset(con, P3.A9B_FEATURES)
print("load_merged_dataset rows:", len(rows))
