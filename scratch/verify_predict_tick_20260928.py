"""Pre/sealed-arm verification for the 2026-09-28 predict tick (off-season).

Proves: arm digest matches the sealed artifact, live log state, and whether any
2026-27 features exist (the predict tick's other zero shape).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json  # noqa: E402

from tools.season_gate import ARM_PATH, arm_digest  # noqa: E402
from tools.phase2_read import verify_arm  # noqa: E402
from sports.nba import live_log  # noqa: E402

arm = json.loads(ARM_PATH.read_text(encoding="utf-8"))
verify_arm(arm)  # raises on digest mismatch
print("arm file:", ARM_PATH.name)
print("arm digest:", arm.get("digest"))
print("digest_verified:", arm_digest(arm) == arm.get("digest"))
print("arm keys:", sorted(arm.keys()))

con = sqlite3.connect("sports/nba/data/nba.sqlite")
con.row_factory = sqlite3.Row
n_feat = con.execute(
    "SELECT COUNT(*) n FROM features WHERE feature_version='v3'").fetchone()["n"]
n_feat_2627 = con.execute(
    """SELECT COUNT(*) n FROM features f JOIN games g ON g.game_id=f.game_id
       WHERE f.feature_version='v3' AND g.season='2026-27'""").fetchone()["n"]
n_games_2627 = con.execute(
    "SELECT COUNT(*) n FROM games WHERE season='2026-27'").fetchone()["n"]
print(f"features v3 rows: total={n_feat} 2026-27={n_feat_2627} (season games={n_games_2627})")

print("live_log path:", live_log.LOG_PATH)
print("live_log exists:", live_log.LOG_PATH.exists())
if live_log.LOG_PATH.exists():
    print("live_log n_events:", len(live_log.events()))
