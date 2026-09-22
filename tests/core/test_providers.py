"""Provider core: cache/quota stores + quote sanitizer + archive (map step 7)."""

from __future__ import annotations

import json

from core.providers import archive, odds_api


def test_cache_quota_roundtrip_injected_paths(tmp_path):
    cache = {"k": {"ts": 1.0, "events": []}}
    odds_api._save_cache(tmp_path / "c.json", cache)
    assert odds_api._load_cache(tmp_path / "c.json") == cache
    odds_api._save_quota(tmp_path / "q.json", {"remaining": 10, "used": 5, "updated": 1.0})
    assert odds_api.quota(tmp_path / "q.json")["remaining"] == 10
    assert odds_api.cache_age_s("sk", cache_path=tmp_path / "none.json") is None


def test_sanitizer_drops_and_logs(tmp_path):
    ev = {"bookmakers": [
        {"title": "Good", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 1.5}, {"name": "B", "price": 2.6}]}]},
        {"title": "Suspended", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 1.0}, {"name": "B", "price": 1.0}]}]},
        {"title": "Stale", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 61.0}, {"name": "B", "price": 1.05}]}]},
    ]}
    log = tmp_path / "dropped.jsonl"
    books = odds_api.to_books_map(ev, dropped_log=log)
    assert "Suspended" not in books
    assert books["Good"] == {"A": 1.5, "B": 2.6}
    assert "A" not in books.get("Stale", {})  # 61.0 vs 1.5 median: dropped
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert rows and all("book" in r and "median" in r and "ts" in r for r in rows)


def test_archive_store_and_manifest(tmp_path):
    p = archive.store("sk", [{"id": 1}], {"m": 1}, arch_dir=tmp_path)
    assert p is not None and p.exists()
    man = archive.manifest(tmp_path)
    assert man and man[0]["n_events"] == 1 and man[0]["meta"]["sport_key"] == "sk"
