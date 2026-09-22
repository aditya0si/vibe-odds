"""Freeze the system as of 2023-12-31 for the season simulator.

Copies the GBM model + feature meta (both trained on <=2023 data only),
current Hedge weights, and calibration into data/sim_frozen/ with a manifest.
The sim rebuilds ratings state by replay (deterministic), so no pickle needed.

Manifest/log mechanics live in ``core.ledger`` (map step 3).

python -m backend.sim.freeze
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.ledger import freeze_manifest, sha12

DATA = Path(__file__).resolve().parents[2] / "data"
# FROZEN is a module constant so tests can redirect it (tests/conftest.py):
# the suite must never rewrite frozen evidence under data/.
FROZEN = DATA / "sim_frozen"
CUTOFF = 20240101

_sha = sha12  # historical name


def freeze(cutoff: int = CUTOFF, verbose: bool = True) -> dict:
    FROZEN.mkdir(parents=True, exist_ok=True)
    copied = {}
    for name in ("gbm.txt", "gbm_features.json", "weights.json", "calibration.json"):
        src = DATA / name
        if src.exists():
            shutil.copy2(src, FROZEN / name)
            copied[name] = sha12(src)
        else:
            copied[name] = None
    manifest = {"cutoff": cutoff, "files": copied,
                "note": "GBM trained on <=2023; weights/calibration as live at freeze time."}
    freeze_manifest(manifest, FROZEN / "manifest.json")
    if verbose:
        print(json.dumps(manifest, indent=1))
    return manifest


if __name__ == "__main__":
    freeze()
