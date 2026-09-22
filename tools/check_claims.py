"""Verify the claims this repo makes about itself. Run from the repo root.

Checks:
  1. README keeps the published tennis record ("49 modelling tests" at the
     2026-09-07 freeze).
  2. The machine-readable claims block in README
     (``<!-- claims: modelling=N evidence=N nba=N total=N -->``) matches live
     pytest collection. Update the block whenever tests are added — this tool
     fails on drift in either direction.
  3. Every published NBA literal in ``tests/evidence/nba_published_numbers.py``
     appears in the README (minus-normalised): docs may not drift from the
     re-derived module.
  4. Every frozen NBA evidence artifact exists and matches its manifest hash.

Exit code 0 = claims hold. Any drift = non-zero, so CI can fail on a stale claim.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
README_NORM = README.replace("\u2212", "-")  # typographic minus -> ASCII

sys.path.insert(0, str(ROOT))
from tests.evidence import nba_published_numbers as NB  # noqa: E402

NBA_MANIFEST = json.loads(
    (ROOT / "tests/evidence/nba_frozen_manifest.json").read_text(encoding="utf-8"))
NBA_ARTIFACTS: dict[str, dict] = NBA_MANIFEST["files"]

# published literals that MUST appear in the README (docs may not drift)
PINNED = [
    "T1_FORMULA_BRIER", "T1_CLIMATOLOGICAL_BRIER", "T1_ELO_ONLY_BRIER",
    "T2_MEAN_DIFF", "T3_MEAN_DIFF",
    "MARKET_FORMULA_BRIER", "MARKET_OPEN_BRIER", "MARKET_CLOSE_BRIER",
    "MARKET_OPEN_TO_CLOSE_MEAN_DIFF",
    "SIGMA_D_LOCK", "SIGMA_D_CURRENT_CODE",
    "A5_GAP_TO_OPEN", "A6_GAP_TO_OPEN", "A6_A5_MEAN_DIFF",
    "T2_CI", "T3_CI", "MARKET_OPEN_TO_CLOSE_CI", "A6_A5_CI",
]


def collected(args: list[str]) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=ROOT, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    if not m:
        print(out[-2000:])
        raise SystemExit("could not parse pytest collection output")
    return int(m.group(1))


def _substrings(value) -> list[str]:
    if isinstance(value, (tuple, list)):
        out: list[str] = []
        for v in value:
            out.extend(_substrings(v))
        return out
    s = repr(float(value))
    return [] if s in ("0.0", "1.0") else [s]


def main() -> int:
    problems: list[str] = []

    if "49 modelling tests" not in README:
        problems.append("README lost the published '49 modelling tests' record")

    m = re.search(r"<!-- claims: modelling=(\d+) evidence=(\d+) nba=(\d+) total=(\d+) -->",
                  README)
    if not m:
        problems.append("README has no machine-readable claims block")
        return _finish(problems)
    claim_mod, claim_ev, claim_nba, claim_total = map(int, m.groups())

    modelling = collected(["tests", "--ignore=tests/evidence", "--ignore=tests/sports"])
    if modelling != claim_mod:
        problems.append(f"modelling suite collects {modelling}, README claims {claim_mod}")

    guards = collected(["tests/evidence"])
    if guards != claim_ev:
        problems.append(f"evidence guards collect {guards}, README claims {claim_ev}")

    nba = collected(["tests/sports"])
    if nba != claim_nba:
        problems.append(f"NBA suite collects {nba}, README claims {claim_nba}")

    total = collected([])
    if total != claim_total:
        problems.append(f"total suite collects {total}, README claims {claim_total}")

    for attr in PINNED:
        for s in _substrings(getattr(NB, attr)):
            if s not in README_NORM:
                problems.append(f"README is missing published literal {attr}={s}")

    for rel, meta in sorted(NBA_ARTIFACTS.items()):
        p = ROOT / rel
        if not p.exists():
            problems.append(f"missing NBA evidence artifact: {rel}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
            problems.append(
                f"NBA evidence drifted: {rel} (hash != nba_frozen_manifest.json); "
                "rebaseline the manifest in a commit that says which number moved")

    return _finish(problems, modelling, guards, nba, total)


def _finish(problems, modelling=0, guards=0, nba=0, total=0) -> int:
    print(f"tennis modelling tests: {modelling}")
    print(f"evidence guard tests:   {guards}")
    print(f"nba tests:              {nba}")
    print(f"total:                  {total}")
    print(f"nba frozen artifacts:   {len(NBA_ARTIFACTS)}")
    if problems:
        for p in problems:
            print(f"CLAIM FAILED: {p}")
        return 1
    print("claims hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
