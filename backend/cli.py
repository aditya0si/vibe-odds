"""CLI: python -m backend.cli events|compare --group usopen (tennis only)"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

load_dotenv()

from core.odds import best_per_outcome, ev_per_unit, no_vig_probs
from backend.providers import the_odds_api as prov


def cmd_events(group: str):
    evs = prov.fetch_group(group, mock=not os.getenv("ODDS_API_KEY"))
    for e in evs[:20]:
        print(f"{e.get('id')} | {e.get('away_team')} @ {e.get('home_team')} | {len(e.get('bookmakers', []))} books")


def cmd_compare(group: str, idx: int = 0, market: str = "h2h"):
    evs = prov.fetch_group(group, mock=not os.getenv("ODDS_API_KEY"))
    if not evs:
        print("no events")
        return
    ev = evs[min(idx, len(evs) - 1)]
    books = prov.to_books_map(ev, market)
    print(f"{ev.get('away_team')} @ {ev.get('home_team')} [{market}]")
    for book, outs in books.items():
        print(f"  {book}: {outs}")
    best = best_per_outcome(books)
    print("BEST:", best)
    outcomes = sorted({o for b in books.values() for o in b})
    pin = books.get("Pinnacle")
    if pin and all(o in pin for o in outcomes):
        fair = dict(zip(outcomes, no_vig_probs([pin[o] for o in outcomes])))
        for o in outcomes:
            print(f"  {o}: fair {fair[o]:.3f} best {best[o]['odds']}@{best[o]['book']} EV {ev_per_unit(fair[o], best[o]['odds'])*100:.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["events", "compare"])
    ap.add_argument("--group", default="usopen")
    ap.add_argument("--idx", type=int, default=0)
    ap.add_argument("--market", default="h2h")
    a = ap.parse_args()
    if a.cmd == "events":
        cmd_events(a.group)
    else:
        cmd_compare(a.group, a.idx, a.market)


if __name__ == "__main__":
    main()
