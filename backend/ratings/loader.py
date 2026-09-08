"""Load TML/Sackmann-schema ATP CSVs into normalized chronological match rows."""

from __future__ import annotations

import csv
from pathlib import Path

RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "tennis_atp"

LEVEL_MULT = {
    "G": 1.10,   # Grand Slam
    "F": 1.00,   # Tour Finals
    "M": 1.00,   # Masters 1000
    "A": 0.95,   # ATP 500/250 tour
    "500": 0.95, "250": 0.90,
    "D": 0.80,   # Davis Cup
    "O": 0.85,   # Olympics / other
}

# NEVER sort rounds alphabetically ('F' < 'R128'!): finals would replay
# before first rounds and leak future results into as-of features.
ROUND_ORDER = {"R128": 0, "R64": 1, "R32": 2, "R16": 3, "RR": 2,
               "QF": 4, "SF": 5, "BR": 5, "F": 6}


def _fnum(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (ValueError, TypeError):
        return None


def normalize(row: dict) -> dict | None:
    from backend.ratings.names import canonical

    score = (row.get("score") or "").strip()
    if not score:
        return None
    walkover = score == "W/O"
    retirement = "RET" in score
    try:
        date = int(row["tourney_date"])
    except (ValueError, TypeError, KeyError):
        return None
    surface = (row.get("surface") or "Hard").strip().lower()
    if surface not in ("hard", "clay", "grass", "carpet"):
        surface = "hard"
    try:
        best_of = int(row.get("best_of") or 3)
    except ValueError:
        best_of = 3
    level = (row.get("tourney_level") or "A").strip()
    return {
        "date": date,
        "surface": surface,
        "level": level,
        "level_mult": LEVEL_MULT.get(level, 0.90),
        "best_of": best_of,
        "round": (row.get("round") or "").strip(),
        "match_num": (row.get("match_num") or "").strip(),
        "tourney": (row.get("tourney_name") or "").strip(),
        "winner": canonical((row.get("winner_name") or "").strip()),
        "loser": canonical((row.get("loser_name") or "").strip()),
        "score": score,
        "walkover": walkover,
        "retirement": retirement,
        "minutes": _fnum(row.get("minutes")),
        "w_rank": _fnum(row.get("winner_rank")),
        "l_rank": _fnum(row.get("loser_rank")),
        "w_age": _fnum(row.get("winner_age")),
        "l_age": _fnum(row.get("loser_age")),
        "w_stats": {k: _fnum(row.get(f"w_{k}")) for k in
                    ("ace", "df", "svpt", "1stIn", "1stWon", "2ndWon", "SvGms", "bpSaved", "bpFaced")},
        "l_stats": {k: _fnum(row.get(f"l_{k}")) for k in
                    ("ace", "df", "svpt", "1stIn", "1stWon", "2ndWon", "SvGms", "bpSaved", "bpFaced")},
    }


def _sort_key(m: dict) -> tuple:
    try:
        mn = int(m.get("match_num") or 0)
    except (ValueError, TypeError):
        mn = 0
    return (m["date"], m["tourney"], ROUND_ORDER.get(m["round"], 5), mn)


def load_years(first: int = 2018, last: int = 2026) -> list[dict]:
    out: list[dict] = []
    for y in range(first, last + 1):
        p = RAW / f"{y}.csv"
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                m = normalize(row)
                if m and m["winner"] and m["loser"]:
                    out.append(m)
    out.sort(key=_sort_key)
    return out
