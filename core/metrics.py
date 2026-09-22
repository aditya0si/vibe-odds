"""Sport-neutral scoring metrics: every adapter scores predictions with these.

Kept tiny and pure (lists in, floats out) so both the tennis and NBA pipelines
score identically — a metric defined twice drifts twice. Data loading and
binning policy stay in the adapters; only the maths lives here.
"""

from __future__ import annotations


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


def brier(probs: list[float], outcomes: list[int]) -> float:
    """Mean squared probability error (the headline loss).

    Refuses empty input: a silent 0.0 on zero games would read as perfection.
    (``ece`` keeps its historical 0.0-on-empty contract; do not copy that.)
    """
    if not probs:
        raise ValueError("brier: no predictions to score")
    return sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(probs)
