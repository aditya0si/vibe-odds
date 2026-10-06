"""Feasibility probe: can the LIVE season capture use ESPN's core API (not blocked)?

Checks:
  1. is sports.core.api.espn.com reachable from this network?
  2. does the 2026-27 season list events NOW (pre-season, before the opener)?
  3. for a completed 2025-26 event, does the odds payload carry .open/.close objects?
  4. for an upcoming 2026-27 event (if listed), what does its odds payload look like?
Read-only; writes nothing. No key needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

BASE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
S = requests.Session()


def get(url, timeout=25):
    try:
        r = S.get(url, headers={"User-Agent": UA}, timeout=timeout)
        return r.status_code, (r.json() if r.status_code == 200 else r.text[:200])
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:200]}"


print("1) reachability")
code, _ = get(f"{BASE}/seasons/2027/types/2/events?limit=5&page=1")
print(f"   sports.core.api.espn.com -> {code}")

print("2) 2026-27 (ESPN season 2027) event listing")
code, js = get(f"{BASE}/seasons/2027/types/2/events?limit=100&page=1")
if code == 200 and isinstance(js, dict):
    items = js.get("items") or []
    print(f"   page1 items: {len(items)}  count field: {js.get('count')}")
    refs = [i.get("$ref") for i in items[:3]]
    print("   first refs:", refs)
else:
    print("   listing failed:", js)
    sys.exit(0)

print("3) a listed 2026-27 event: competition + odds shape")
if refs:
    eid = refs[0].rstrip("/").split("/")[-1].split("?")[0]
    code, comp = get(f"{BASE}/events/{eid}/competitions/{eid}")
    print(f"   competition {eid}: {code}")
    if code == 200:
        date = comp.get("date")
        teams = {c.get("homeAway"): (c.get("team") or {}).get("$ref", "").split("/")[-1]
                 for c in comp.get("competitors", [])}
        print(f"   date={date} home={teams.get('home')} away={teams.get('away')} "
              f"neutral={comp.get('neutralSite')}")
    code, odds = get(f"{BASE}/events/{eid}/competitions/{eid}/odds")
    print(f"   odds {eid}: {code}")
    if code == 200:
        items = odds.get("items") or []
        print(f"   provider items: {len(items)}")
        for it in items[:4]:
            prov = (it.get("provider") or {}).get("name")
            h = it.get("homeTeamOdds") or {}
            a = it.get("awayTeamOdds") or {}
            print(f"     - {prov}: homeML={h.get('moneyLine')} open={bool(h.get('open'))} "
                  f"close={bool(h.get('close'))} | awayML={a.get('moneyLine')} "
                  f"open={bool(a.get('open'))} close={bool(a.get('close'))}")

print("4) a completed 2025-26 event (ESPN season 2026): odds shape after the fact")
code, js26 = get(f"{BASE}/seasons/2026/types/2/events?limit=3&page=1")
if code == 200 and isinstance(js26, dict):
    items = js26.get("items") or []
    if items:
        eid = items[0].get("$ref", "").rstrip("/").split("/")[-1].split("?")[0]
        code, odds = get(f"{BASE}/events/{eid}/competitions/{eid}/odds")
        print(f"   event {eid}: odds {code}")
        if code == 200:
            items = odds.get("items") or []
            print(f"   provider items: {len(items)}")
            for it in items[:3]:
                prov = (it.get("provider") or {}).get("name")
                h = it.get("homeTeamOdds") or {}
                a = it.get("awayTeamOdds") or {}
                print(f"     - {prov}: homeML={h.get('moneyLine')} open={json.dumps(h.get('open'))[:60]} "
                      f"close={json.dumps(h.get('close'))[:60]}")
