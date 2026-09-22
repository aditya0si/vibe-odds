"""Evidence ledger helpers: append-only logs + hash manifests (map step 3)."""

from __future__ import annotations

import json

from core import ledger


def test_read_append_jsonl_roundtrip(tmp_path):
    p = tmp_path / "log.jsonl"
    assert ledger.read_jsonl(p) == []
    ledger.append_jsonl(p, {"a": 1})
    ledger.append_jsonl(p, {"b": 2})
    assert ledger.read_jsonl(p) == [{"a": 1}, {"b": 2}]


def test_read_jsonl_tolerates_torn_final_line(tmp_path):
    """A crash mid-append must not poison the whole log."""
    p = tmp_path / "log.jsonl"
    ledger.append_jsonl(p, {"ok": 1})
    with p.open("a", encoding="utf-8") as f:
        f.write('{"torn"')  # simulated crash mid-write
    assert ledger.read_jsonl(p) == [{"ok": 1}]


def test_read_json_missing_or_corrupt_is_none(tmp_path):
    assert ledger.read_json(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{corrupt")
    assert ledger.read_json(bad) is None


def test_sha12_manifest_roundtrip_and_tamper_detection(tmp_path):
    a = tmp_path / "a.json"
    a.write_text('{"x": 1}')
    man = {"cutoff": 20240101,
           "files": {"a.json": ledger.sha12(a),
                     "missing.json": ledger.sha12(tmp_path / "missing.json")},
           "note": "t"}
    assert man["files"]["missing.json"] is None

    target = tmp_path / "sim_frozen" / "manifest.json"
    ledger.freeze_manifest(man, target)
    # evidence format is pinned: json.dumps(manifest, indent=1)
    assert target.read_text() == json.dumps(man, indent=1)

    assert ledger.verify_manifest(ledger.read_json(target), tmp_path) == []
    a.write_text('{"x": 2}')  # tamper
    assert ledger.verify_manifest(man, tmp_path) == ["a.json"]
