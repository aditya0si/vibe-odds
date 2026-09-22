"""Verify the claims this repo makes about itself. Run from the repo root.

Currently checks:
  1. README states the test-count claim.
  2. The tennis (modelling) suite collects exactly 49 tests.
  3. The evidence guard suite exists and collects at least one test.
  4. Every frozen NBA evidence artifact exists and matches its manifest hash.
  5. The NBA adapter suite collects the claimed number of tests.

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
MODELLING_CLAIM = 49
NBA_ADAPTER_CLAIM = 40

NBA_MANIFEST = json.loads(
    (ROOT / "tests/evidence/nba_frozen_manifest.json").read_text(encoding="utf-8"))
NBA_ARTIFACTS: dict[str, dict] = NBA_MANIFEST["files"]


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


def main() -> int:
    problems: list[str] = []

    if "49 modelling tests" not in README:
        problems.append("README no longer states '49 modelling tests'")

    modelling = collected(["tests", "--ignore=tests/evidence", "--ignore=tests/sports"])
    if modelling != MODELLING_CLAIM:
        problems.append(f"modelling suite collects {modelling}, README claims {MODELLING_CLAIM}")

    guards = collected(["tests/evidence"])
    if guards < 1:
        problems.append("evidence guard suite collects no tests")

    nba = collected(["tests/sports"])
    if nba != NBA_ADAPTER_CLAIM:
        problems.append(f"NBA adapter suite collects {nba}, claimed {NBA_ADAPTER_CLAIM}")

    for rel, meta in sorted(NBA_ARTIFACTS.items()):
        p = ROOT / rel
        if not p.exists():
            problems.append(f"missing NBA evidence artifact: {rel}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
            problems.append(
                f"NBA evidence drifted: {rel} (hash != nba_frozen_manifest.json); "
                "rebaseline the manifest in a commit that says which number moved")

    print(f"tennis modelling tests: {modelling} (claimed {MODELLING_CLAIM})")
    print(f"evidence guard tests:   {guards}")
    print(f"nba adapter tests:      {nba} (claimed {NBA_ADAPTER_CLAIM})")
    print(f"nba frozen artifacts:   {len(NBA_ARTIFACTS)}")
    if problems:
        for p in problems:
            print(f"CLAIM FAILED: {p}")
        return 1
    print("claims hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
