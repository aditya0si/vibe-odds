"""Exact point -> game -> tiebreak -> set -> match conversion under iid.

All distributions computed by direct dynamic programming (no closed forms
to get wrong). Verified by test_markov.py (mass conservation, symmetry).
"""

from __future__ import annotations

import math


def game_p(p: float) -> float:
    """P(server wins game) given point-win prob p."""
    win_before = sum(math.comb(3 + i, i) * p ** 4 * (1 - p) ** i for i in range(3))
    p_deuce = math.comb(6, 3) * p ** 3 * (1 - p) ** 3
    win_deuce = p ** 2 / (p ** 2 + (1 - p) ** 2) if 0 < p < 1 else p
    return win_before + p_deuce * win_deuce


def _tb_server(k: int, first: str = "A") -> str:
    """Server of point k (0-indexed): ABBAABB... starting with `first`."""
    if k == 0:
        return first
    other = "B" if first == "A" else "A"
    return other if ((k - 1) // 2) % 2 == 0 else first


def tiebreak_p(pA: float, pB: float, to: int = 7, first: str = "A",
               max_points: int = 300) -> float:
    """P(A wins tiebreak), first to `to` win-by-2. pA/pB = pt-win on own serve."""
    dp = {(0, 0): 1.0}
    win = 0.0
    for k in range(max_points):
        nxt: dict = {}
        rest = 0.0
        for (a, b), m in dp.items():
            if a + b != k or m == 0:
                rest += m
                continue
            s = _tb_server(k, first)
            pa = pA if s == "A" else 1 - pB
            for na, nb, q in ((a + 1, b, pa), (a, b + 1, 1 - pa)):
                if (na >= to or nb >= to) and abs(na - nb) >= 2:
                    if na > nb:
                        win += m * q
                    # else B wins: mass leaves
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + m * q
        dp = nxt
        if not dp:
            break
        if sum(dp.values()) < 1e-12:
            break
    return win


def set_p(gA: float, gB: float, pA: float, pB: float, decider: bool = False,
          tb_to_decider: int = 10) -> float:
    """P(A wins set), A serves first. 6-6 -> tiebreak (deciders: first-to-10)."""
    dp = {(0, 0): 1.0}
    win = 0.0
    for _ in range(200):
        nxt: dict = {}
        for (i, j), m in dp.items():
            if m == 0:
                continue
            g = gA if (i + j) % 2 == 0 else gB  # prob SERVER holds
            pa = g if (i + j) % 2 == 0 else 1 - g  # prob A wins the game
            for ni, nj, q in ((i + 1, j, pa), (i, j + 1, 1 - pa)):
                if ni == 6 and nj == 6:
                    tb = tiebreak_p(pA, pB, to=(tb_to_decider if decider else 7),
                                    first=("A" if (i + j) % 2 == 0 else "B"))
                    win += m * q * tb
                elif (ni >= 6 or nj >= 6) and abs(ni - nj) >= 2:
                    if ni > nj:
                        win += m * q
                else:
                    nxt[(ni, nj)] = nxt.get((ni, nj), 0.0) + m * q
        dp = nxt
        if not dp or sum(dp.values()) < 1e-12:
            break
    return win


def match_p(fAB: float, fBA: float, best_of: int = 3) -> float:
    """P(A wins match) from serve point probs."""
    gA, gB = game_p(fAB), game_p(fBA)
    s = set_p(gA, gB, fAB, fBA)
    s5 = set_p(gA, gB, fAB, fBA, decider=True)
    if best_of == 3:
        return s ** 2 + 2 * s * (1 - s) * s5
    if best_of == 5:
        return s ** 3 + 3 * s ** 3 * (1 - s) + 6 * s ** 2 * (1 - s) ** 2 * s5
    raise ValueError("best_of must be 3 or 5")
