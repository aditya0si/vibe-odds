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
    c = d["db_counts"]
    # games = raw rows; the split keeps unplayed scheduled rows from masquerading as played
    assert c["games"] == c["games_completed"] + c["games_scheduled"]
    assert c["games_completed"] >= 25258          # scored corpus intact (10 historical
    # neutrals sit unscored until the box-score repair pass fills them)
    assert len(d["seasons"]) >= 21
    assert d["seasons"][-1]["season"] == "2026-27"  # the upcoming schedule is ingested


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
    assert "/static/nba-app.js" in html                 # local module entry
    assert "/static/court-evidence.css" in html         # local stylesheet
    assert "Loading evidence…" in html                  # boot state present

    # Endpoints come from the injected config, not from literals typed into the
    # template; assert the wired routes rather than substrings of the page body.
    cfg = json.loads(html.split("window.__ADAPTER__ = ", 1)[1].split(";</script>", 1)[0])
    assert cfg["sport"] == "nba"
    assert cfg["title"] == "Court Evidence — NBA"
    assert cfg["tabs"] == TABS
    for key, ep in cfg["api"].items():
        assert ep.startswith("/api/"), f"{key} route not same-origin: {ep}"


def test_nba_shell_adapter_title_and_static_assets():
    s, b = _asgi(server.app, "GET", "/")
    assert s == 200
    html = b.decode("utf-8")
    assert "Court Evidence — NBA" in html
    assert "<main" in html or 'id="main"' in html
    for asset in ("/static/nba-app.js", "/static/transport.js",
                  "/static/request-state.js", "/static/nba-contract.js",
                  "/static/court-evidence.css"):
        st, body = _asgi(server.app, "GET", asset)
        assert st == 200, f"{asset} -> {st}"
        assert body, f"{asset} served empty"


def test_nba_empty_log_contract(tmp_path, monkeypatch):
    log = tmp_path / "empty.jsonl"
    monkeypatch.setattr(server, "LOG", log)
    d = _get("/api/board")
    assert d == {"board": [], "n": 0}


def test_nba_historical_error_http200():
    s, b = _asgi(server.app, "GET", "/api/predict?game_id=NOPE")
    assert s == 200                          # an unknown id is a 200 error body, not a prediction
    body = json.loads(b)
    assert "error" in body and body["game_id"] == "NOPE"


def test_nba_tiers_use_passes():
    d = _get("/api/model")
    assert d["tiers"], "model has no tiers"
    for key, tier in d["tiers"].items():
        assert "passes" in tier, f"tier {key} is missing the frozen 'passes' verdict"
        assert isinstance(tier["passes"], bool)
        assert "pass" not in tier or tier.get("pass") is not tier["passes"], (
            f"tier {key} exposes a legacy 'pass' key that must not be read as a verdict"
        )


def test_nba_null_bins_preserved():
    from sports.nba import evidence
    d = evidence.reliability_view(n_bins=50)   # force empty bins
    empties = [b for b in d["bins"] if b["n"] == 0]
    assert empties, "expected empty bins at 50 bins"
    for b in empties:
        assert b["mean_p"] is None and b["rate"] is None, "empty bin must stay a gap, not zero"


def test_nba_scheduled_counts_are_separate():
    d = _get("/api/stats")
    c = d["db_counts"]
    assert c["games"] == c["games_completed"] + c["games_scheduled"]
    assert c["games_scheduled"] > 0            # 2026-27 schedule is ingested, unscored
