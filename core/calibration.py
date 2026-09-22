"""Isotonic calibration via PAVA (no sklearn needed). Monotone stepwise fit.

Sport-neutral core (map step 2): the stepwise-fit maths is identical for every
adapter; only storage differs. ``path`` is injectable on every reader/writer so
tests and new sport adapters never touch shared evidence by accident;
``CAL_PATH`` remains the tennis default and the historical monkeypatch point.

The expected-calibration-error metric lives in ``core.metrics`` and is
re-exported here for historical callers (``calibrate.ece``).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.metrics import ece  # noqa: F401  (re-export: CAL.ece)

CAL_PATH = Path(__file__).resolve().parents[1] / "data" / "calibration.json"

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


def _resolve(path: Path | None) -> Path:
    return Path(path) if path is not None else CAL_PATH


def save_fn(fn: list[tuple[float, float]], surface: str = "_global",
            path: Path | None = None) -> None:
    from core.io import atomic_write_json
    target = _resolve(path)
    if surface == "_global":
        # legacy single-file layout still supported by load_fn()
        atomic_write_json(target, fn)
    else:
        all_fn = load_all(target)
        all_fn[surface] = fn
        atomic_write_json(target, {"_v": 2, "tables": all_fn})


def load_fn(surface: str | None = None, path: Path | None = None) -> list[tuple[float, float]] | None:
    try:
        raw = json.loads(_resolve(path).read_text())
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


def load_all(path: Path | None = None) -> dict:
    """All per-surface calibration tables ({} if legacy/absent)."""
    try:
        raw = json.loads(_resolve(path).read_text())
    except Exception:
        return {}
    if isinstance(raw, dict) and raw.get("_v") == 2:
        return dict(raw.get("tables", {}))
    return {}
