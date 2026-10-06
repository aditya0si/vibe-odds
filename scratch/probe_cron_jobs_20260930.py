"""Read-only: print schedule.expr / env presence for the nba crons in jobs.json.

Pairs each job name with its cron expression so a grid regression is visible.
Shape of jobs.json: {"jobs": [ {...}, ... ], "updated_at": ...} (jobs is a LIST).
"""
import json
import os
from pathlib import Path

p = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "cron" / "jobs.json"
d = json.loads(p.read_text(encoding="utf-8"))
jobs = d["jobs"] if isinstance(d, dict) else d
for j in jobs:
    name = j.get("name") or ""
    if "nba" in name.lower():
        sched = j.get("schedule") or {}
        expr = sched.get("expr") if isinstance(sched, dict) else sched
        print(
            f"{name} | expr={expr} | has_env={'env' in j} | enabled={j.get('enabled')} | id={j.get('id')}"
        )
print("total jobs in file:", len(jobs))
