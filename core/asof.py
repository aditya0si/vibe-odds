"""As-of replay harness (sport-neutral core, map step 8).

One replay loop rules every derived artifact: walkovers update nothing,
pre-window matches still advance state, and a row is built BEFORE its own
match is replayed into the state — so every value is computed from history
strictly before the match (the no-leakage contract, map R1).

Callers own everything sport-specific: match loading and order (tennis:
``backend.ratings.loader`` — never reorder its stream), the state objects
(``new_ctx``/``replay_ctx``), and what a row contains (``row_fn``).
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator, TypeVar

M = TypeVar("M")  # match dict-like
R = TypeVar("R")  # row


def asof_rows(matches: Iterable[M], ctx, replay: Callable, row_fn: Callable,
              first: int = 0) -> Iterator[R]:
    """Yield ``row_fn(m, ctx)`` for matches from year ``first``, strictly as-of.

    ``replay(ctx, m)`` runs for EVERY non-walkover match; rows are built first
    so a row never sees its own match. Matches before the window still replay
    (state warm-up) but emit nothing.
    """
    for m in matches:
        if m["walkover"]:
            continue
        if m["date"] >= first * 10000:
            row = row_fn(m, ctx)
            if row is not None:
                yield row
        replay(ctx, m)


def replay_all(matches: Iterable[M], ctx, replay: Callable) -> tuple[int, int]:
    """Advance ``ctx`` over every non-walkover match. Returns (cutoff_date, n)."""
    cutoff, n = 0, 0
    for m in matches:
        if m["walkover"]:
            continue
        replay(ctx, m)
        cutoff = max(cutoff, m["date"])
        n += 1
    return cutoff, n
