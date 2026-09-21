"""Atomic JSON persistence with timestamped backups + rollback.

Every learned-state file (weights, calibration, policy, upsets, sim ledger)
goes through here. Writes are tmp-file + os.replace (no torn files on
crash), the previous version is kept under data/backups/ (capped), and
``rollback()`` restores the last good version when a learn run poisons state.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

BACKUP_KEEP = 20


def _backup_dir(path: Path) -> Path:
    d = path.parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write_json(path: Path, obj, backup: bool = True) -> Path:
    """Write obj as JSON atomically. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        bdir = _backup_dir(path)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = bdir / f"{path.stem}-{stamp}{path.suffix}"
        try:
            dest.write_bytes(path.read_bytes())
        except Exception:
            pass
        # cap: drop oldest
        try:
            olds = sorted(bdir.glob(f"{path.stem}-*{path.suffix}"))
            for extra in olds[:-BACKUP_KEEP]:
                with contextlib.suppress(FileNotFoundError):
                    extra.unlink()
        except Exception:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)
    return path


def backups_for(path: Path) -> list[str]:
    bdir = Path(path).parent / "backups"
    try:
        return sorted(p.name for p in bdir.glob(f"{Path(path).stem}-*{Path(path).suffix}"))
    except Exception:
        return []


def rollback(path: Path, which: int = -1) -> dict:
    """Restore a backup over path. which=-1 -> most recent. Returns status."""
    path = Path(path)
    names = backups_for(path)
    if not names:
        return {"ok": False, "error": f"no backups for {path.name}"}
    try:
        name = names[which]
    except IndexError:
        return {"ok": False, "error": f"backup index {which} out of range ({len(names)})"}
    src = path.parent / "backups" / name
    # keep the (bad) current version too, so rollback itself is reversible
    atomic_write_json(path, json.loads(src.read_text()), backup=True)
    return {"ok": True, "restored": name, "path": str(path)}
