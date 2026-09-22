"""FastAPI server: compare, model verdicts, EV, tracker. Serves frontend/ static UI.

Tennis (men's singles) only. Write endpoints (/track, /settle, /learn,
/rollback) require X-API-Key when the VIBE_API_KEY env var is set —
without it anyone could poison weights/policy. When unset (local dev),
health reports auth:false as a reminder.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query

load_dotenv()

from core.odds import (best_per_outcome, ev_per_unit, find_arbitrage,
                               kelly_fraction, no_vig_probs)
from core.api import require_write_key, serve_frontend
from backend.api.adapter import TennisAdapter
from backend.api import analytics as _analytics
from backend.providers import the_odds_api as prov
from core import tracker

app = FastAPI(title="Vibe-Odds (tennis)")
BASE = Path(__file__).resolve().parents[2]
FRONT = BASE / "frontend"
serve_frontend(app, FRONT, config=TennisAdapter().config)

GROUPS = ["usopen", "tennis"]
WRITE_KEY = os.getenv("VIBE_API_KEY", "")


def _require_key(x_api_key: str | None) -> None:
    require_write_key(x_api_key, WRITE_KEY)


@app.get("/api/health")
def health():
    return {"ok": True, "has_key": bool(os.getenv("ODDS_API_KEY")),
            "auth": bool(WRITE_KEY), "provider": prov.last_status(),
            "quota": prov.quota(), "groups": GROUPS}


@app.get("/api/events")
def events(group: str = Query("usopen", enum=GROUPS),
           mock: bool = False):
    try:
        evs = prov.fetch_group(group, mock=mock or not os.getenv("ODDS_API_KEY"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [{"id": e.get("id"), "sport": e.get("sport_key"),
             "label": f"{e.get('away_team')} vs {e.get('home_team')}",
             "commence_time": e.get("commence_time"),
             "books": len(e.get("bookmakers", []))} for e in evs]


@app.get("/api/compare")
def compare(event_id: str, market: str = "h2h", group: str = "usopen", mock: bool = False):
    if group not in GROUPS:
        raise HTTPException(status_code=400, detail=f"unknown group {group!r}")
    evs = prov.fetch_group(group, mock=mock or not os.getenv("ODDS_API_KEY"))
    ev = next((e for e in evs if e.get("id") == event_id), evs[0] if evs else None)
    if not ev:
        return {"error": "no events", "mock": prov.last_status().get("mode") == "mock"}
    books = prov.to_books_map(ev, market)
    if not books:
        return {"event": event_id, "market": market, "books": {}, "note": "no books for market"}
    outcomes = sorted({o for b in books.values() for o in b})
    fair, fair_source, warning = _fair_probs(books, outcomes)
    best = best_per_outcome(books)
    rows = []
    for o in outcomes:
        b = best.get(o, {})
        f = fair.get(o, 0)
        ev_v = ev_per_unit(f, b.get("odds", 0)) if b else 0
        rows.append({"outcome": o, "fair_prob": round(f, 4), "fair_odds": round(1 / f, 3) if f else None,
                     "best_odds": b.get("odds"), "best_book": b.get("book"),
                     "ev": round(ev_v, 4), "kelly_half": round(kelly_fraction(f, b.get("odds", 1)), 3) if b else 0})
    arb = find_arbitrage(best)
    return {"event": {"id": ev.get("id"), "label": f"{ev.get('away_team')} vs {ev.get('home_team')}",
                      "commence_time": ev.get("commence_time")},
            "market": market, "books": books, "rows": rows, "arb": arb,
            "fair_source": fair_source, "warning": warning,
            "feed": prov.last_status().get("mode"),
            "tweet": make_tweet(ev, rows)}


def make_tweet(ev: dict, rows: list[dict]) -> str:
    label = f"{ev.get('away_team')} vs {ev.get('home_team')}"
    lines = [f"[USO Mens] {label}"]
    for r in sorted(rows, key=lambda x: x["ev"], reverse=True)[:3]:
        if r["ev"] and r["ev"] > 0:
            lines.append(f"{r['outcome']} {r['best_odds']} ({r['best_book']}) EV {r['ev']*100:.1f}%")
    lines.append("#USOpen #Tennis")
    return "\n".join(lines)


def _event_status(commence_iso: str | None, n_books: int) -> dict:
    """started/thin guards: a dead market must never wear a VALUE badge."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    started, hours_old = False, None
    try:
        dt = datetime.fromisoformat((commence_iso or "").replace("Z", "+00:00"))
        hours_old = (now - dt).total_seconds() / 3600
        started = hours_old > 0.5
    except Exception:
        pass
    thin = n_books < 5
    return {"started": started, "thin": thin, "hours_old": round(hours_old, 1) if hours_old is not None else None,
            "bettable": not started and not thin}


