"""Daily 2026-27 season ops (plan Task 8 live-ops loop).

    python tools/season_ops.py schedule [--now ISO] [--out games.json]
    python tools/season_ops.py verify                      # sealed-arm digest check
    python tools/season_ops.py refresh                     # rebuild v3 features (post-game)

schedule : emit the JSON list of {game_id, tipoff_ts, home_team, away_team} for
           games near NOW (tip-off in [now-1h, now+6h]) - the input
           tools/t60_snapshot.py tick consumes. Window-keyed, not date-keyed:
           tip-offs run past midnight UTC, so a UTC calendar date drops the
           late games of every slate.
verify   : digest-verify the sealed arm (nba_phase2_arm_2026_27.json) - the ops
           sanity check that the claim arm on disk is the one that was sealed.
refresh  : re-run the v3 feature build so the latest completed games are as-of
           available (same build, no leakage change).

NO LIVE PREDICTION LOGGING (deviation, docs/preregistration.md 9): the sealed
A7 arm takes 10 of its 17 features (avail_*, impact_*) from each game's OWN
inactive list, which the free pipeline learns only from the post-game box score
(see sports/nba/features/availability.py). A pre-tip prediction from the sealed
arm is therefore impossible by construction, so there is no `predict`
subcommand: the prospective claim read (tools/phase2_read.py) is scored from
settled as-of features plus the live-captured market prices, and stands alone.

Nothing is bought. The sealed arm is never refit here.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):                   # script mode (cron / manual): repo importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build, paths
from tools.season_gate import ARM_PATH          # noqa: E402
from tools.phase2_read import verify_arm        # noqa: E402  (digest guard)

FEATURE_VERSION = "v3"          # the A7 arm input (IMPACT_VERSION)
SEASON = "2026-27"
TEAM_NAMES: dict[int, str] | None = None


def _team_names(con) -> dict[int, str]:
    global TEAM_NAMES
    if TEAM_NAMES is None:
        TEAM_NAMES = {r["team_id"]: r["full_name"]
                      for r in con.execute("SELECT team_id, full_name FROM teams")}
    return TEAM_NAMES


def _ts(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def games_near(con, now, season: str = SEASON, back_hours: float = 1.0,
               ahead_hours: float = 6.0) -> list[dict]:
    """Regular-season games with tip-off in [now-back_hours, now+ahead_hours]."""
    now = _ts(now)
    lo = (now - timedelta(hours=back_hours)).isoformat()
    hi = (now + timedelta(hours=ahead_hours)).isoformat()
    names = _team_names(con)
    out = []
    for r in con.execute(
            """SELECT game_id, tipoff_ts, home_team_id, away_team_id
                 FROM games WHERE season=? AND season_type='regular'
                   AND tipoff_ts IS NOT NULL AND tipoff_ts >= ? AND tipoff_ts <= ?
                 ORDER BY tipoff_ts""", (season, lo, hi)):
        out.append({"game_id": r["game_id"], "tipoff_ts": r["tipoff_ts"],
                    "home_team": names.get(r["home_team_id"], str(r["home_team_id"])),
                    "away_team": names.get(r["away_team_id"], str(r["away_team_id"]))})
    return out


def cmd_schedule(con, now, out_path: str | None, season: str = SEASON,
                 back_hours: float = 1.0, ahead_hours: float = 6.0) -> dict:
    games = games_near(con, now, season, back_hours, ahead_hours)
    payload = json.dumps(games, indent=1)
    if out_path:
        Path(out_path).write_text(payload, encoding="utf-8")
    return {"now": _ts(now).isoformat(), "window_hours": [back_hours, ahead_hours],
            "n_games": len(games), "games": games, "written_to": out_path}


def cmd_verify(arm: dict) -> dict:
    verify_arm(arm)
    return {"arm": ARM_PATH.name, "digest": arm.get("digest"),
            "arm_name": arm.get("arm"), "fit_through": arm.get("fit_through"),
            "n_fit": arm.get("n_fit"), "verified": True}


def cmd_refresh(con, verbose: bool = True) -> dict:
    """Rebuild v3 features so completed games are as-of available."""
    from sports.nba.features import build as FB
    rows, evidence = FB.build_rows(con, version=FEATURE_VERSION, verbose=verbose)
    n = FB.write_rows(con, rows)
    return {"feature_version": FEATURE_VERSION, "rows_written": n,
            "games_covered": len(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="2026-27 daily season ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schedule", help="emit games.json for the T-60 capture")
    s.add_argument("--now", default=None, help="ISO reference time (default: now, UTC)")
    s.add_argument("--season", default=SEASON)
    s.add_argument("--back-hours", type=float, default=1.0)
    s.add_argument("--ahead-hours", type=float, default=6.0)
    s.add_argument("--out", default=None, help="write games.json here (else stdout only)")
    v = sub.add_parser("verify", help="digest-verify the sealed arm")
    r = sub.add_parser("refresh", help="rebuild v3 features after games complete")
    r.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    con = build.init(verbose=False)
    if a.cmd == "schedule":
        out = cmd_schedule(con, a.now or datetime.now(timezone.utc).isoformat(),
                           a.out, a.season, a.back_hours, a.ahead_hours)
    elif a.cmd == "verify":
        out = cmd_verify(json.loads(ARM_PATH.read_text(encoding="utf-8")))
    else:
        out = cmd_refresh(con, verbose=not a.quiet)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
