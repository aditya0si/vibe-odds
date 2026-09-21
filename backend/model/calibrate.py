"""Isotonic calibration via PAVA (no sklearn needed). Monotone stepwise fit."""

from __future__ import annotations

import json
from pathlib import Path

CAL_PATH = Path(__file__).resolve().parents[2] / "data" / "calibration.json"


def fit_isotonic(probs: list[float], outcomes: list[int]) -> list[tuple[float, float]]:
    """Returns [(prob_threshold, calibrated_value)] stepwise function."""
    order = sorted(range(len(probs)), key=lambda i: probs[i])
    ys = [float(outcomes[i]) for i in order]
    xs = [probs[i] for i in order]
    # PAVA: pool adjacent violators
    blocks: list[list[float]] = [[y] for y in ys]
    bx: list[list[float]] = [[x] for x in xs]
    i = 0
    while i < len(blocks) - 1:
        m1 = sum(blocks[i]) / len(blocks[i])
        m2 = sum(blocks[i + 1]) / len(blocks[i + 1])
        if m1 > m2:
            blocks[i] = blocks[i] + blocks[i + 1]
            bx[i] = bx[i] + bx[i + 1]
            del blocks[i + 1]
            del bx[i + 1]
            i = max(0, i - 1)
        else:
            i += 1
    fn = []
    for xs_b, ys_b in zip(bx, blocks):
        fn.append((max(xs_b), sum(ys_b) / len(ys_b)))
    return fn


def apply_isotonic(fn: list[tuple[float, float]], p: float) -> float:
    for thresh, val in fn:
        if p <= thresh:
            return val
    return fn[-1][1] if fn else p


def save_fn(fn: list[tuple[float, float]], surface: str = "_global") -> None:
    from core.io import atomic_write_json
    if surface == "_global":
        # legacy single-file layout still supported by load_fn()
        atomic_write_json(CAL_PATH, fn)
    else:
        all_fn = load_all()
        all_fn[surface] = fn
        atomic_write_json(CAL_PATH, {"_v": 2, "tables": all_fn})


def load_fn(surface: str | None = None) -> list[tuple[float, float]] | None:
    try:
        raw = json.loads(CAL_PATH.read_text())
    except Exception:
        return None
    if isinstance(raw, dict) and raw.get("_v") == 2:
        tables = raw.get("tables", {})
        if surface and surface in tables:
            return [(float(t), float(v)) for t, v in tables[surface]]
        glob = tables.get("_global")
        if glob:
            return [(float(t), float(v)) for t, v in glob]
        return None
    try:
        return [(float(t), float(v)) for t, v in raw]
    except Exception:
        return None


def load_all() -> dict:
    """All per-surface calibration tables ({} if legacy/absent)."""
    try:
        raw = json.loads(CAL_PATH.read_text())
    except Exception:
        return {}
    if isinstance(raw, dict) and raw.get("_v") == 2:
        return dict(raw.get("tables", {}))
    return {}


def ece(probs: list[float], outcomes: list[int], n_bins: int = 10) -> float:
    """Expected calibration error."""
    bins = [[] for _ in range(n_bins)]
    for p, y in zip(probs, outcomes):
        bins[min(n_bins - 1, int(p * n_bins))].append((p, y))
    tot, err = len(probs), 0.0
    for b in bins:
        if not b:
            continue
        ap = sum(p for p, _ in b) / len(b)
        ay = sum(y for _, y in b) / len(b)
        err += len(b) / tot * abs(ap - ay)
    return err
