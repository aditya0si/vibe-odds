"""Frozen-evidence ledger: append-only logs + hash manifests.

Sport-neutral (map step 3): every adapter that publishes evidence freezes it
behind a manifest and appends its verdicts through these helpers, so "frozen
evidence" and "accountability log" mean the same thing in every sport.
Manifest bytes are pinned by test: ``json.dumps(manifest, indent=1)``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def read_json(path: Path | str):
    """Read one JSON document; None if missing/corrupt (callers decide)."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def read_jsonl(path: Path | str) -> list:
    """Read an append-only log; [] if missing. Tolerates a torn final line."""
    out: list = []
    try:
        lines = Path(path).read_text().splitlines()
    except Exception:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue  # torn write; append_jsonl is line-atomic from here on
    return out


def append_jsonl(path: Path | str, row: dict) -> None:
    """Append one row to an evidence log (creates parent dirs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def sha12(path: Path | str) -> str | None:
    """Content hash prefix used in manifests; None = file absent."""
    p = Path(path)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def freeze_manifest(manifest: dict, target: Path) -> dict:
    """Write a freeze manifest in pinned evidence format.

    Bytes are ``json.dumps(manifest, indent=1)`` — evidence guards hash these
    files, so the format is part of the contract, not an implementation detail.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=1))
    return manifest


def verify_manifest(manifest: dict, root: Path) -> list[str]:
    """Names whose bytes no longer match the manifest ([] = clean).

    ``manifest['files']`` keys are paths relative to ``root`` (as written by
    the freeze that produced the manifest).
    """
    bad: list[str] = []
    for name, sha in (manifest or {}).get("files", {}).items():
        if sha is None:
            continue
        if sha12(Path(root) / name) != sha:
            bad.append(name)
    return bad
