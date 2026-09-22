"""Raw odds archive mechanics (sport-neutral core, map step 7).

Lost timestamps cannot be reconstructed — so the archive is written at
fetch time, inside fetch_odds on every successful live response, plus a
manual CLI for backfills (tennis: ``backend.providers.archive``). Files:
<arch_dir>/<ts>_<sport>.json with {meta, events}. Quota guard still applies;
archiving never triggers fetches.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ARCH_DIR = Path(__file__).resolve().parents[2] / "data" / "odds_archive"

def store(sport_key: str, events: list, meta: dict | None = None,
          arch_dir: Path | None = None) -> Path | None:
    d = Path(arch_dir) if arch_dir is not None else ARCH_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sport_key)
        p = d / f"{stamp}_{safe}.json"
        blob = {"meta": {"sport_key": sport_key, "fetched_ts": time.time(),
                         **(meta or {})},
                "n_events": len(events), "events": events}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob))
        import os
        os.replace(tmp, p)
        # prune: keep newest 500 snapshots
        olds = sorted(d.glob("*.json"))
        for extra in olds[:-500]:
            try:
                extra.unlink()
            except FileNotFoundError:
                pass
        return p
    except Exception as e:
        print(f"[warn] archive store failed: {type(e).__name__}")
        return None


def manifest(arch_dir: Path | None = None) -> list[dict]:
    d = Path(arch_dir) if arch_dir is not None else ARCH_DIR
    out = []
    try:
        for p in sorted(d.glob("*.json")):
            try:
                dd = json.loads(p.read_text()[:2000])
                out.append({"file": p.name, "meta": dd.get("meta", {}),
                            "n_events": dd.get("n_events")})
            except Exception:
                continue
    except Exception:
        pass
    return out
