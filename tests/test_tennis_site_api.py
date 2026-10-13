"""Tennis ASGI integration gate (Court Evidence Task 4).

Drives the REAL ``backend.api.server.app`` through direct ASGI invocation — the
app and its routes are never replaced by a fake server. Provider store paths and
the model verdict log are sandboxed into ``tmp_path`` so no production cache or
evidence file can be written.

Determinism split (explicit):
* provider FETCH is a controlled seam (``prov.fetch_group`` / missing key) — this
  is controlled-input integration, NOT a live-feed smoke;
* the MODEL verdict is a controlled seam for the board/predict tests, while the
  real ``_build_card`` endpoint handler still executes end to end;
* reliability / players / detail read real frozen state from disk (no network).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from backend.api import server
from backend.providers import the_odds_api as prov


def _asgi(method="GET", path="/", headers=(), body=b""):
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
    s, b = _asgi("GET", path)
    assert s == 200, f"{path} -> {s}"
    return json.loads(b)


# Controlled, valid provider event (2 outcomes, clean books).
EVENT = {
    "id": "ev1",
    "sport_key": "tennis_atp_us_open",
    "sport_title": "ATP US Open",
    "commence_time": "2030-01-01T18:00:00Z",
    "home_team": "Player B",
    "away_team": "Player A",
    "bookmakers": [
        {"key": "pinnacle", "title": "Pinnacle",
         "markets": [{"key": "h2h", "outcomes": [
             {"name": "Player A", "price": 1.80}, {"name": "Player B", "price": 2.10}]}]},
        {"key": "draftkings", "title": "DraftKings",
         "markets": [{"key": "h2h", "outcomes": [
             {"name": "Player A", "price": 1.85}, {"name": "Player B", "price": 2.05}]}]},
    ],
}


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Redirect every writer the suite could touch into tmp (never data/)."""
    monkeypatch.setattr(prov, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(prov, "QUOTA_PATH", tmp_path / "quota.json")
    monkeypatch.setattr(prov, "DROPPED_LOG", tmp_path / "dropped.jsonl")
    import backend.model.serve as serve
    monkeypatch.setattr(serve, "VERDICTS_LOG", tmp_path / "verdicts.jsonl")
    return tmp_path


@pytest.fixture
def controlled_predictor(monkeypatch):
    """Deterministic model seam: a controlled valid predictor output."""
    import backend.model.serve as serve

    def fake_predict(a, b, surface="hard", date=99999999, market_prob_a=None,
                     news_lean_a=None, best_of=3):
        p = 0.60
        return {
            "a": a, "b": b, "surface": surface, "pick": a if p >= 0.5 else b,
            "model_prob_a": p, "model_prob_cal_a": p, "model_prob_free_a": p,
            "model_prob_pick": p, "prob_source": "gbm",
            "signals": {"elo": 0.6, "gbm": 0.6}, "weights_used": {"gbm": 1.0},
            "reasons": ["controlled fixture reason"], "unknown_players": [],
            "familiarity": {a: 100, b: 100},
        }

    monkeypatch.setattr(serve, "predict_match", fake_predict)
    return fake_predict


# ---- adapter + assets ------------------------------------------------------

def test_tennis_adapter_routes_and_assets():
    s, b = _asgi("GET", "/")
    assert s == 200
    html = b.decode("utf-8")
    assert "Court Evidence" in html
    cfg = json.loads(html.split("window.__ADAPTER__ = ", 1)[1].split(";</script>", 1)[0])
    assert cfg["sport"] == "tennis"
    assert cfg["title"] == "Court Evidence — ATP tennis"
    assert set(cfg["tabs"]) == {"Matches", "Players", "Model", "Experiments", "Methodology"}
    for key in ("health", "board", "predict", "players", "model", "stats", "reliability", "player_detail"):
        assert key in cfg["api"], f"adapter is missing the {key} route"
        assert cfg["api"][key].startswith("/api/"), f"{key} route not same-origin"
    assert cfg["api"]["player_detail"] == "/api/players/{name}"

    for asset in ("/static/tennis-app.js", "/static/tennis-contract.js",
                  "/static/transport.js", "/static/request-state.js",
                  "/static/court-evidence.css"):
        st, body = _asgi("GET", asset)
        assert st == 200, f"{asset} -> {st}"
        assert body, f"{asset} served empty"


def test_tennis_health_contract():
    h = _get("/api/health")
    assert h["ok"] is True
    assert set(h["groups"]) == {"usopen", "tennis"}
    assert "has_key" in h and "provider" in h and "quota" in h


# ---- board + feed containment ----------------------------------------------

def test_tennis_board_missing_key_forces_mock(monkeypatch, controlled_predictor):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    d = _get("/api/board?group=usopen")
    assert d["group"] == "usopen"
    assert d["feed"] == "mock", "no credential must force mock events"
    assert isinstance(d["matches"], list) and d["matches"]
    assert "cache_age_s" in d and "summary" in d
    # _build_card really ran; with the controlled predictor the card is valid.
    card = d["matches"][0]
    assert "pick" in card and "outcomes" in card
    assert len(card["outcomes"]) == 2


def test_tennis_build_card_controlled_predictor(monkeypatch, controlled_predictor):
    """Direct real ``_build_card`` invocation with controlled valid model output."""
    card = server._build_card(EVENT, "usopen", "h2h")
    assert set(card["outcomes"]) == {"Player A", "Player B"}
    assert card["pick"] in card["outcomes"]
    assert card["fair_source"] in ("pinnacle", "consensus-median")
    assert card["status"]["bettable"] in (True, False)
    for o in card["outcomes"].values():
        assert 0.0 <= o["model_prob"] <= 1.0
        assert 0.0 <= o["model_prob_free"] <= 1.0


def test_tennis_board_error_card_shape(monkeypatch):
    bad = {
        "id": "ev-bad", "sport_key": "tennis_atp_us_open", "commence_time": "2030-01-01T18:00:00Z",
        "home_team": "B", "away_team": "A",
        "bookmakers": [{"key": "x", "title": "X", "markets": [
            {"key": "h2h", "outcomes": [{"name": "A", "price": 1.8}]}]}],
    }
    monkeypatch.setattr(prov, "fetch_group", lambda group, mock=False: [bad])
    d = _get("/api/board?group=usopen")
    card = d["matches"][0]
    assert card["id"] == "ev-bad"
    assert card["error"] == "needs exactly 2 outcomes"
    assert isinstance(card["outcomes"], list)
    assert "pick" not in card


def test_tennis_group_validation():
    assert _asgi("GET", "/api/board?group=bogus")[0] == 400
    assert _asgi("GET", "/api/events?group=bogus")[0] == 400


# ---- predict ---------------------------------------------------------------

def test_tennis_predict_unknown_id_falls_back(monkeypatch, controlled_predictor):
    """The backend returns the FIRST event for an unknown id; the client contract
    must reject the returned-ID mismatch (tested in tennis-contract.test.mjs)."""
    monkeypatch.setattr(prov, "fetch_group", lambda group, mock=False: [EVENT])
    d = _get("/api/predict?event_id=NOPE&group=usopen")
    assert d["event"]["id"] == "ev1", "backend fallback documents why the client validates IDs"
    assert d["event"]["id"] != "NOPE"
    assert d["verdict"]["pick"] in d["market"]


# ---- reliability / players -------------------------------------------------

def test_tennis_reliability_equal_count_contract():
    d = _get("/api/reliability?n_bins=10")
    assert d["n"] > 1000
    assert sum(b["n"] for b in d["bins"]) == d["n"]
    assert "ece_oof" in d
    for b in d["bins"]:
        assert "p_mean" in b and "frac_pos" in b and "n" in b
        assert "lo" not in b and "hi" not in b, "tennis bins are equal-count, not fixed-width"


def test_tennis_players_surface_and_unknown_detail():
    d = _get("/api/players?q=alcaraz&n=5&surface=clay")
    assert d["surface"] == "clay"
    assert any(p["name"] == "Carlos Alcaraz" for p in d["players"])
    assert _asgi("GET", "/api/players?surface=bogus")[0] == 400

    u = _get("/api/players/No%20Such%20Player%20XYZ")
    assert u["unknown"] is True
    assert "note" in u
