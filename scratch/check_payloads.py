"""Check: are the v3 payloads intact, and what does a v5 payload contain?"""
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build          # noqa: E402
from sports.nba.model import phase2 as P2  # noqa: E402

con = build.init(verbose=False)

n_num = n_none = 0
for r in con.execute("SELECT payload FROM features WHERE feature_version='v3'"):
    if json.loads(r[0]).get("avail_missing_home") is None:
        n_none += 1
    else:
        n_num += 1
print("v3 numeric avail:", n_num, "none:", n_none)

row = json.loads(con.execute(
    "SELECT payload FROM features WHERE feature_version='v5' LIMIT 1").fetchone()[0])
print("v5 has avail key:", "avail_missing_home" in row, "value:", row.get("avail_missing_home"))

b, n_fit, n_tune = P2.tune_brier(con, "v3", P2.A7_FEATURES)
print(f"A7 recompute: brier={b:.5f} n_fit={n_fit} n_tune={n_tune} "
      f"(record: 0.21573 / 19085 / 1230)")
