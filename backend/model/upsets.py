"""Upset autopsy: when a confident prediction loses, diagnose WHY and what to learn.

Upset = ensemble prob of the actual loser >= UPSET_CONF (default 0.60).
The analysis splits signals into right/wrong camps, names the biggest
offender, and records a lesson. Lessons accumulate in data/upsets.json and
the learner surfaces systemic patterns (e.g. 'serve signal wrong in 4 of
last 5 hard-court upsets' -> its weight is already decaying via Hedge).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.report import log_json_list, recent_json, upset_patterns

UPSETS_PATH = Path(__file__).resolve().parents[2] / "data" / "upsets.json"
UPSET_CONF = 0.60


def analyze(a: str, b: str, surface: str, probs: dict[str, float | None],
            weights_used: dict[str, float], ensemble_p_a: float,
            winner: str, event_label: str = "", date: int = 0) -> dict | None:
    y_a = 1 if winner == a else 0
    loser = b if winner == a else a
    conf_loser = ensemble_p_a if winner == b else 1 - ensemble_p_a
    if conf_loser < UPSET_CONF:
        return None
    right, wrong = [], []
    for k, p in probs.items():
        if p is None:
            continue
        ok = (p >= 0.5) == (y_a == 1)
        (right if ok else wrong).append(
            {"signal": k, "prob_winner": round(p if y_a else 1 - p, 3),
             "weight": weights_used.get(k, 0)})
    wrong.sort(key=lambda x: -abs(x["prob_winner"] - 0.5))
    right.sort(key=lambda x: -abs(x["prob_winner"] - 0.5))
    lessons = []
    if wrong:
        w0 = wrong[0]
        lessons.append(
            f"{w0['signal']} was the biggest offender: gave {loser} "
            f"{1 - w0['prob_winner']:.0%} (weight {w0['weight']}) — Hedge already decayed it.")
    if right:
        lessons.append(
            f"{right[0]['signal']} saw it coming ({right[0]['prob_winner']:.0%} on {winner}) — its weight grew.")
    if all(s["signal"] in ("elo_surface", "elo_overall", "market") for s in wrong[:2]) and len(wrong) >= 2:
        lessons.append("Favorites-based signals all failed: possible injury/form shock or matchup issue — check news.")
    if not right:
        lessons.append("Every awake signal was wrong: true surprise. No structural lesson; weights barely move on one event.")
    return {"date": date, "event": event_label, "surface": surface,
            "winner": winner, "loser": loser,
            "model_conf_loser": round(conf_loser, 3),
            "right": right, "wrong": wrong, "lessons": lessons}


def log_upset(report: dict, cap: int = 200) -> None:
    log_json_list(UPSETS_PATH, report, cap)


def recent_upsets(n: int = 20) -> list[dict]:
    return recent_json(UPSETS_PATH, n)


def pattern_summary() -> dict:
    """Which signals are most often on the wrong side of upsets?"""
    try:
        data = json.loads(UPSETS_PATH.read_text())
    except Exception:
        return {}
    return upset_patterns(data)
