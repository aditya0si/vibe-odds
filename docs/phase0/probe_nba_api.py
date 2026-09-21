"""Phase 0 probe: can nba_api (stats.nba.com) build an NBA game DB for 2005-06 -> 2025-26?

Run phases separately so progress survives a timeout:
    python docs/phase0/probe_nba_api.py season-log      # (1) LeagueGameFinder per season
    python docs/phase0/probe_nba_api.py era-boxscores   # (2) summary v2 vs v3 per era
    python docs/phase0/probe_nba_api.py modern-v3       # (3) v3 endpoints on a 2025-26 game
    python docs/phase0/probe_nba_api.py throttle        # (4) 10 sequential per-game calls, timed

Writes/updates: docs/phase0/probe-nba-api-raw.json  (raw machine-readable evidence)
No pandas required: every payload is parsed from the raw nba_api dict.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "phase0"
RAW = DOCS / "probe-nba-api-raw.json"

SEASONS = ["2005-06", "2008-09", "2012-13", "2016-17",
           "2019-20", "2020-21", "2023-24", "2025-26"]
ERA_SEASONS = ["2005-06", "2012-13", "2018-19"]


def load_raw() -> dict:
    if RAW.exists():
        return json.loads(RAW.read_text(encoding="utf-8"))
    return {"probes": []}


def save_raw(data: dict) -> None:
    RAW.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def record(data: dict, phase: str, name: str, ok: bool, detail: str, **extra) -> None:
    row = {"phase": phase, "probe": name, "ok": bool(ok), "detail": detail, **extra}
    data["probes"].append(row)
    save_raw(data)
    print(("[PASS] " if ok else "[FAIL] ") + name + " :: " + detail[:400], flush=True)


def sets_summary(payload: dict, sample_rows: int = 2) -> list[dict]:
    out = []
    for s in payload.get("resultSets", []) or []:
        rows = s.get("rowSet", []) or []
        out.append({
            "name": s.get("name"),
            "headers": s.get("headers"),
            "n_rows": len(rows),
            "sample": rows[:sample_rows],
        })
    return out


def call(fn, **kw):
    """Run an nba_api endpoint call, capturing warnings, exceptions and wall time."""
    t0 = time.time()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            obj = fn(**kw)
            return obj, (time.time() - t0), [str(x.message) for x in w], None
        except Exception as exc:  # noqa: BLE001 - probe wants every failure mode
            return None, (time.time() - t0), [str(x.message) for x in w], \
                f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------- (1) season game logs
def phase_season_log(data: dict) -> None:
    from nba_api.stats.endpoints import leaguegamefinder
    for season in SEASONS:
        for attempt in (1, 2):
            obj, dt, warns, err = call(
                leaguegamefinder.LeagueGameFinder,
                season_nullable=season, league_id_nullable="00",
                player_or_team_abbreviation="T", timeout=40)
            if err is None:
                break
            if attempt == 1:
                time.sleep(3)  # one retry: distinguishes transient from permanent
        if obj is None:
            record(data, "1-season-log", f"LeagueGameFinder {season}", False, err,
                   seconds=round(dt, 2), attempts=2, warnings=warns)
            continue
        payload = obj.get_dict()
        sets = sets_summary(payload, sample_rows=1)
        rs = sets[0] if sets else {"headers": [], "n_rows": 0, "sample": []}
        hdr = rs["headers"] or []
        rows = rs["n_rows"]
        idx = {h: i for i, h in enumerate(hdr)}
        gids, seasons_ids, dates, types = [], {}, set(), {}
        for r in payload["resultSets"][0]["rowSet"]:
            gid = str(r[idx["GAME_ID"]])
            gids.append(gid)
            sid = str(r[idx["SEASON_ID"]]) if "SEASON_ID" in idx else "?"
            seasons_ids[sid] = seasons_ids.get(sid, 0) + 1
            if "GAME_DATE" in idx:
                dates.add(str(r[idx["GAME_DATE"]])[:10])
            # game-id prefix: 001 pre, 002 regular, 003 all-star, 004 playoffs, 005 play-in
            types[gid[:3]] = types.get(gid[:3], 0) + 1
        uniq = len(set(gids))
        record(data, "1-season-log", f"LeagueGameFinder {season}", uniq > 0,
               f"{rows} rows, {uniq} unique GAME_IDs, season_id_counts={seasons_ids}, "
               f"game_id_prefix_counts={types}, first_date={min(dates) if dates else None}, "
               f"last_date={max(dates) if dates else None}",
               seconds=round(dt, 2), warnings=warns, attempts=attempt)
        # store a light evidence trail (headers + counts + 1 sample row), not the payload
        rs0 = payload["resultSets"][0]
        data.setdefault("season_log", {})[season] = {
            "endpoint": "leaguegamefinder",
            "n_rows": rows, "n_unique_game_ids": uniq,
            "headers": hdr, "sample_row": rs0["rowSet"][:1],
            "season_id_counts": seasons_ids, "game_id_prefix_counts": types,
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None, "seconds": round(dt, 2),
            "warnings": warns,
        }
        # keep game ids we need later
        data.setdefault("game_ids", {})
        reg = [g for g in gids if g.startswith("002")]
        data["game_ids"][season] = {
            "first_regular": reg[0] if reg else (gids[0] if gids else None),
            "last_regular": reg[-1] if reg else None,
            "regular_count_rows": len(reg),
        }
        save_raw(data)


# ------------------------------------------------- (2) inactives/officials era
V3_ATTRS = ["game_summary", "game_info", "arena_info", "officials", "line_score",
            "inactive_players", "last_five_meetings", "other_stats", "available_video",
            "player_stats", "team_stats", "team_starter_bench_stats"]


def describe(obj) -> dict:
    """Normalise an nba_api endpoint object to {set_name: {headers, n_rows, sample}}.

    v2 endpoints put rows under response['resultSets'][i]['rowSet'].
    v3 endpoints have NO resultSets: nba_api's v3 parsers expose named datasets
    ('InactivePlayers', 'Officials', 'PlayByPlay', ...) reachable through
    obj.nba_response.get_data_sets(endpoint) -> {name: {'headers','data'}}.
    Calling get_dict()['resultSets'] on a v3 endpoint silently yields nothing.
    """
    raw = obj.get_dict()
    if "resultSets" in raw or "resultSet" in raw:
        sets = raw.get("resultSets") or [raw["resultSet"]]
        if isinstance(sets, dict):
            sets = [sets]
        return {s["name"]: {"headers": s.get("headers"),
                            "n_rows": len(s.get("rowSet") or []),
                            "sample": (s.get("rowSet") or [])[:1]}
                for s in sets}
    try:
        got = obj.nba_response.get_data_sets(obj.endpoint)
        if got:
            return {name: {"headers": d.get("headers"),
                           "n_rows": len(d.get("data") or []),
                           "sample": (d.get("data") or [])[:1]}
                    for name, d in got.items()}
    except Exception:  # noqa: BLE001 - fall through to the attribute scan
        pass
    out = {}
    for attr in V3_ATTRS:
        ds = getattr(obj, attr, None)
        if ds is None or not hasattr(ds, "get_dict"):
            continue
        d = ds.get_dict()
        if isinstance(d, dict) and ("data" in d or "headers" in d):
            rows = d.get("data") or []
            out[attr] = {"headers": d.get("headers"), "n_rows": len(rows),
                         "sample": rows[:1]}
    if not out:  # last resort: whatever datasets were attached
        for i, ds in enumerate(getattr(obj, "data_sets", []) or []):
            d = ds.get_dict()
            out[f"data_set_{i}"] = {"headers": d.get("headers"),
                                    "n_rows": len(d.get("data") or []),
                                    "sample": (d.get("data") or [])[:1]}
    return out


def pick(sets: dict, *candidates: str) -> dict | None:
    """Find a result set by case-insensitive substring (v2 and v3 names differ)."""
    for c in candidates:
        for name, val in sets.items():
            if c in str(name).lower():
                return val
    return None


def summarise_sets(sets: dict, sample_rows: int = 2) -> dict:
    return {k: {"n_rows": v["n_rows"], "sample": v["sample"][:sample_rows]}
            for k, v in sets.items()}


def probe_boxsummary(data: dict, tag: str, mod, cls, version: str, gid: str):
    obj, dt, warns, err = call(getattr(mod, cls), game_id=gid, timeout=40)
    if obj is None:
        record(data, "2-era-boxscores", f"{version} gid={gid}", False, err,
               seconds=round(dt, 2), game_id=gid, warnings=warns)
        return None
    sets = describe(obj)
    names = list(sets)
    rows_by_name = {k: v["n_rows"] for k, v in sets.items()}

    def rows_for(candidates: list[str]) -> int:
        for c in candidates:
            for name in names:
                if c in name.lower():
                    return rows_by_name[name]
        return 0

    def set_for(candidates: list[str]):
        for c in candidates:
            for name in names:
                if c in name.lower():
                    return sets[name]
        return None

    n_inactive = rows_for(["inactiveplayers", "inactive"])
    n_officials = rows_for(["officials"])
    ok = bool(names) and n_inactive > 0
    record(data, "2-era-boxscores", f"{version} gid={gid}", ok,
           f"sets={len(names)} {names} | inactive={n_inactive} officials={n_officials}",
           seconds=round(dt, 2), game_id=gid, warnings=warns,
           result_sets=rows_by_name)
    inactive = set_for(["inactiveplayers", "inactive"])
    officials = set_for(["officials"])
    return {"endpoint": version, "game_id": gid, "ok": bool(names),
            "n_result_sets": len(names), "result_sets": rows_by_name,
            "inactive_rows": n_inactive, "officials_rows": n_officials,
            "seconds": round(dt, 2), "warnings": warns,
            "inactive_headers": inactive["headers"] if inactive else None,
            "inactive_sample": summarise_sets({"x": inactive})["x"]["sample"]
            if inactive else None,
            "officials_headers": officials["headers"] if officials else None,
            "officials_sample": summarise_sets({"x": officials})["x"]["sample"]
            if officials else None}


def phase_era_boxscores(data: dict) -> None:
    from nba_api.stats.endpoints import (boxscoresummaryv2,
                                         boxscoresummaryv3, leaguegamefinder)
    for season in ERA_SEASONS:
        gid = (data.get("game_ids", {}).get(season) or {}).get("first_regular")
        if not gid:
            # leaguegamefinder may not have been run yet: run it for this season only
            obj, dt, warns, err = call(
                leaguegamefinder.LeagueGameFinder, season_nullable=season,
                league_id_nullable="00", player_or_team_abbreviation="T", timeout=40)
            if obj is None:
                record(data, "2-era-boxscores", f"game-id lookup {season}", False, err)
                continue
            rows = obj.get_dict()["resultSets"][0]["rowSet"]
            hdr = obj.get_dict()["resultSets"][0]["headers"]
            gi = hdr.index("GAME_ID")
            gid = next((str(r[gi]) for r in rows if str(r[gi]).startswith("002")), None)
            if gid is None:
                record(data, "2-era-boxscores", f"game-id lookup {season}", False,
                       "no 002 (regular season) game id returned")
                continue
        data.setdefault("era_boxscores", {})[season] = {"game_id": gid, "probes": []}
        print(f"--- era {season} game_id={gid}", flush=True)
        for mod, cls, ver in ((boxscoresummaryv3, "BoxScoreSummaryV3", "boxscoresummaryv3"),
                              (boxscoresummaryv2, "BoxScoreSummaryV2", "boxscoresummaryv2")):
            res = probe_boxsummary(data, season, mod, cls, ver, gid)
            if res:
                data["era_boxscores"][season]["probes"].append(res)
        save_raw(data)


# ---------------------------------------------------- (3) modern v3 endpoints
def phase_modern_v3(data: dict) -> None:
    from nba_api.stats.endpoints import (boxscoreadvancedv3,
                                         boxscoretraditionalv3)
    from nba_api.stats.endpoints import boxscoresummaryv3
    gids = (data.get("game_ids", {}).get("2025-26") or {})
    gid = gids.get("first_regular") or gids.get("last_regular")
    if not gid:
        from nba_api.stats.endpoints import leaguegamefinder
        obj, dt, warns, err = call(leaguegamefinder.LeagueGameFinder,
                                   season_nullable="2025-26", league_id_nullable="00",
                                   player_or_team_abbreviation="T", timeout=40)
        if obj is None:
            record(data, "3-modern-v3", "2025-26 game-id lookup", False, err)
            return
        rs = obj.get_dict()["resultSets"][0]
        gi = rs["headers"].index("GAME_ID")
        gid = next((str(r[gi]) for r in rs["rowSet"] if str(r[gi]).startswith("002")), None)
        if not gid:
            record(data, "3-modern-v3", "2025-26 game-id lookup", False, "no 002 game id")
            return
    print(f"--- modern v3 on 2025-26 game_id={gid}", flush=True)
    data["modern_v3"] = {"game_id": gid, "probes": []}
    jobs = [
        ("boxscoresummaryv3", boxscoresummaryv3, "BoxScoreSummaryV3"),
        ("boxscoretraditionalv3", boxscoretraditionalv3, "BoxScoreTraditionalV3"),
        ("boxscoreadvancedv3", boxscoreadvancedv3, "BoxScoreAdvancedV3"),
    ]
    for ver, mod, cls in jobs:
        obj, dt, warns, err = call(getattr(mod, cls), game_id=gid, timeout=40)
        if obj is None:
            record(data, "3-modern-v3", f"{ver} gid={gid}", False, err,
                   seconds=round(dt, 2), game_id=gid, warnings=warns)
            data["modern_v3"]["probes"].append(
                {"endpoint": ver, "ok": False, "error": err, "seconds": round(dt, 2)})
            continue
        payload = obj.get_dict()
        sets = describe(obj)
        names = list(sets)
        totals = {k: v["n_rows"] for k, v in sets.items()}
        record(data, "3-modern-v3", f"{ver} gid={gid}", bool(names),
               f"sets={names} rows={totals}", seconds=round(dt, 2), game_id=gid,
               warnings=warns)
        data["modern_v3"]["probes"].append({
            "endpoint": ver, "ok": bool(names), "result_sets": totals,
            "headers": {k: v["headers"] for k, v in sets.items()},
            "top_level_response_keys": list(payload.keys()),
            "sample_rows": {k: v["sample"] for k, v in sets.items()},
            "seconds": round(dt, 2), "warnings": warns})
        save_raw(data)


# ----------------------------------------------------------- (4) throttling
def phase_throttle(data: dict) -> None:
    """Two 10-call sequential runs in the shape of the nightly ingest."""
    from nba_api.stats.endpoints import (boxscoreadvancedv3,
                                         boxscoresummaryv3,
                                         boxscoretraditionalv3)
    from nba_api.stats.endpoints import leaguegamefinder
    obj, dt, warns, err = call(leaguegamefinder.LeagueGameFinder,
                               season_nullable="2025-26", league_id_nullable="00",
                               player_or_team_abbreviation="T", timeout=40)
    if obj is None:
        record(data, "4-throttle", "2025-26 game list", False, err)
        return
    rs = obj.get_dict()["resultSets"][0]
    gi = rs["headers"].index("GAME_ID")
    gids = sorted({str(r[gi]) for r in rs["rowSet"] if str(r[gi]).startswith("002")})

    blocks = {}
    # block A: 10 x boxscoresummaryv3 (the availability/inactives call)
    runs = [("A-mixed-summary", [(boxscoresummaryv3.BoxScoreSummaryV3, g)
                                 for g in gids[:10]])]
    # block B: 10 x box score v3 (traditional/advanced alternating)
    runs.append(("B-boxscore-v3", [(boxscoretraditionalv3.BoxScoreTraditionalV3, g)
                                   if i % 2 == 0 else
                                   (boxscoreadvancedv3.BoxScoreAdvancedV3, g)
                                   for i, g in enumerate(gids[10:20])]))
    for block, jobs in runs:
        timings, failures, http_429 = [], 0, 0
        for i, (fn, gid) in enumerate(jobs, 1):
            o, dt2, w2, e2 = call(fn, game_id=gid, timeout=45)
            if o is None:
                failures += 1
                if "429" in str(e2) or "Too Many" in str(e2):
                    http_429 += 1
                sets = {}
            else:
                sets = describe(o)
            timings.append({"i": i, "game_id": gid, "seconds": round(dt2, 2),
                            "status": "ok" if o is not None else "fail",
                            "n_result_sets": len(sets), "error": e2})
            print(f"  [{block} {i}/10] {gid} {round(dt2,2)}s "
                  f"{'ok' if o is not None else 'fail'} sets={len(sets)}", flush=True)
        vals = [t["seconds"] for t in timings]
        blocks[block] = {
            "endpoint_mix": "BoxScoreSummaryV3 x10" if block.startswith("A")
            else "BoxScoreTraditionalV3 / BoxScoreAdvancedV3 alternating x10",
            "n_calls": len(timings), "n_failures": failures, "http_429": http_429,
            "timings": timings, "min_s": min(vals),
            "median_s": sorted(vals)[len(vals) // 2], "mean_s": round(sum(vals) / len(vals), 2),
            "max_s": max(vals), "total_s": round(sum(vals), 2),
            "sets_seen": [t["n_result_sets"] for t in timings]}
        record(data, "4-throttle", f"{block}: 10 sequential calls", failures == 0,
               f"min={blocks[block]['min_s']}s median={blocks[block]['median_s']}s "
               f"mean={blocks[block]['mean_s']}s max={blocks[block]['max_s']}s "
               f"total={blocks[block]['total_s']}s failures={failures} http429={http_429} "
               f"result_sets_per_call={blocks[block]['sets_seen']}")
        save_raw(data)
    data["throttle"] = blocks
    save_raw(data)


# ------------------------------- (6) inactive-list population rate by season
def phase_inactive_sample(data: dict) -> None:
    """10 games per season: is the pre-game inactive list actually populated?
    Answers 'can player-availability features be built across the whole history'."""
    from nba_api.stats.endpoints import boxscoresummaryv3, leaguegamefinder
    sample = {}
    for season in SEASONS:
        obj, dt, w, e = call(leaguegamefinder.LeagueGameFinder,
                             season_nullable=season, league_id_nullable="00",
                             player_or_team_abbreviation="T", timeout=40)
        if obj is None:
            record(data, "6-inactive-sample", f"game ids {season}", False, e)
            continue
        rs = obj.get_dict()["resultSets"][0]
        h = rs["headers"]
        gi, si = h.index("GAME_ID"), h.index("SEASON_ID")
        reg = sorted({str(r[gi]) for r in rs["rowSet"] if str(r[si]) == "2" + season[:4]})
        if not reg:
            continue
        step = max(1, len(reg) // 10)
        picks = reg[::step][:10]
        per = {"n_games_sampled": len(picks), "games": [], "inactive_rows": [],
               "officials_rows": [], "failures": [], "seconds": []}
        for gid in picks:
            o, dt2, w2, e2 = call(boxscoresummaryv3.BoxScoreSummaryV3,
                                  game_id=gid, timeout=45)
            per["seconds"].append(round(dt2, 2))
            if o is None:
                per["failures"].append({"game_id": gid, "error": e2})
                continue
            sets = describe(o)
            inact = pick(sets, "inactiveplayers", "inactive")
            off = pick(sets, "officials")
            per["games"].append({"game_id": gid,
                                 "inactive_rows": inact["n_rows"] if inact else 0,
                                 "officials_rows": off["n_rows"] if off else 0})
            per["inactive_rows"].append(inact["n_rows"] if inact else 0)
            per["officials_rows"].append(off["n_rows"] if off else 0)
        n = len(per["games"])
        per["summary"] = {
            "n_ok": n, "n_failed": len(per["failures"]),
            "games_with_inactives": sum(1 for x in per["inactive_rows"] if x > 0),
            "games_with_3_officials": sum(1 for x in per["officials_rows"] if x == 3),
            "inactive_min": min(per["inactive_rows"], default=None),
            "inactive_max": max(per["inactive_rows"], default=None),
            "inactive_empty_game_ids": [g["game_id"] for g in per["games"]
                                        if g["inactive_rows"] == 0],
        }
        sample[season] = per
        record(data, "6-inactive-sample", f"v3 inactives {season}", n > 0,
               f"{per['summary']}", seconds=round(sum(per['seconds']), 2))
        save_raw(data)
    data["inactive_sample"] = sample
    save_raw(data)


# ------------------------------- (7) the 2012-13 LeagueGameFinder gap game
def phase_gap_2012_13(data: dict) -> None:
    """0021201214 is in leaguegamelog but not in leaguegamefinder - what is it?"""
    from nba_api.stats.endpoints import (boxscoresummaryv3,
                                         leaguegamefinder, leaguegamelog)
    gid = "0021201214"
    out = {"game_id": gid}
    o, dt, w, e = call(boxscoresummaryv3.BoxScoreSummaryV3, game_id=gid, timeout=40)
    if o is None:
        out["summary_v3"] = {"ok": False, "error": e}
        raw = None
        try:
            from nba_api.stats.library.http import NBAStatsHTTP
            resp = NBAStatsHTTP().send_api_request(endpoint="boxscoresummaryv3",
                                                   parameters={"GameID": gid}, timeout=40)
            raw = resp.get_response()
            out["summary_v3"]["raw_body"] = raw[:400]
            out["summary_v3"]["valid_json"] = bool(resp.valid_json())
        except Exception as exc2:  # noqa: BLE001
            out["summary_v3"]["raw_body"] = f"<raw fetch failed: {type(exc2).__name__}: {exc2}>"
        record(data, "7-gap", f"boxscoresummaryv3 {gid}", False,
               f"{e} | raw={(raw or '')[:200]!r}", seconds=round(dt, 2))
    else:
        sets = describe(o)
        gs = pick(sets, "gamesummary")
        rows = gs["sample"] if gs else None
        gi = gs["headers"].index("gameId") if gs and "gameId" in gs["headers"] else None
        out["summary_v3"] = {"ok": True, "sets": {k: v["n_rows"] for k, v in sets.items()},
                            "game_summary_headers": gs["headers"] if gs else None,
                            "game_summary_row": rows, "seconds": round(dt, 2)}
        record(data, "7-gap", f"boxscoresummaryv3 {gid}", True,
               f"sets={ {k: v['n_rows'] for k, v in sets.items()} } "
               f"row={str(rows)[:200]}", seconds=round(dt, 2))
    # does leaguegamelog carry it as a real (played) game?
    o2, dt2, w2, e2 = call(leaguegamelog.LeagueGameLog, season="2012-13",
                           season_type_all_star="Regular Season", league_id="00", timeout=40)
    if o2 is None:
        out["gamelog"] = {"ok": False, "error": e2}
    else:
        s2 = o2.get_dict()["resultSets"][0]
        h2 = s2["headers"]
        gi2 = h2.index("GAME_ID")
        rows2 = [dict(zip(h2, r)) for r in s2["rowSet"] if str(r[gi2]) == gid]
        keep = ("GAME_ID", "GAME_DATE", "MATCHUP", "WL", "PTS", "MIN", "TEAM_ABBREVIATION")
        out["gamelog"] = {"ok": True, "n_rows": len(rows2),
                          "rows": [{k: r.get(k) for k in keep} for r in rows2],
                          "seconds": round(dt2, 2)}
        record(data, "7-gap", f"leaguegamelog {gid}", True,
               f"rows={len(rows2)} {out['gamelog']['rows']}", seconds=round(dt2, 2))
    # and does leaguegamefinder return it when asked for that game id directly?
    o3, dt3, w3, e3 = call(leaguegamefinder.LeagueGameFinder, league_id_nullable="00",
                           game_id_nullable=gid, player_or_team_abbreviation="T", timeout=40)
    if o3 is None:
        out["gamefinder_by_id"] = {"ok": False, "error": e3}
        record(data, "7-gap", f"leaguegamefinder game_id={gid}", False, e3)
    else:
        rs3 = o3.get_dict()["resultSets"][0]
        h3 = rs3["headers"]
        k3 = ("GAME_ID", "GAME_DATE", "MATCHUP", "WL", "PTS", "SEASON_ID")
        all3 = [{k: dict(zip(h3, r)).get(k) for k in k3} for r in rs3["rowSet"]]
        from collections import Counter
        out["gamefinder_by_id"] = {
            "ok": True, "n_rows": len(all3),
            "first_row": all3[0] if all3 else None,
            "last_row": all3[-1] if all3 else None,
            "date_range": [min(x["GAME_DATE"] for x in all3),
                           max(x["GAME_DATE"] for x in all3)] if all3 else None,
            "season_id_counts": dict(sorted(Counter(x["SEASON_ID"] for x in all3).items())),
            "seconds": round(dt3, 2)}
        record(data, "7-gap", f"leaguegamefinder game_id={gid} (ignored filter?)", True,
               f"rows={len(all3)} (looks like the unimplemented GameID filter + a 30k rows cap), "
               f"date_range={out['gamefinder_by_id']['date_range']}, "
               f"first={out['gamefinder_by_id']['first_row']}, "
               f"last={out['gamefinder_by_id']['last_row']}", seconds=round(dt3, 2))
    data["gap_2012_13"] = out
    save_raw(data)


# ------------------------------------------------- (5) row-count integrity
def phase_integrity(data: dict) -> None:
    """Every regular-season GAME_ID should appear exactly twice (home + away).
    Also cross-checks the row count against a second endpoint, leaguegamelog."""
    from collections import Counter
    from nba_api.stats.endpoints import leaguegamefinder, leaguegamelog
    for season in ["2005-06", "2012-13", "2019-20", "2025-26"]:
        obj, dt, warns, err = call(leaguegamefinder.LeagueGameFinder,
                                   season_nullable=season, league_id_nullable="00",
                                   player_or_team_abbreviation="T", timeout=40)
        if obj is None:
            record(data, "5-integrity", f"LeagueGameFinder {season}", False, err)
            continue
        rs = obj.get_dict()["resultSets"][0]
        h = rs["headers"]
        gi, si = h.index("GAME_ID"), h.index("SEASON_ID")
        reg = [r for r in rs["rowSet"] if str(r[si]) == "2" + season.replace("-", "")[:4]]
        counts = Counter(str(r[gi]) for r in reg)
        odd = {g: c for g, c in counts.items() if c != 2}
        detail = (f"{len(reg)} regular-season rows, {len(counts)} distinct games, "
                  f"games_without_exactly_2_rows={len(odd)}")
        if odd:
            first = sorted(odd.items())[:3]
            samples = []
            for g, c in first:
                teams = [str(r[h.index("TEAM_ABBREVIATION")]) for r in reg if str(r[gi]) == g]
                samples.append({"game_id": g, "rows": c, "teams": teams})
            detail += f" e.g. {samples}"
        record(data, "5-integrity", f"LeagueGameFinder {season}", not odd, detail,
               seconds=round(dt, 2), odd_games=odd)
        # second endpoint for the same season -> agrees?
        o2, dt2, w2, e2 = call(leaguegamelog.LeagueGameLog, season=season,
                               season_type_all_star="Regular Season",
                               league_id="00", timeout=40)
        if o2 is None:
            record(data, "5-integrity", f"LeagueGameLog {season} (alt endpoint)", False, e2,
                   seconds=round(dt2, 2), warnings=w2)
        else:
            s2 = o2.get_dict()["resultSets"][0]
            h2, rows2 = s2["headers"], s2["rowSet"]
            g2 = h2.index("GAME_ID")
            ids2 = {str(r[g2]) for r in rows2}
            same = ids2 == set(counts)
            record(data, "5-integrity", f"LeagueGameLog {season} (alt endpoint)", True,
                   f"{len(rows2)} rows, {len(ids2)} distinct GAME_IDs, "
                   f"same_id_set_as_leaguegamefinder={same}",
                   seconds=round(dt2, 2), warnings=w2,
                   only_in_gamelog=sorted(ids2 - set(counts))[:5],
                   only_in_gamefinder=sorted(set(counts) - ids2)[:5])
        save_raw(data)


# ------------------------------------- (2b) v3 availability sweep, all seasons
def phase_era_sweep(data: dict) -> None:
    """boxscoresummaryv3 on 3 games per season: does the inactive list exist all the
    way back to 2005-06, or only for modern seasons?"""
    from nba_api.stats.endpoints import boxscoresummaryv3, leaguegamefinder
    sweep = {}
    for season in SEASONS:
        obj, dt, warns, err = call(leaguegamefinder.LeagueGameFinder,
                                   season_nullable=season, league_id_nullable="00",
                                   player_or_team_abbreviation="T", timeout=40)
        if obj is None:
            record(data, "2b-era-sweep", f"game ids {season}", False, err)
            continue
        rs = obj.get_dict()["resultSets"][0]
        h = rs["headers"]
        gi, si = h.index("GAME_ID"), h.index("SEASON_ID")
        reg = sorted({str(r[gi]) for r in rs["rowSet"] if str(r[si]) == "2" + season[:4]})
        if not reg:
            record(data, "2b-era-sweep", f"game ids {season}", False, "no regular ids")
            continue
        picks = {"first": reg[0], "middle": reg[len(reg) // 2], "last": reg[-1]}
        sweep[season] = {"n_regular_games": len(reg), "picks": {}}
        for label, gid in picks.items():
            o, dt2, w2, e2 = call(boxscoresummaryv3.BoxScoreSummaryV3,
                                  game_id=gid, timeout=40)
            if o is None:
                sweep[season]["picks"][label] = {"game_id": gid, "ok": False, "error": e2}
                record(data, "2b-era-sweep", f"v3 {season} {label} gid={gid}", False, e2)
                continue
            sets = describe(o)
            rows = {k: v["n_rows"] for k, v in sets.items()}
            inact = pick(sets, "inactiveplayers", "inactive")
            off = pick(sets, "officials")
            inactive = inact["n_rows"] if inact else 0
            officials = off["n_rows"] if off else 0
            ok = inactive > 0 and officials > 0
            sweep[season]["picks"][label] = {
                "game_id": gid, "ok": ok, "inactive_rows": inactive,
                "officials_rows": officials, "rows": rows, "seconds": round(dt2, 2),
                "warnings": w2}
            record(data, "2b-era-sweep",
                   f"v3 {season} {label} gid={gid}", ok,
                   f"inactive={inactive} officials={officials} sets={len(rows)}",
                   seconds=round(dt2, 2), warnings=w2)
        save_raw(data)
    data["era_sweep"] = sweep
    save_raw(data)


# ------------------------------------- (3b) v2 family vs a 2025-26 game
def phase_modern_v2(data: dict) -> None:
    """The deprecation question: do the v2 endpoints still serve 2025-26 games?"""
    import nba_api.stats.endpoints as ep
    gid = (data.get("modern_v3") or {}).get("game_id") or \
        (data.get("game_ids", {}).get("2025-26") or {}).get("first_regular")
    if not gid:
        record(data, "3b-modern-v2", "2025-26 game id", False, "run modern-v3 first")
        return
    jobs = [("boxscoretraditionalv2", "BoxScoreTraditionalV2"),
            ("boxscoreadvancedv2", "BoxScoreAdvancedV2"),
            ("boxscoresummaryv2", "BoxScoreSummaryV2"),
            ("playbyplayv2", "PlayByPlayV2")]
    out = []
    for modname, clsname in jobs:
        mod = getattr(ep, modname)
        o, dt, w, e = call(getattr(mod, clsname), game_id=gid, timeout=40)
        if o is None:
            out.append({"endpoint": modname, "ok": False, "error": e,
                        "seconds": round(dt, 2), "warnings": w})
            record(data, "3b-modern-v2", f"{modname} gid={gid}", False, e,
                   seconds=round(dt, 2), warnings=w)
            continue
        sets = describe(o)
        rows = {k: v["n_rows"] for k, v in sets.items()}
        o2 = o.get_dict().get("resultSets") or o.get_dict().get("resultSet")
        out.append({"endpoint": modname, "ok": bool(rows), "rows": rows,
                    "n_result_sets": len(rows), "seconds": round(dt, 2), "warnings": w})
        record(data, "3b-modern-v2", f"{modname} gid={gid}", bool(rows),
               f"sets={len(rows)} rows={rows}", seconds=round(dt, 2), warnings=w)
        del o2
        save_raw(data)
    data["modern_v2"] = {"game_id": gid, "probes": out}
    save_raw(data)


# ---------------------------------- (3c) raw HTTP layer: what does the API say?
def phase_raw_http(data: dict) -> None:
    """Call stats.nba.com through nba_api's HTTP layer to capture the *raw* body.

    nba_api's v2 classes raise opaque errors (e.g. KeyError: 'resultSet') when the
    service returns an envelope without that key; this records what actually arrived.
    """
    from nba_api.stats.library.http import NBAStatsHTTP
    gid26 = (data.get("modern_v3") or {}).get("game_id", "0022501191")
    gid13 = next((p["game_id"] for s in ["2012-13"]
                  for p in (data.get("era_boxscores", {}).get(s, {}).get("probes") or [])
                  if p["endpoint"] == "boxscoresummaryv2"), "0021201218")
    cases = [
        ("boxscoreadvancedv2", {"GameID": gid26, "StartPeriod": "0", "EndPeriod": "10",
                                "StartRange": "0", "EndRange": "0", "RangeType": "0"},
         "2025-26"),
        ("playbyplayv2", {"GameID": gid26, "StartPeriod": "0", "EndPeriod": "10"},
         "2025-26"),
        ("boxscoretraditionalv2", {"GameID": gid26, "StartPeriod": "0", "EndPeriod": "10",
                                   "StartRange": "0", "EndRange": "0", "RangeType": "0"},
         "2025-26"),
        ("boxscoresummaryv2", {"GameID": gid26}, "2025-26"),
        ("boxscoreadvancedv2", {"GameID": gid13, "StartPeriod": "0", "EndPeriod": "10",
                                "StartRange": "0", "EndRange": "0", "RangeType": "0"},
         "2012-13 control"),
        ("scoreboardv2", {"GameDate": "01/15/2013", "LeagueID": "00", "DayOffset": "0"},
         "the known JSONDecodeError case"),
    ]
    out = []
    for endpoint, params, note in cases:
        t0 = time.time()
        try:
            resp = NBAStatsHTTP().send_api_request(endpoint=endpoint, parameters=params,
                                                   timeout=40)
            body = resp.get_response()
            valid = resp.valid_json()
            keys = list(resp.get_dict().keys()) if valid else None
            out.append({"endpoint": endpoint, "note": note, "http_ok": True,
                        "valid_json": bool(valid), "body_len": len(body),
                        "top_keys": keys, "body_head": body[:300],
                        "seconds": round(time.time() - t0, 2)})
            record(data, "3c-raw-http", f"{endpoint} ({note})", bool(valid),
                   f"valid_json={valid} len={len(body)} keys={keys} head={body[:160]!r}",
                   seconds=round(time.time() - t0, 2))
        except Exception as exc:  # noqa: BLE001
            out.append({"endpoint": endpoint, "note": note, "http_ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "seconds": round(time.time() - t0, 2)})
            record(data, "3c-raw-http", f"{endpoint} ({note})", False,
                   f"{type(exc).__name__}: {exc}", seconds=round(time.time() - t0, 2))
        save_raw(data)
    data["raw_http"] = out
    save_raw(data)


# --------------------------- (3d) where exactly does the v2 family stop?
def phase_v2_boundary(data: dict) -> None:
    """v2 vs v3 inactive/officials rows across the 2025-04-10 warning boundary."""
    from nba_api.stats.endpoints import (boxscoresummaryv2,
                                         boxscoresummaryv3, leaguegamefinder)
    rows_out = []
    for season in ["2024-25", "2025-26"]:
        obj, dt, w, e = call(leaguegamefinder.LeagueGameFinder,
                             season_nullable=season, league_id_nullable="00",
                             player_or_team_abbreviation="T", timeout=40)
        if obj is None:
            record(data, "3d-v2-boundary", f"game ids {season}", False, e)
            continue
        rs = obj.get_dict()["resultSets"][0]
        h = rs["headers"]
        gi, si, di = h.index("GAME_ID"), h.index("SEASON_ID"), h.index("GAME_DATE")
        games = {}
        for r in rs["rowSet"]:
            if str(r[si]) != "2" + season[:4]:
                continue
            gid, date = str(r[gi]), str(r[di])[:10]
            games.setdefault(gid, date)
        ordered = sorted(games.items(), key=lambda kv: kv[1])
        if season == "2024-25":
            picks = [x for x in ordered if x[1] < "2025-04-01"][:2] + \
                    [x for x in ordered if x[1] >= "2025-04-10"][:3]
        else:
            picks = ordered[:1] + ordered[len(ordered) // 2:len(ordered) // 2 + 1] + ordered[-1:]
        for gid, date in picks:
            rec = {"season": season, "game_id": gid, "date": date}
            for ver, mod, cls in (("v2", boxscoresummaryv2, "BoxScoreSummaryV2"),
                                  ("v3", boxscoresummaryv3, "BoxScoreSummaryV3")):
                o, dt2, w2, e2 = call(getattr(mod, cls), game_id=gid, timeout=40)
                if o is None:
                    rec[ver] = {"ok": False, "error": e2, "warning": w2}
                else:
                    sets = describe(o)
                    rws = {k: v["n_rows"] for k, v in sets.items()}
                    inact = pick(sets, "inactiveplayers", "inactive")
                    off = pick(sets, "officials")
                    rec[ver] = {"ok": True,
                                "inactive_rows": inact["n_rows"] if inact else 0,
                                "officials_rows": off["n_rows"] if off else 0,
                                "sets": rws, "warning": w2}
            rows_out.append(rec)
            record(data, "3d-v2-boundary",
                   f"{season} {date} gid={gid}", True,
                   f"v2 inactive={rec['v2'].get('inactive_rows')} officials={rec['v2'].get('officials_rows')}"
                   f" | v3 inactive={rec['v3'].get('inactive_rows')} officials={rec['v3'].get('officials_rows')}")
            save_raw(data)
    data["v2_v3_boundary"] = rows_out
    save_raw(data)


# ------------------------- (3e) scoreboard v2/v3 raw + extra v2/v3 controls
def phase_scoreboard_raw(data: dict) -> None:
    """The known scoreboardv2 failure (empty body, 0 bytes) - deterministic or flaky?
    And does scoreboardv3 serve the same historical date?"""
    from nba_api.stats.library.http import NBAStatsHTTP
    cases = []
    for date in ["01/15/2013", "01/15/2013", "12/15/2024", "12/15/2024",
                 "01/14/2013", "01/16/2013", "11/02/2005"]:
        for ep in ["scoreboardv2", "scoreboardv3"]:
            cases.append((ep, {"GameDate": date, "LeagueID": "00", "DayOffset": "0"}, date))
    out = []
    for endpoint, params, date in cases:
        t0 = time.time()
        try:
            resp = NBAStatsHTTP().send_api_request(endpoint=endpoint, parameters=params,
                                                   timeout=40)
            body = resp.get_response()
            valid = resp.valid_json()
            n_sets = len(resp.get_dict().get("resultSets", [])) if valid else 0
            head = body[:200]
            out.append({"endpoint": endpoint, "date": date, "valid_json": bool(valid),
                        "body_len": len(body), "n_result_sets": n_sets,
                        "body_head": head, "seconds": round(time.time() - t0, 2)})
            record(data, "3e-scoreboard-raw", f"{endpoint} {date}", bool(valid) and len(body) > 0,
                   f"len={len(body)} valid_json={valid} sets={n_sets} head={head[:120]!r}",
                   seconds=round(time.time() - t0, 2))
        except Exception as exc:  # noqa: BLE001
            out.append({"endpoint": endpoint, "date": date, "error": f"{type(exc).__name__}: {exc}",
                        "seconds": round(time.time() - t0, 2)})
            record(data, "3e-scoreboard-raw", f"{endpoint} {date}", False,
                   f"{type(exc).__name__}: {exc}", seconds=round(time.time() - t0, 2))
        save_raw(data)
    data["scoreboard_raw"] = out
    save_raw(data)


def phase_v3_extra(data: dict) -> None:
    """Controls: is boxscoreadvancedv2 dead for ALL games or only 2025-26?
    And does playbyplayv3 cover the whole history (2005-06 / 2025-26)?"""
    import nba_api.stats.endpoints as ep
    gid26 = (data.get("modern_v3") or {}).get("game_id", "0022501191")
    gid13 = (data.get("era_boxscores", {}).get("2012-13") or {}).get("game_id")
    gid05 = (data.get("era_boxscores", {}).get("2005-06") or {}).get("game_id")
    cases = [
        ("boxscoreadvancedv2", "BoxScoreAdvancedV2", gid13, {}, "2012-13 defaults"),
        ("boxscoreadvancedv2", "BoxScoreAdvancedV2", gid26, {}, "2025-26 defaults"),
        ("boxscoretraditionalv2", "BoxScoreTraditionalV2", gid13, {}, "2012-13 defaults"),
        ("playbyplayv3", "PlayByPlayV3", gid05, {}, "2005-06"),
        ("playbyplayv3", "PlayByPlayV3", gid26, {}, "2025-26"),
    ]
    out = []
    for modname, clsname, gid, extra, note in cases:
        mod = getattr(ep, modname)
        o, dt, w, e = call(getattr(mod, clsname), game_id=gid, timeout=40, **extra)
        if o is None:
            raw = None
            try:
                from nba_api.stats.library.http import NBAStatsHTTP
                raw = NBAStatsHTTP().send_api_request(
                    endpoint=modname, parameters={"GameID": gid}, timeout=40).get_response()
            except Exception as exc2:  # noqa: BLE001
                raw = f"<raw fetch failed: {type(exc2).__name__}: {exc2}>"
            out.append({"endpoint": modname, "note": note, "game_id": gid,
                        "ok": False, "error": e, "raw_body_head": (raw or "")[:200],
                        "seconds": round(dt, 2), "warnings": w})
            record(data, "3f-v3-extra", f"{modname} {note} gid={gid}", False,
                   f"{e} | raw={ (raw or '')[:120]!r}", seconds=round(dt, 2), warnings=w)
            save_raw(data)
            continue
        sets = describe(o)
        rws = {k: v["n_rows"] for k, v in sets.items()}
        out.append({"endpoint": modname, "note": note, "game_id": gid, "ok": bool(rws),
                    "rows": rws, "seconds": round(dt, 2), "warnings": w})
        record(data, "3f-v3-extra", f"{modname} {note} gid={gid}", bool(rws),
               f"rows={rws} sets={len(rws)}", seconds=round(dt, 2), warnings=w)
        save_raw(data)
    data["v3_extra"] = out
    save_raw(data)


# ------------------------------- (8) does a multi-season query hit a 30k row cap?
def phase_cap_check(data: dict) -> None:
    """One unfiltered multi-season call: how many rows come back, and where does it stop?"""
    from nba_api.stats.endpoints import leaguegamefinder
    o, dt, w, e = call(leaguegamefinder.LeagueGameFinder, league_id_nullable="00",
                       date_from_nullable="10/01/2005", date_to_nullable="06/30/2026",
                       player_or_team_abbreviation="T", timeout=60)
    if o is None:
        record(data, "8-cap", "multi-season LeagueGameFinder", False, e)
        return
    rs = o.get_dict()["resultSets"][0]
    h, rows = rs["headers"], rs["rowSet"]
    gi, di = h.index("GAME_ID"), h.index("GAME_DATE")
    dates = sorted(str(r[di])[:10] for r in rows)
    out = {"n_rows": len(rows), "first_date": dates[0] if dates else None,
           "last_date": dates[-1] if dates else None,
           "distinct_games": len({str(r[gi]) for r in rows}),
           "seconds": round(dt, 2),
           "note": "rowSet ordering in the payload (first/last row as delivered)"}
    out["delivered_first"] = {"GAME_ID": str(rows[0][gi]), "GAME_DATE": str(rows[0][di])[:10]}
    out["delivered_last"] = {"GAME_ID": str(rows[-1][gi]), "GAME_DATE": str(rows[-1][di])[:10]}
    data["cap_check"] = out
    record(data, "8-cap", "multi-season LeagueGameFinder", len(rows) > 0,
           f"{len(rows)} rows (cap suspect), dates {out['first_date']} -> {out['last_date']}, "
           f"delivered {out['delivered_first']} -> {out['delivered_last']}",
           seconds=round(dt, 2), warnings=w)
    save_raw(data)


def phase_v3_old(data: dict) -> None:
    """If the recommendation is 'v3 for all seasons', v3 box scores must work on the
    oldest games too - not just 2025-26."""
    import nba_api.stats.endpoints as ep
    from nba_api.stats.endpoints import leaguegamefinder
    jobs = [("2005-06", "0020501226"), ("2012-13", "0021201218"),
            ("2016-17", None), ("2019-20", None)]
    out = []
    for season, gid in jobs:
        if gid is None:
            o, dt, w, e = call(leaguegamefinder.LeagueGameFinder, season_nullable=season,
                               league_id_nullable="00", player_or_team_abbreviation="T",
                               timeout=40)
            if o is None:
                record(data, "9-v3-old", f"ids {season}", False, e)
                continue
            rs = o.get_dict()["resultSets"][0]
            h = rs["headers"]
            gi, si = h.index("GAME_ID"), h.index("SEASON_ID")
            reg = sorted({str(r[gi]) for r in rs["rowSet"]
                          if str(r[si]) == "2" + season[:4]})
            gid = reg[len(reg) // 2]
        for modname, clsname in (("boxscoretraditionalv3", "BoxScoreTraditionalV3"),
                                 ("boxscoreadvancedv3", "BoxScoreAdvancedV3")):
            mod = getattr(ep, modname)
            o, dt, w, e = call(getattr(mod, clsname), game_id=gid, timeout=40)
            if o is None:
                out.append({"endpoint": modname, "season": season, "game_id": gid,
                            "ok": False, "error": e})
                record(data, "9-v3-old", f"{modname} {season} gid={gid}", False, e)
                continue
            rows = {k: v["n_rows"] for k, v in describe(o).items()}
            out.append({"endpoint": modname, "season": season, "game_id": gid,
                        "ok": bool(rows), "rows": rows, "seconds": round(dt, 2),
                        "warnings": w})
            record(data, "9-v3-old", f"{modname} {season} gid={gid}", bool(rows),
                   f"rows={rows}", seconds=round(dt, 2), warnings=w)
            save_raw(data)
    data["v3_old"] = out
    save_raw(data)


PHASES = {"season-log": phase_season_log, "era-boxscores": phase_era_boxscores,
          "era-sweep": phase_era_sweep, "modern-v3": phase_modern_v3,
          "modern-v2": phase_modern_v2,
          "raw-http": phase_raw_http, "v2-boundary": phase_v2_boundary,
          "scoreboard-raw": phase_scoreboard_raw, "v3-extra": phase_v3_extra,
          "throttle": phase_throttle, "integrity": phase_integrity,
          "inactive-sample": phase_inactive_sample, "gap": phase_gap_2012_13,
          "cap": phase_cap_check, "v3-old": phase_v3_old}


def main(argv: list[str]) -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    data = load_raw()
    data["generated_by"] = "docs/phase0/probe_nba_api.py"
    data["run_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    names = argv or ["season-log", "era-boxscores", "modern-v3", "throttle"]
    for name in names:
        fn = PHASES[name]
        print(f"=== phase {name} ===", flush=True)
        try:
            fn(data)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            record(data, name, f"phase {name} crashed", False, traceback.format_exc()[-400:])
    save_raw(data)
    print("\nraw -> " + str(RAW))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
