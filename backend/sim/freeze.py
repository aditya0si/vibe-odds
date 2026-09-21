"""Freeze the system as of 2023-12-31 for the season simulator.

Copies the GBM model + feature meta (both trained on <=2023 data only),
current Hedge weights, and calibration into data/sim_frozen/ with a manifest.
The sim rebuilds ratings state by replay (deterministic), so no pickle needed.

python -m backend.sim.freeze
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
# FROZEN is a module constant so tests can redirect it (tests/conftest.py):
# the suite must never rewrite frozen evidence under data/.
FROZEN = DATA / "sim_frozen"
CUTOFF = 20240101


def _sha(p: Path) -> str | None:
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def freeze(cutoff: int = CUTOFF, verbose: bool = True) -> dict:
    FROZEN.mkdir(parents=True, exist_ok=True)
    copied = {}
    for name in ("gbm.txt", "gbm_features.json", "weights.json", "calibration.json"):
        src = DATA / name
        if src.exists():
            shutil.copy2(src, FROZEN / name)
            copied[name] = _sha(src)
        else:
            copied[name] = None
    manifest = {"cutoff": cutoff, "files": copied,
                "note": "GBM trained on <=2023; weights/calibration as live at freeze time."}
    (FROZEN / "manifest.json").write_text(json.dumps(manifest, indent=1))
    if verbose:
        print(json.dumps(manifest, indent=1))
    return manifest


if __name__ == "__main__":
    freeze()