def _fair_probs(books: dict, outcomes: list) -> tuple[dict, str, str | None]:
    """Shared fair-price logic: Pinnacle if it agrees with field, else consensus median."""
    med = {}
    for o in outcomes:
        prices = sorted(b[o] for b in books.values() if o in b)
        med[o] = prices[len(prices) // 2] if prices else 0
    fair_cons = dict(zip(outcomes, no_vig_probs([med[o] for o in outcomes])))
    pin = books.get("Pinnacle")
    fair_pin = None
    if pin and all(o in pin for o in outcomes):
        fair_pin = dict(zip(outcomes, no_vig_probs([pin[o] for o in outcomes])))
    warning = None
    if fair_pin:
        div = max(abs(fair_pin[o] - fair_cons[o]) for o in outcomes)
        if div > 0.12:
            warning = (f"Pinnacle disagrees with field by {div*100:.0f}pts "
                       f"(possibly stale/live). Fair = consensus median.")
            return fair_cons, "consensus-median", warning
        return fair_pin, "pinnacle", None
    return fair_cons, "consensus-median", None


def _build_card(ev: dict, group: str, market: str = "h2h") -> dict:
    """Full prediction card for one event: model vs market, edges, policy, flags."""
    from backend.model.serve import predict_match
    from backend.policy.bandit import Policy

    books = prov.to_books_map(ev, market)
    outcomes = sorted({o for b in books.values() for o in b})
    base = {"id": ev.get("id"),
            "label": f"{ev.get('away_team')} vs {ev.get('home_team')}",
            "commence_time": ev.get("commence_time"),
            "n_books": len(books)}
    if len(outcomes) != 2:
        return {**base, "error": "needs exactly 2 outcomes", "outcomes": outcomes}
    fair, fair_source, warning = _fair_probs(books, outcomes)
    a, b = outcomes
    best_of = 5 if group == "usopen" else 3
    card = predict_match(a, b, "hard", market_prob_a=fair[a], best_of=best_of)
    best = best_per_outcome(books)
    edges = {}
    for o in outcomes:
        bb = best.get(o)
        mp_free = card["model_prob_free_a"] if o == a else 1 - card["model_prob_free_a"]
        mp_full = card["model_prob_a"] if o == a else 1 - card["model_prob_a"]
        edges[o] = {"model_prob": round(mp_full, 4),
                    "model_prob_free": round(mp_free, 4),
                    "fair_prob": round(fair.get(o, 0), 4),
                    "best_odds": bb.get("odds") if bb else None,
                    "best_book": bb.get("book") if bb else None,
                    "edge": round(ev_per_unit(mp_free, bb["odds"]), 4) if bb else None,
                    "kelly_half": round(kelly_fraction(mp_free, bb["odds"]), 3) if bb else 0}
    pk = card["pick"]
    fav_market = a if fair[a] >= 0.5 else b
    gap = abs(edges[pk]["model_prob_free"] - edges[pk]["fair_prob"])
    policy = Policy.load().decide("hard", edges[pk]["edge"] or 0, card["model_prob_pick"])
    bandit_action = policy["action"]
    # W3 policy verdict: frozen ev003 (+20.57u) nearly doubles the bandit
    # (+11.04u) on identical sim matches -> the bandit is demoted to shadow.
    # Live action = frozen threshold + cold-start + bettable guards.
    for o in outcomes:
        qs = sorted(b[o] for b in books.values() if o in b)
        edges[o]["field_median"] = round(qs[(len(qs) - 1) // 2], 3) if qs else None
        edges[o]["n_quotes"] = len(qs)
    status = _event_status(ev.get("commence_time"), len(books))
    raw_value = (edges[pk]["edge"] or 0) >= 0.03
    # W1 cold-start containment: no stake under 10 tour-level matches.
    fam = card.get("familiarity") or {}
    thin_names = [nm for nm, cnt in fam.items() if (cnt or 0) < 10]
    cold_start = bool(thin_names)
    action = ("post" if raw_value and status["bettable"] and not cold_start
              else "pass")
    return {**base, "pick": pk, "conf": card["model_prob_pick"],
            "model_prob_a": card["model_prob_a"],
            "badge": _analytics.calibration_badge(card["model_prob_pick"]),
            "fair_source": fair_source, "warning": warning,
            "unknown_players": card["unknown_players"],
            "outcomes": edges, "signals": card["signals"],
            "weights_used": card["weights_used"], "reasons": card["reasons"][:4],
            "policy": action, "policy_bandit": bandit_action, "status": status,
            "cold_start": {"guarded": cold_start, "players": thin_names},
            "flags": {"disagree": (pk != fav_market) and status["bettable"],
                      "gap": round(gap, 4),
                      "value": raw_value and status["bettable"],
                      "edge_raw": raw_value,  # what value WOULD be ignoring staleness
                      "big_gap": gap >= 0.07 and status["bettable"]}}


@app.get("/api/board")
def board(group: str = "usopen", market: str = "h2h"):
    """Every upcoming match with model vs market, sorted by start time."""
    if group not in GROUPS:
        raise HTTPException(status_code=400, detail=f"unknown group {group!r}")
    evs = prov.fetch_group(group, mock=not os.getenv("ODDS_API_KEY"))
    cards = [_build_card(ev, group, market) for ev in evs]
    cards.sort(key=lambda c: c.get("commence_time") or "")
    feed = prov.last_status().get("mode")
    age = None
    for sk in prov.SPORT_GROUPS.get(group, []):
        a = prov.cache_age_s(sk)
        if a is not None and (age is None or a < age):
            age = a
    n_value = sum(1 for c in cards if (c.get("flags") or {}).get("value"))
    n_dis = sum(1 for c in cards if (c.get("flags") or {}).get("disagree"))
    return {"group": group, "feed": feed, "cache_age_s": round(age) if age is not None else None,
            "matches": cards,
            "summary": {"n": len(cards), "value": n_value, "disagree": n_dis}}


@app.get("/api/predict")
def predict(event_id: str, group: str = "usopen", market: str = "h2h"):
    from backend.model.serve import predict_match
    if group not in GROUPS:
        raise HTTPException(status_code=400, detail=f"unknown group {group!r}")
    evs = prov.fetch_group(group, mock=not os.getenv("ODDS_API_KEY"))
    ev = next((e for e in evs if e.get("id") == event_id), evs[0] if evs else None)
    if not ev:
        return {"error": "no events"}
    books = prov.to_books_map(ev, market)
    outcomes = sorted({o for b in books.values() for o in b})
    if len(outcomes) != 2:
        return {"error": "model verdict needs exactly 2 outcomes", "outcomes": outcomes}
    fair, fair_source, warning = _fair_probs(books, outcomes)
    a, b = outcomes
    surface = "hard"
    best_of = 5 if group == "usopen" else 3  # slam men's singles
    card = predict_match(a, b, surface, market_prob_a=fair[a], best_of=best_of)
    best = best_per_outcome(books)
    edges = {}
    for o in outcomes:
        bb = best.get(o)
        # Edge ALWAYS vs the market-free model prob: the full prob saw the
        # market (0.19 weight), so edge-vs-full would dilute every signal.
        mp_free = card["model_prob_free_a"] if o == a else 1 - card["model_prob_free_a"]
        mp_full = card["model_prob_a"] if o == a else 1 - card["model_prob_a"]
        edges[o] = {"model_prob": round(mp_full, 4),
                    "model_prob_free": round(mp_free, 4),
                    "best_odds": bb.get("odds") if bb else None,
                    "best_book": bb.get("book") if bb else None,
                    "edge": round(ev_per_unit(mp_free, bb["odds"]), 4) if bb else None,
                    "kelly_half": round(kelly_fraction(mp_free, bb["odds"]), 3) if bb else 0}
    label = f"{ev.get('away_team')} vs {ev.get('home_team')}"
    pk = card["pick"]
    tweet = (f"[USO Model] {pk} {card['model_prob_pick']:.0%} over "
             f"{b if pk == a else a} | best {edges[pk]['best_odds']} {edges[pk]['best_book']} "
             f"edge {edges[pk]['edge']*100:+.1f}% (free) | " + " | ".join(card["reasons"][:2]))
    from backend.policy.bandit import Policy
    bandit = Policy.load().decide(surface, edges[pk]["edge"] or 0, card["model_prob_pick"])
    fam = card.get("familiarity") or {}
    cold = any((cnt or 0) < 10 for cnt in fam.values())
    action = "post" if (edges[pk]["edge"] or 0) >= 0.03 and not cold else "pass"
    policy = {"action": action, "bandit": bandit["action"],
              "reason": f"frozen ev>=0.03 {'blocked' if action == 'pass' else 'met'}"
                        + (" (cold-start)" if cold and (edges[pk]["edge"] or 0) >= 0.03 else ""),
              "n": bandit.get("n", 0)}
    return {"event": {"id": ev.get("id"), "label": label,
                      "commence_time": ev.get("commence_time")},
            "verdict": card, "market": edges, "fair_source": fair_source,
            "warning": warning, "unknown_players": card["unknown_players"],
            "feed": prov.last_status().get("mode"),
            "policy": policy, "tweet": tweet}


@app.get("/api/props")
def props(event_id: str, group: str = "usopen", mock: bool = False):
    if group not in GROUPS:
        raise HTTPException(status_code=400, detail=f"unknown group {group!r}")
    evs = prov.fetch_group(group, mock=mock or not os.getenv("ODDS_API_KEY"))
    ev = next((e for e in evs if e.get("id") == event_id), evs[0] if evs else None)
    if not ev:
        return {"props": []}
    return {"event": event_id, "props": prov.list_props(ev)[:200]}


@app.post("/api/track")
def track(payload: dict, x_api_key: str | None = Header(default=None)):
    _require_key(x_api_key)
    pid = tracker.log_pick(
        sport=payload.get("sport", ""), event_id=payload.get("event_id", ""),
        event_label=payload.get("event_label", ""), market=payload.get("market", "h2h"),
        outcome=payload.get("outcome", ""), book=payload.get("book", ""),
        odds_taken=float(payload.get("odds_taken", 0)), fair_prob=float(payload.get("fair_prob", 0)),
        ev=float(payload.get("ev", 0)), note=payload.get("note", ""),
        model_prob=payload.get("model_prob"), surface=payload.get("surface", ""))
    return {"id": pid}


@app.post("/api/settle")
def settle(payload: dict, x_api_key: str | None = Header(default=None)):
    _require_key(x_api_key)
    from backend.policy.bandit import Policy, bucket, reward_of

    pid = int(payload["id"])
    tracker.settle_pick(pid, payload["result"], payload.get("closing_fair_odds"))
    pick = tracker.get_pick(pid)
    policy_info = None
    if pick and payload["result"] in ("win", "loss", "push"):
        pol = Policy.load()
        b = bucket(pick.get("surface") or "", pick.get("ev") or 0,
                   pick.get("model_prob") or pick.get("fair_prob") or 0.5)
        r = reward_of(payload["result"], pick.get("odds_taken") or 1.0)
        pol.update(b, "post", r)
        pol.save()
        policy_info = {"bucket": b, "reward": r}
    return {"ok": True, "policy": policy_info}


@app.get("/api/stats")
def stats():
    return {"stats": tracker.stats(), "calibration": tracker.calibration(),
            "tuning": tracker.suggest_ev_threshold(),
            "provider": prov.last_status(), "quota": prov.quota()}


@app.get("/api/weights")
def weights():
    from backend.model.ensemble import Ensemble
    ens = Ensemble.load()
    return {"global": {k: round(v, 3) for k, v in ens.global_w.items()},
            "surfaces": {s: {k: round(v, 3) for k, v in w.items()}
                         for s, w in ens.surf.items()},
            "eta": ens.eta}


@app.get("/api/upsets")
def upsets(n: int = 20):
    from backend.model.upsets import pattern_summary, recent_upsets
    return {"upsets": recent_upsets(n), "patterns": pattern_summary()}


@app.post("/api/learn")
def learn(payload: dict | None = None, x_api_key: str | None = Header(default=None)):
    _require_key(x_api_key)
    from backend.model.learn import run
    since = (payload or {}).get("since", 20260101)
    return run(int(since))


@app.get("/api/sim")
def sim():
    import json as _json
    from pathlib import Path as _Path
    p = _Path(__file__).resolve().parents[2] / "data" / "sim_2024_2025.json"
    if not p.exists():
        return {"error": "no sim ledger yet — run python -m backend.sim.season"}
    return _json.loads(p.read_text())


@app.get("/api/players")
def players(q: str = "", n: int = 50, surface: str = "hard"):
    """Top players by blended Elo (read-only). q filters by name substring."""
    if surface not in ("hard", "clay", "grass"):
        raise HTTPException(status_code=400, detail="surface must be hard|clay|grass")
    return {"surface": surface, "players": _analytics.players_list(q, n, surface)}


@app.get("/api/players/{name}")
def player_detail(name: str, vs: str = ""):
    """Elo snapshot + form + surface splits + recent matches + optional H2H."""
    return _analytics.player_detail(name, vs)


@app.get("/api/h2h")
def head_to_head(a: str, b: str):
    return _analytics.h2h(a, b)


@app.get("/api/model")
def model_summary():
    """Frozen model evidence: sim arms, paired CIs, calibration validation,
    column tournament, ablation. Read-only from data files, no recompute."""
    return _analytics.model_summary()


@app.get("/api/reliability")
def reliability(n_bins: int = 10):
    """Binned GBM out-of-fold probs vs outcomes for the reliability diagram."""
    return _analytics.reliability(n_bins)


@app.post("/api/rollback")
def rollback(payload: dict, x_api_key: str | None = Header(default=None)):
    """Restore a learned-state file from its newest backup. target: weights|calibration|policy."""
    _require_key(x_api_key)
    from core.io import rollback as _rb
    from core import calibration as _cal
    from backend.model.ensemble import WEIGHTS_PATH
    from backend.policy.bandit import PATH as _pol
    targets = {"weights": WEIGHTS_PATH, "calibration": _cal.CAL_PATH, "policy": _pol}
    t = (payload or {}).get("target", "")
    if t not in targets:
        raise HTTPException(status_code=400, detail=f"target must be one of {sorted(targets)}")
    return _rb(targets[t])
