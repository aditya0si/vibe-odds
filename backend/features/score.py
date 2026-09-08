"""Parse tennis scores into structural facts. No ML, no I/O."""

from __future__ import annotations

import re

SET_RE = re.compile(r"(\d+)-(\d+)(?:\((\d+)\))?")


def parse_score(score: str) -> dict | None:
    """'6-2 6-7(5) 6-3' -> sets/tiebreaks/games. None for W/O or unparseable."""
    score = (score or "").strip()
    if not score or score == "W/O":
        return None
    retired = "RET" in score
    score = score.replace("RET", "").strip()
    sets_w = sets_l = tb_w = tb_l = games_w = games_l = 0
    for tok in score.split():
        m = SET_RE.fullmatch(tok)
        if not m:
            continue
        w, l = int(m.group(1)), int(m.group(2))
        games_w += w
        games_l += l
        if w > l:
            sets_w += 1
        elif l > w:
            sets_l += 1
        if m.group(3) is not None:  # tiebreak played; winner took it
            if w > l:
                tb_w += 1
            else:
                tb_l += 1
    if sets_w == 0 and sets_l == 0:
        return None
    return {"sets_w": sets_w, "sets_l": sets_l, "tb_w": tb_w, "tb_l": tb_l,
            "games_w": games_w, "games_l": games_l, "retired": retired,
            "decider": (sets_w + sets_l) >= 3 and abs(sets_w - sets_l) == 1}
