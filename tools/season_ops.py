"""Daily 2026-27 season ops (plan Task 8 live-ops loop).

One tool, three subcommands, all cron-able and all read-only unless they write:

  python tools/season_ops.py schedule [--date 2026-10-27]   # games.json for T-60
  python tools/season_ops.py predict  [--date 2026-10-27]   # log sealed-arm T-60 preds
  python tools/season_ops.py refresh                        # rebuild v3 features (post-game)

schedule : emit the JSON list of {game_id, tipoff_ts, home_team, away_team} for
           today's games - the input tools/t60_snapshot.py --schedule consumes.
predict  : score today's games with the SEALED arm (nba_phase2_arm_2026_27.json,
           digest-verified) and log each prediction BEFORE tip-off via
           sports/nba/live_log.log_prediction (LatePrediction rejected after tip).
refresh  : re-run the v3 feature build so the latest completed games are as-of
           available for tomorrow's predictions (same build, no leakage change).

Nothing is bought. Prediction logging uses the frozen arm only - never a refit.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):                   # script mode (cron / manual): repo importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build, paths
from sports.nba.model import formula as F
from sports.nba.model.phase2 import A7_FEATURES
from sports.nba import live_log
from tools.season_gate import ARM_PATH          # noqa: E402
from tools.phase2_read import verify_arm        # noqa: E402  (digest guard)

FEATURE_VERSION = "v3"          # the A7 arm input (IMPACT_VERSION)
TEAM_NAMES: dict[int, str] | None = None


def _team_names(con) -> dict[int, str]:
    global TEAM_NAMES
    if TEAM_NAMES is None:
        TEAM_NAMES = {r["team_id"]: r["full_name"]
                      for r in con.execute("SELECT team_id, full_name FROM teams")}
    return TEAM_NAMES


def today_games(con, date: str | None = None) -> list[dict]:
    """Today's regular-season games with tip-off + full team names (for T-60)."""
    date = date or datetime.now(timezone.utc).date().isoformat()
    names = _team_names(con)
    out = []
    for r in con.execute(
            """SELECT game_id, tipoff_ts, home_team_id, away_team_id
                 FROM games WHERE season_type='regular' AND game_date=? ORDER BY tipoff_ts""",
            (date,)):
        out.append({"game_id": r["game_id"], "tipoff_ts": r["tipoff_ts"],
                    "home_team": names.get(r["home_team_id"], str(r["home_team_id"])),
                    "away_team": names.get(r["away_team_id"], str(r["away_team_id"]))})
    return out


def cmd_schedule(con, date: str | None, out_path: str | None) -> dict:
    games = today_games(con, date)
    payload = json.dumps(games, indent=1)
    if out_path:
        Path(out_path).write_text(payload, encoding="utf-8")
    return {"date": date or datetime.now(timezone.utc).date().isoformat(),
            "n_games": len(games), "games": games, "written_to": out_path}


def _feature_index(con) -> dict[str, dict]:
    return {r["game_id"]: json.loads(r["payload"])
            for r in con.execute("SELECT game_id, payload FROM features WHERE feature_version=?",
                                 (FEATURE_VERSION,))}


def cmd_predict(con, date: str | None, arm: dict, dry_run: bool = False) -> dict:
    """Log a sealed-arm prediction for every game with features, before tip-off."""
    verify_arm(arm)
    feats = _feature_index(con)
    logged, skipped = [], []
    for g in today_games(con, date):
        row = feats.get(g["game_id"])
        if row is None:
            skipped.append({"game_id": g["game_id"], "reason": "no v3 features yet"})
            continue
        p = F.predict(arm["formula"], row)
        if dry_run:
            logged.append({"game_id": g["game_id"], "prob_home": round(p, 4), "dry_run": True})
            continue
        rec = live_log.log_prediction(g["game_id"], g["tipoff_ts"], p,
                                      source="sealed_a7_2026_27")
        logged.append({"game_id": rec["game_id"], "prob_home": rec["prob_home"],
                       "logged_at": rec["logged_at"]})
    return {"date": date or datetime.now(timezone.utc).date().isoformat(),
            "logged": logged, "skipped": skipped, "n_logged": len(logged)}


def cmd_refresh(con, verbose: bool = True) -> dict:
    """Rebuild v3 features so completed games feed tomorrow's predictions."""
    from sports.nba.features import build as FB
    rows, evidence = FB.build_rows(con, version=FEATURE_VERSION, verbose=verbose)
    n = FB.write_rows(con, rows)
    return {"feature_version": FEATURE_VERSION, "rows_written": n,
            "games_covered": len(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="2026-27 daily season ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schedule", help="emit games.json for T-60 capture")
    s.add_argument("--date", default=None)
    s.add_argument("--out", default=None, help="write games.json here (else stdout only)")
    p = sub.add_parser("predict", help="log sealed-arm T-60 predictions")
    p.add_argument("--date", default=None)
    p.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("refresh", help="rebuild v3 features after games complete")
    r.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    con = build.init(verbose=False)
    if a.cmd == "schedule":
        out = cmd_schedule(con, a.date, a.out)
    elif a.cmd == "predict":
        arm = json.loads(ARM_PATH.read_text(encoding="utf-8"))
        out = cmd_predict(con, a.date, arm, dry_run=a.dry_run)
    else:
        out = cmd_refresh(con, verbose=not a.quiet)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
