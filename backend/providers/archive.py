"""Raw odds archive (Astra W1): every live fetch stored whole, with metadata.

Lost timestamps cannot be reconstructed — so the archive is written at
fetch time, inside fetch_odds on every successful live response, plus a
manual CLI for backfills. Files: data/odds_archive/<ts>_<sport>.json with
{meta, events}. Quota guard still applies; archiving never triggers fetches.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ARCH_DIR = Path(__file__).resolve().parents[2] / "data" / "odds_archive"


def store(sport_key: str, events: list, meta: dict | None = None) -> Path | None:
    try:
        ARCH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sport_key)
        p = ARCH_DIR / f"{stamp}_{safe}.json"
        blob = {"meta": {"sport_key": sport_key, "fetched_ts": time.time(),
                         **(meta or {})},
                "n_events": len(events), "events": events}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob))
        import os
        os.replace(tmp, p)
        # prune: keep newest 500 snapshots
        olds = sorted(ARCH_DIR.glob("*.json"))
        for extra in olds[:-500]:
            try:
                extra.unlink()
            except FileNotFoundError:
                pass
        return p
    except Exception as e:
        print(f"[warn] archive store failed: {type(e).__name__}")
        return None


def manifest() -> list[dict]:
    out = []
    try:
        for p in sorted(ARCH_DIR.glob("*.json")):
            try:
                d = json.loads(p.read_text()[:2000])
                out.append({"file": p.name, "meta": d.get("meta", {}),
                            "n_events": d.get("n_events")})
            except Exception:
                continue
    except Exception:
        pass
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="usopen")
    ap.add_argument("--manifest", action="store_true")
    a = ap.parse_args()
    if a.manifest:
        for row in manifest():
            print(row)
    else:
        from dotenv import load_dotenv
        load_dotenv()
        from backend.providers import the_odds_api as prov
        for sk in prov.SPORT_GROUPS[a.group]:
            evs = prov.fetch_odds(sk)
            p = store(sk, evs, {"group": a.group, "status": prov.last_status()})
            print(f"{sk}: {len(evs)} events -> {p}")
