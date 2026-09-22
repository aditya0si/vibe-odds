"""PATH step 7 gate: API contract shapes, page load, the pre-tip live log."""

from __future__ import annotations

import asyncio
import json

import pytest

from sports.nba import live_log
from sports.nba import server
from sports.nba.evidence import TABS


def _asgi(app, method="GET", path="/", headers=(), body=b""):
    chunks, status = [], {}
    hdrs = [(b"host", b"t")] + list(headers)
    if body:
        hdrs.append((b"content-type", b"application/json"))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            status["s"] = m["status"]
        elif m["type"] == "http.response.body":
            chunks.append(m.get("body", b""))

    p, _, q = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             "path": p, "raw_path": p.encode(), "query_string": q.encode(),
             "headers": hdrs, "client": ("127.0.0.1", 1),
             "server": ("t", 80), "root_path": "", "state": {}}
    asyncio.run(server.app(scope, receive, send))
    return status["s"], b"".join(chunks)


def _get(path):
    s, b = _asgi(server.app, "GET", path)
    assert s == 200, f"{path} -> {s}"
    return json.loads(b)


def test_site_meta_contract():
    d = _get("/api/site_meta")
    assert d["sport"] == "nba" and d["tabs"] == TABS
    assert {a["key"] for a in d["artifacts"]} >= {"formula", "walkforward", "calibration"}


def test_model_contract():
    d = _get("/api/model")
    f = d["formula"]
    assert len(f["features"]) == len(f["coef_raw"]) == 7
    assert set(d["pooled_all_test"]) >= {"formula", "market_close", "climatological"}
    assert "tiers" in d and "by_season" in d and "paired_vs_close" in d


def test_experiments_contract():
    d = _get("/api/experiments")
    assert "verdict" in d["availability"]
    assert d["calibration"]["decision"]
    assert "A5" in d["ablation"] and "A6" in d["ablation"]


def test_sim_contract():
    d = _get("/api/sim")
    assert "tiers" in d and "pooled_all_test" in d and d["n_test_games"]["all"] > 4000


def test_stats_contract():
    d = _get("/api/stats")
    assert d["db_counts"]["games"] == 25268
    assert len(d["seasons"]) == 21


def test_teams_contract():
    d = _get("/api/teams")
    assert 0.5 < d["home_win_rate"] < 0.7 and d["teams"][0]["wins"] > 0


def test_reliability_contract():
    d = _get("/api/reliability?n_bins=10")
    assert d["n"] > 4000 and len(d["bins"]) == 10 and "ece" in d


def test_predict_contract():
    from sports.nba.evidence import _test_rows_and_labels
    rows, _ = _test_rows_and_labels()
    gid = rows[len(rows) // 2]["game_id"]
    d = _get(f"/api/predict?game_id={gid}")
    assert d["game_id"] == gid and 0.0 < d["prob_home"] < 1.0
    assert d["pick"] in ("home", "away")
    bad = _get("/api/predict?game_id=NOPE")
    assert "error" in bad


def test_board_and_live_log_endpoints(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    monkeypatch.setattr(server, "LOG", log)
    monkeypatch.setattr(live_log, "LOG_PATH", log)
    live_log.log_prediction("G1", "2026-10-27T23:30:00Z", 0.62,
                            logged_at="2026-10-27T20:00:00Z")
    live_log.settle("G1", 112, 105)
    d = _get("/api/live/log")
    assert d["n_events"] == 2
    b = _get("/api/board")
    assert b["n"] == 1 and b["board"][0]["correct"] is True


def test_live_log_pre_tip_gate(tmp_path):
    log = tmp_path / "log.jsonl"
    row = live_log.log_prediction("G2", "2026-10-28T01:00:00Z", 0.55,
                                  logged_at="2026-10-27T22:00:00Z", out=log)
    assert row["logged_at"] < row["tipoff_ts"]     # THE gate: logged_at < tipoff
    assert row["pick"] == "home"
    with pytest.raises(live_log.LatePrediction):
        live_log.log_prediction("G2", "2026-10-28T01:00:00Z", 0.9,
                                logged_at="2026-10-28T02:00:00Z", out=log)
    with pytest.raises(live_log.LatePrediction):
        live_log.log_prediction("G2", "2026-10-28T01:00:00Z", 0.9,
                                logged_at="2026-10-28T01:00:00Z", out=log)  # equal is late


def test_settle_endpoint_keyed(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    monkeypatch.setattr(server, "LOG", log)
    monkeypatch.setattr(server, "WRITE_KEY", "sekrit")
    body = json.dumps({"game_id": "G9", "home_score": 101, "away_score": 99}).encode()
    s, _ = _asgi(server.app, "POST", "/api/live/settle", body=body)
    assert s == 401                                          # write lane is keyed
    assert _asgi(server.app, "POST", "/api/x", body=body)[0] == 405   # read-only guard
    s, b = _asgi(server.app, "POST", "/api/live/settle", body=body,
                 headers=[(b"x-api-key", b"sekrit")])
    assert s == 200 and json.loads(b)["game_id"] == "G9"
    s, _ = _asgi(server.app, "POST", "/api/live/settle", body=b"{}",
                 headers=[(b"x-api-key", b"sekrit")])
    assert s == 400                                          # keyed but bad payload


def test_index_headless_load():
    s, b = _asgi(server.app, "GET", "/")
    html = b.decode("utf-8")
    assert s == 200
    assert "window.__ADAPTER__" in html
    for tab in TABS:
        assert tab in html                       # 5 tabs wired
    for ep in ("/api/site_meta", "/api/board", "/api/model", "/api/experiments",
               "/api/teams", "/api/sim", "/api/stats", "/api/reliability"):
        assert ep in html                        # fetch targets exist
    assert "loading…" in html                    # boot state present
