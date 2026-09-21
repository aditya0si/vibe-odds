"""Ingest box scores, PRE-GAME inactive lists and officials (v3 endpoint family).

    python -m sports.nba.ingest.box_scores --season 2012-13 --workers 4
    python -m sports.nba.ingest.box_scores --all --workers 4
    python -m sports.nba.ingest.box_scores --all --limit 25 --debug-keys   # canary

Why v3 only: v2 returns empty sets for 2025-26+ and its advanced/play-by-play
variants return literal `{}` (Phase 0). v3 works for every season 2005-06..2025-26.

Structure learned from the live payload (docs/phase0/verification-notes.md):
  boxscoresummaryv3  -> boxScoreSummary.{gameTimeUTC,arena,attendance,isNeutral,officials[]}
                        and boxScoreSummary.{homeTeam,awayTeam}.inactives[]  <- PRE-GAME state
  boxscoretraditionalv3 -> boxScoreTraditional.{homeTeam,awayTeam}.{teamId,players[],starters[]}
  boxscoreadvancedv3    -> boxScoreAdvanced.{homeTeam,awayTeam}.players[]

Deliberately NOT used: the per-player `comment` field ("DNP - Coach's Decision",
"DND"), which is a post-game fact and therefore a look-ahead trap. Availability
features come from the inactive list, which the league publishes before tip-off.

Raw payloads are not retained for box scores (they would add ~1.5 GB across the
backfill); the DB is the artifact, and failures are logged to
sports/nba/data/season-*-box_scores.failures.jsonl for a targeted retry.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sports.nba.db import build, paths
from sports.nba.ingest import nba_api_client as api

# advanced-v3 field names -> our columns (checked against the live payload; unknown keys stay NULL)
ADV_MAP = {
    "off_rating": ("offensiveRating", "estimatedOffensiveRating"),
    "def_rating": ("defensiveRating", "estimatedDefensiveRating"),
    "net_rating": ("netRating", "estimatedNetRating"),
    "ts_pct": ("trueShootingPercentage", "estimatedTrueShootingPercentage"),
    "usg_pct": ("usagePercentage", "estimatedUsagePercentage"),
    "pace": ("pace", "estimatedPace"),
    "pie": ("PIE", "pie"),
}


def _min_to_float(v) -> float | None:
    if not v or not isinstance(v, str):
        return None
    parts = v.split(":")
    try:
        if len(parts) == 2:
            return round(int(parts[0]) + int(parts[1]) / 60.0, 2)
        if len(parts) == 3:
            return round(int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60.0, 2)
    except (ValueError, TypeError):
        return None
    return None


def _pick(stats: dict, names: tuple[str, ...]):
    for n in names:
        if n in stats and stats[n] is not None:
            return stats[n]
    return None


def fetch_game(game_id: str, debug_keys: bool = False, verbose: bool = False,
               skip_advanced: bool = False, only_advanced: bool = False) -> dict:
    """Network phase only: fetch + parse one game into row tuples.

    skip_advanced: the advanced endpoint is the slowest of the three (~0.77s vs ~0.43s) and
    most of its content is derivable from the traditional box score (TS%, usage, PIE), so the
    first pass can run without it; back-fill later with only_advanced=True.
    """
    from nba_api.stats.endpoints import boxscoresummaryv3, boxscoretraditionalv3, boxscoreadvancedv3

    ts = api.now_iso()
    out: dict = {"game_id": game_id, "errors": [], "traditional": [], "advanced": [],
                 "inactives": [], "officials": [], "game_update": None}

    if not only_advanced:
        sresp, errs = api.call_with_retry(
            lambda: boxscoresummaryv3.BoxScoreSummaryV3(game_id=game_id, timeout=api.CALL_TIMEOUT),
            label=f"summary {game_id}", verbose=verbose)
        out["errors"] += [f"summary: {e}" for e in errs]
        if sresp is not None:
            _parse_summary(sresp, out, ts)

        tresp, errs = api.call_with_retry(
            lambda: boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, timeout=api.CALL_TIMEOUT),
            label=f"traditional {game_id}", verbose=verbose)
        out["errors"] += [f"traditional: {e}" for e in errs]
        if tresp is not None:
            _parse_traditional(tresp, out, ts)

    if skip_advanced:
        return out

    aresp, errs = api.call_with_retry(
        lambda: boxscoreadvancedv3.BoxScoreAdvancedV3(game_id=game_id, timeout=api.CALL_TIMEOUT),
        label=f"advanced {game_id}", verbose=verbose)
    out["errors"] += [f"advanced: {e}" for e in errs]
    if aresp is not None:
        ba = aresp.get_dict().get("boxScoreAdvanced", {}) or {}
        for side in ("homeTeam", "awayTeam"):
            team = ba.get(side) or {}
            tid = team.get("teamId")
            for p in team.get("players") or []:
                st = p.get("statistics") or {}
                if debug_keys:
                    print(f"    [advanced keys {side}, game {game_id}]: {sorted(st.keys())}")
                out["advanced"].append((
                    game_id, tid, p.get("personId"),
                    _pick(st, ADV_MAP["off_rating"]), _pick(st, ADV_MAP["def_rating"]),
                    _pick(st, ADV_MAP["net_rating"]), _pick(st, ADV_MAP["ts_pct"]),
                    _pick(st, ADV_MAP["usg_pct"]), _pick(st, ADV_MAP["pace"]),
                    _pick(st, ADV_MAP["pie"]), ts))
    return out


def _parse_summary(sresp, out: dict, ts: str) -> None:
    bs = sresp.get_dict().get("boxScoreSummary", {}) or {}
    out["game_update"] = {
        "game_id": out["game_id"],
        "home_team_id": bs.get("homeTeamId"), "away_team_id": bs.get("awayTeamId"),
        "tipoff_ts": bs.get("gameTimeUTC") or bs.get("gameEt"),
        "attendance": bs.get("attendance"),
        "arena": (bs.get("arena") or {}).get("arenaName"),
        "is_neutral": 1 if bs.get("isNeutral") else 0,
        "home_score": _pick(((bs.get("homeTeam") or {}).get("statistics") or {}), ("points",)),
        "away_score": _pick(((bs.get("awayTeam") or {}).get("statistics") or {}), ("points",)),
        "asof_ts": ts,
    }
    for o in bs.get("officials") or []:
        out["officials"].append((out["game_id"], o.get("personId"), o.get("firstName"),
                                 o.get("familyName"), (o.get("jerseyNum") or "").strip(), ts))
    for side in ("homeTeam", "awayTeam"):
        team = bs.get(side) or {}
        tid = team.get("teamId")
        for p in team.get("inactives") or []:
            out["inactives"].append((out["game_id"], tid, p.get("personId"), None, ts))


def _parse_traditional(tresp, out: dict, ts: str) -> None:
    game_id = out["game_id"]
    bt = tresp.get_dict().get("boxScoreTraditional", {}) or {}
    for side in ("homeTeam", "awayTeam"):
        team = bt.get(side) or {}
        tid = team.get("teamId")
        starters = set(team.get("starters") or [])
        for p in team.get("players") or []:
            st = p.get("statistics") or {}
            out["traditional"].append((
                game_id, tid, p.get("personId"), 1 if p.get("personId") in starters else 0,
                _min_to_float(st.get("minutes")), st.get("points"), st.get("reboundsTotal"),
                st.get("assists"), st.get("steals"), st.get("blocks"), st.get("turnovers"),
                st.get("foulsPersonal"), st.get("fieldGoalsMade"), st.get("fieldGoalsAttempted"),
                st.get("threePointersMade"), st.get("threePointersAttempted"),
                st.get("freeThrowsMade"), st.get("freeThrowsAttempted"),
                st.get("plusMinusPoints"), ts))


def write_game(con: sqlite3.Connection, g: dict) -> None:
    if g["game_update"]:
        u = g["game_update"]
        con.execute(
            """UPDATE games SET home_team_id=COALESCE(?, home_team_id), away_team_id=COALESCE(?, away_team_id),
                   tipoff_ts=COALESCE(?, tipoff_ts), attendance=COALESCE(?, attendance),
                   arena=COALESCE(?, arena), is_neutral=?, source='v3_boxscore', source_updated_at=?,
                   home_score=COALESCE(?, home_score), away_score=COALESCE(?, away_score)
               WHERE game_id=?""",
            (u["home_team_id"], u["away_team_id"], u["tipoff_ts"], u["attendance"], u["arena"],
             u["is_neutral"], u["asof_ts"], u["home_score"], u["away_score"], u["game_id"]))
    for table, rows, cols in (
        ("game_inactives", g["inactives"],
         "(game_id, team_id, player_id, reason, asof_ts) VALUES (?,?,?,?,?)"),
        ("game_officials", g["officials"],
         "(game_id, official_id, first_name, last_name, jersey_num, asof_ts) VALUES (?,?,?,?,?,?)"),
        ("game_traditional", g["traditional"],
         "(game_id, team_id, player_id, started, minutes, pts, reb, ast, stl, blk, tov, pf, fgm, fga,"
         " fg3m, fg3a, ftm, fta, plus_minus, asof_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
        ("game_advanced", g["advanced"],
         "(game_id, team_id, player_id, off_rating, def_rating, net_rating, ts_pct, usg_pct, pace, pie,"
         " asof_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)"),
    ):
        if rows:
            con.executemany(f"INSERT OR REPLACE INTO {table} {cols}", rows)


def season_complete(con: sqlite3.Connection, season: str) -> bool:
    row = con.execute(
        """SELECT COUNT(*) AS n FROM games g
           WHERE g.season=? AND g.season_type='regular'
             AND EXISTS (SELECT 1 FROM game_traditional t WHERE t.game_id=g.game_id)""",
        (season,)).fetchone()
    total = con.execute(
        "SELECT COUNT(*) AS n FROM games WHERE season=? AND season_type='regular'", (season,)).fetchone()["n"]
    return row["n"] >= total


def ingest(con: sqlite3.Connection, seasons: list[str], workers: int = 4, limit: int | None = None,
           force: bool = False, debug_keys: bool = False, verbose: bool = True,
           skip_advanced: bool = False, only_advanced: bool = False) -> dict:
    table = "game_advanced" if only_advanced else "game_traditional"
    todo: list[str] = []
    for s in seasons:
        if season_complete(con, s) and not force and not only_advanced:
            if verbose:
                print(f"  {s}: already complete (skip)")
            continue
        ids = [r["game_id"] for r in con.execute(
            f"""SELECT g.game_id FROM games g
               WHERE g.season=? AND g.season_type='regular'
                 AND (? OR NOT EXISTS (SELECT 1 FROM {table} t WHERE t.game_id=g.game_id))
               ORDER BY g.game_date""", (s, 1 if force else 0))]
        if limit:
            ids = ids[:limit]
        todo.extend(ids)

    if not todo:
        print("nothing to do")
        return {"games": 0}

    print(f"ingesting {len(todo)} games with {workers} workers")
    t0 = time.time()
    done = 0
    failures: list[dict] = []
    consecutive_summary_fail = 0
    aborted: str | None = None
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(fetch_game, gid, debug_keys, False, skip_advanced, only_advanced): gid
               for gid in todo}
    try:
        for fut in as_completed(futures):
            gid = futures[fut]
            try:
                g = fut.result()
            except Exception as exc:  # noqa: BLE001
                failures.append({"game_id": gid, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if g["errors"]:
                failures.append({"game_id": gid, "error": "; ".join(g["errors"])[:400]})
            write_game(con, g)
            done += 1
            if g["game_update"] is None:
                # no summary payload => no inactives/officials. A burst of these means
                # stats.nba.com is throttling us; better to stop than to grind for hours.
                consecutive_summary_fail += 1
                if consecutive_summary_fail >= 25:
                    aborted = ("25 consecutive games returned no summary payload - stats.nba.com is most "
                               "likely throttling. Progress is committed; re-run later.")
                    break
            else:
                consecutive_summary_fail = 0
            con.commit()          # per game: never hold SQLite's write lock between API calls
            if done % 20 == 0:    # progress line (and a cheap rate/ETA estimate)
                rate = done / max(1e-9, time.time() - t0)
                eta = (len(todo) - done) / max(1e-9, rate)
                print(f"  {done}/{len(todo)} games  ({rate:.2f}/s, eta {eta/60:.1f} min)", flush=True)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    con.commit()
    if aborted:
        raise RuntimeError(aborted)

    for s in seasons:
        if not only_advanced and season_complete(con, s):
            api.mark_complete(con, f"season:{s}:box_scores")
    p = api.log_failures("box_scores", failures)
    print(f"done: {done} games in {(time.time()-t0)/60:.1f} min; failures: {len(failures)}"
          + (f" -> {p.name}" if p else ""))
    return {"games": done, "failures": len(failures)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest NBA box scores, inactives and officials")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--season", action="append", default=[])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="cap games per season (canary)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--debug-keys", action="store_true", help="print advanced-stat keys once")
    ap.add_argument("--skip-advanced", action="store_true",
                    help="first pass without the slow advanced endpoint (its data is derivable)")
    ap.add_argument("--only-advanced", action="store_true",
                    help="back-fill game_advanced for games that lack it")
    args = ap.parse_args(argv)
    seasons = api.SEASONS if args.all else args.season
    if not seasons:
        ap.error("give --all or --season YYYY-YY")
    con = build.init(verbose=False)
    ingest(con, seasons, workers=args.workers, limit=args.limit, force=args.force,
           debug_keys=args.debug_keys, skip_advanced=args.skip_advanced,
           only_advanced=args.only_advanced)
    return 0


if __name__ == "__main__":
    sys.exit(main())
