"""Reporting core (sport-neutral, map step 13).

Ledger mechanics (append-with-cap JSON lists, newest-first reads) and report
reducers shared by the upset autopsy and the ablation sweep. Verbatim from
backend/model/{upsets,ablate}.py (map R4: serialization via core.io keeps the
written bytes identical).
"""

from __future__ import annotations

import json
from pathlib import Path


def log_json_list(path, item, cap: int = 200) -> None:
    """Append one report to a JSON list, keeping the newest ``cap`` entries."""
    from core.io import atomic_write_json
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = []
    data.append(item)
    atomic_write_json(path, data[-cap:], backup=False)


def recent_json(path, n: int = 20) -> list:
    """Newest-first slice of a JSON list (empty when missing/unreadable)."""
    try:
        return json.loads(Path(path).read_text())[-n:][::-1]
    except Exception:
        return []


def upset_patterns(reports: list) -> dict:
    """Which signals are most often on the wrong side of upsets?"""
    wrong_count: dict[str, int] = {}
    surf_count: dict[str, int] = {}
    for u in reports:
        surf_count[u.get("surface", "?")] = surf_count.get(u.get("surface", "?"), 0) + 1
        for w in u.get("wrong", []):
            wrong_count[w["signal"]] = wrong_count.get(w["signal"], 0) + 1
    return {"n_upsets": len(reports), "by_surface": surf_count,
            "wrong_counts": dict(sorted(wrong_count.items(), key=lambda x: -x[1]))}


def ablation_rows(acc: dict, bri: dict) -> tuple[list[dict], float]:
    """delta_brier rows vs the "full" variant, worst first. Returns (rows, base)."""
    base = bri["full"][0] / bri["full"][1]
    rows = []
    for v in bri:
        b = bri[v][0] / bri[v][1]
        rows.append({"variant": v, "n": bri[v][1],
                     "acc": round(acc[v][0] / acc[v][1], 4),
                     "brier": round(b, 4),
                     "delta_brier": round(b - base, 5)})
    rows.sort(key=lambda r: -r["delta_brier"])
    return rows, base
