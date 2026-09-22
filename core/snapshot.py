"""Version-guarded state snapshots (sport-neutral core, map step 8).

A snapshot may only ever cost time, never correctness: a version bump (or a
corrupt file) must fall back to a full replay instead of restoring stale
semantics. Atomic write: tmp + replace. Pickle protocol 4 because the state
objects' ``snapshot()`` payloads are plain dicts/lists/tuples of numbers.

Adapters decide what goes in the blob and how to restore it (tennis:
``backend.ratings.snapshot`` — six rating tables, ``VERSION = 4``).
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path


def save_versioned(path: Path | str, blob: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(blob, protocol=4))
    os.replace(tmp, path)
    return path


def load_versioned(path: Path | str, version: int):
    """The blob, or None if missing/corrupt/wrong version (caller replays)."""
    try:
        blob = pickle.loads(Path(path).read_bytes())
    except Exception:
        return None
    if not isinstance(blob, dict) or blob.get("version") != version:
        return None
    return blob
