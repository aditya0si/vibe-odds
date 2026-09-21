"""Phase 0 source probe: can THIS machine actually reach and read the data we plan to build on?

Usage:  python tools/probes/source_probe.py
Writes: docs/phase0/source-probes.md   (PASS/FAIL table + exact outputs)
        docs/phase0/probe-raw.json     (raw machine-readable results)

Nothing is downloaded beyond a handful of small API responses. Every probe is
bounded by a hard timeout and every failure is recorded, never guessed at.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "phase0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NBA_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
ESPN_HEADERS = {"User-Agent": UA, "Accept": "application/json"}

RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str, **extra) -> None:
    RESULTS.append({"probe": name, "ok": ok, "detail": detail, **extra})
    print(("[PASS] " if ok else "[FAIL] ") + name + " :: " + detail[:300])


def get(url: str, headers: dict | None = None, timeout: int = 25) -> requests.Response:
    return requests.get(url, headers=headers or {"User-Agent": UA}, timeout=timeout)


def probe_http(name: str, url: str, headers: dict | None = None, timeout: int = 25) -> None:
    t0 = time.time()
    try:
        r = get(url, headers, timeout)
        record(name, r.status_code == 200, f"HTTP {r.status_code}, {len(r.content)} bytes",
               ms=int((time.time() - t0) * 1000), url=url)
    except Exception as exc:  # noqa: BLE001 - probe wants every failure mode
        record(name, False, f"{type(exc).__name__}: {exc}", ms=int((time.time() - t0) * 1000), url=url)


# ---------------------------------------------------------------- reachability
def reachability() -> None:
    probe_http("stats.nba.com (raw scoreboardv2)", 
               "https://stats.nba.com/stats/scoreboardv2?GameDate=01/15/2013&LeagueID=00&DayOffset=0",
               NBA_HEADERS, timeout=30)
    probe_http("site.api.espn.com scoreboard",
               "https://site.api.espn.com/apis/site/v2/sports/basketball/leagues/nba/scoreboard?dates=20130115",
               ESPN_HEADERS, timeout=25)
    probe_http("sports.core.api.espn.com odds",
               "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/400489399/competitions/400489399/odds",
               ESPN_HEADERS, timeout=25)
    probe_http("kaggle.com dataset page",
               "https://www.kaggle.com/datasets/cviaxmiwnptr/nba-betting-data-october-2007-to-june-2026", timeout=25)
    probe_http("raw.githubusercontent.com", "https://raw.githubusercontent.com/README.md", timeout=20)
    probe_http("sportsbookreviewsonline archive index",
               "https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm", timeout=25)


# ------------------------------------------------------------------- nba_api
def nba_api_probe(season_dates: list[str]) -> list[str]:
    """Probe nba_api endpoints; return discovered game ids for the payload probe."""
    game_ids: list[str] = []
    try:
        from nba_api.stats.endpoints import scoreboardv2
    except Exception as exc:  # noqa: BLE001
        record("nba_api import", False, f"{type(exc).__name__}: {exc}")
        return game_ids
    record("nba_api import", True, "scoreboardv2 importable")

    for gd in season_dates:
        t0 = time.time()
        try:
            sb = scoreboardv2.ScoreboardV2(game_date=gd, league_id="00", day_offset=0, timeout=40)
            frames = sb.get_data_frames()
            games = frames[0]
            n = int(len(games))
            ids = [str(x) for x in games["GAME_ID"].tolist()] if n else []
            record(f"nba_api scoreboardv2 {gd}", n > 0,
                   f"{n} games, ids[:3]={ids[:3]}", ms=int((time.time() - t0) * 1000))
            if ids:
                game_ids.append(ids[0])
        except Exception as exc:  # noqa: BLE001
            record(f"nba_api scoreboardv2 {gd}", False, f"{type(exc).__name__}: {exc}",
                   ms=int((time.time() - t0) * 1000))
    return game_ids


def payload_probe(game_ids: list[str]) -> None:
    """The availability pillar depends on pre-game inactive lists being present."""
    try:
        from nba_api.stats.endpoints import boxscoresummaryv2
    except Exception as exc:  # noqa: BLE001
        record("nba_api boxscoresummaryv2 import", False, f"{type(exc).__name__}: {exc}")
        return
    for gid in game_ids:
        t0 = time.time()
        try:
            d = boxscoresummaryv2.BoxScoreSummaryV2(game_id=gid, timeout=40)
            sets = d.get_dict().get("resultSets", [])
            names = [s.get("name") for s in sets]
            inactive = next((s for s in sets if s.get("name") == "InactivePlayers"), None)
            officials = next((s for s in sets if s.get("name") == "Officials"), None)
            n_inactive = len(inactive.get("rowSet", [])) if inactive else 0
            n_officials = len(officials.get("rowSet", [])) if officials else 0
            ok = bool(inactive) and n_inactive > 0
            record(f"inactive+officials payload gid={gid}", ok,
                   f"sets={len(names)}, InactivePlayers rows={n_inactive}, Officials rows={n_officials},"
                   f" has_InactivePlayers={'InactivePlayers' in names}",
                   ms=int((time.time() - t0) * 1000), game_id=gid, result_sets=names)
        except Exception as exc:  # noqa: BLE001
            record(f"inactive+officials payload gid={gid}", False, f"{type(exc).__name__}: {exc}",
                   ms=int((time.time() - t0) * 1000), game_id=gid)


# ---------------------------------------------------------------------- ESPN
def espn_probe(dates: list[str]) -> None:
    for d in dates:
        try:
            r = get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/leagues/nba/scoreboard?dates={d}",
                    ESPN_HEADERS, timeout=25)
            if r.status_code != 200:
                record(f"ESPN scoreboard {d}", False, f"HTTP {r.status_code}: {r.text[:120]}")
                continue
            js = r.json()
            events = js.get("events", [])
            if not events:
                record(f"ESPN scoreboard {d}", False, "200 but zero events")
                continue
            ev = events[0]
            comp = ev["competitions"][0]
            has_site_odds = "odds" in comp
            eid = ev["id"]
            detail = f"{len(events)} events, first id={eid}, site 'odds' key={has_site_odds}"
            odds_summary = None
            try:
                o = get(f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{eid}"
                        f"/competitions/{eid}/odds", ESPN_HEADERS, timeout=25)
                if o.status_code == 200:
                    oj = o.json()
                    items = oj.get("items", [])
                    odds_summary = {"count": oj.get("count"), "first_keys": list(items[0].keys()) if items else []}
                    detail += f", core odds count={oj.get('count')}"
                else:
                    detail += f", core odds HTTP {o.status_code}"
            except Exception as exc:  # noqa: BLE001
                detail += f", core odds {type(exc).__name__}: {exc}"
            record(f"ESPN odds {d}", bool(odds_summary and odds_summary.get("count")), detail,
                   espn_event_id=eid, core_odds=odds_summary)
        except Exception as exc:  # noqa: BLE001
            record(f"ESPN scoreboard {d}", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    print("=== reachability ===")
    reachability()
    print("=== nba_api ===")
    game_ids = nba_api_probe(["01/15/2013", "12/15/2024"])
    print("=== game-level payloads ===")
    payload_probe(game_ids)
    print("=== ESPN ===")
    espn_probe(["20130115", "20241215"])

    (DOCS / "probe-raw.json").write_text(
        json.dumps({"generated_by": "tools/probes/source_probe.py", "results": RESULTS}, indent=1),
        encoding="utf-8")

    rows = ["# Phase 0 source probes", "",
            "Generated by `python tools/probes/source_probe.py` — raw results in `probe-raw.json`.", "",
            "| probe | result | detail | ms |", "|---|---|---|---|"]
    for r in RESULTS:
        rows.append(f"| {r['probe']} | {'PASS' if r['ok'] else 'FAIL'} | {r['detail'][:200]} | {r.get('ms','')} |")
    passed = sum(1 for r in RESULTS if r["ok"])
    rows += ["", f"**{passed}/{len(RESULTS)} probes passed.**", ""]
    (DOCS / "source-probes.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(RESULTS)} probes passed -> docs/phase0/source-probes.md")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
