"""Live pre-registered prediction log for 2026-27 (PATH step 7.3).

Every prediction is logged BEFORE tip-off with a timestamp — the only true
out-of-sample record we can own. A row whose logged_at is not strictly before
tipoff is REJECTED: a post-tip "prediction" is not evidence.

Append-only JSONL (core.ledger). Settlements are separate events; board()
merges prediction + settlement per game.

CLI:
  python -m sports.nba.live_log --game-id 2026... --tipoff 2026-10-27T23:30:00Z --prob 0.62
  python -m sports.nba.live_log --settle 2026... --score 112:105
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from core.ledger import append_jsonl, read_jsonl
from sports.nba.db.paths import DATA

LOG_PATH = DATA / "live_log_2026_27.jsonl"


class LatePrediction(ValueError):
    """logged_at >= tipoff: this is not a prediction, it is a result."""


def _ts(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def require_pre_tip(kind: str, game_id: str, tipoff_ts, at) -> datetime:
    """The pre-tip discipline, shared by prediction rows and T-60 odds rows: a row
    timestamped at or after tip-off is a result, not evidence. Raises LatePrediction;
    returns the parsed timestamp on success."""
    tip = _ts(tipoff_ts)
    ts = _ts(at)
    if ts >= tip:
        raise LatePrediction(
            f"refusing to store {kind} for {game_id}: at {_iso(ts)} >= tipoff {_iso(tip)}")
    return ts


def log_prediction(game_id: str, tipoff_ts, prob_home: float, *,
                   source: str = "formula_v1", logged_at=None,
                   out: Path | None = None) -> dict:
    """Append one pre-tip prediction row. Raises LatePrediction after tip-off."""
    tip = _ts(tipoff_ts)
    logged = require_pre_tip(
        "prediction", game_id, tipoff_ts,
        logged_at if logged_at is not None else datetime.now(timezone.utc))
    row = {"type": "predict", "game_id": game_id,
           "tipoff_ts": _iso(tip), "logged_at": _iso(logged),
           "prob_home": round(float(prob_home), 4),
           "pick": "home" if prob_home >= 0.5 else "away",
           "source": source}
    append_jsonl(out or LOG_PATH, row)
    return row


def settle(game_id: str, home_score: int, away_score: int, *,
           settled_at=None, out: Path | None = None) -> dict:
    row = {"type": "settle", "game_id": game_id,
           "home_score": int(home_score), "away_score": int(away_score),
           "settled_at": _iso(_ts(settled_at) if settled_at is not None
                              else datetime.now(timezone.utc))}
    append_jsonl(out or LOG_PATH, row)
    return row


def events(out: Path | None = None) -> list[dict]:
    return read_jsonl(out or LOG_PATH)


def board(out: Path | None = None) -> list[dict]:
    """Per-game merge: latest prediction + settlement + correctness."""
    preds: dict[str, dict] = {}
    sets: dict[str, dict] = {}
    for e in events(out):
        if e.get("type") == "predict":
            preds[e["game_id"]] = e
        elif e.get("type") == "settle":
            sets[e["game_id"]] = e
    rows = []
    for gid, p in sorted(preds.items(), key=lambda kv: kv[1]["tipoff_ts"]):
        s = sets.get(gid)
        row = dict(p)
        if s:
            home_won = s["home_score"] > s["away_score"]
            row["settled"] = {"home_score": s["home_score"], "away_score": s["away_score"],
                              "settled_at": s["settled_at"]}
            row["correct"] = (p["pick"] == "home") == home_won
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="2026-27 pre-tip prediction log")
    ap.add_argument("--game-id")
    ap.add_argument("--tipoff", help="ISO tip-off time (row must be logged before it)")
    ap.add_argument("--prob", type=float, help="P(home win)")
    ap.add_argument("--source", default="formula_v1")
    ap.add_argument("--settle", metavar="GAME_ID")
    ap.add_argument("--score", metavar="H:A")
    ap.add_argument("--out", default=None, help="alternate log path (tests/tools)")
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else None
    if a.settle and a.score:
        h, _, away = a.score.partition(":")
        print(json.dumps(settle(a.settle, int(h), int(away), out=out)))
        return 0
    if not (a.game_id and a.tipoff and a.prob is not None):
        ap.error("need --game-id --tipoff --prob (or --settle GAME_ID --score H:A)")
    print(json.dumps(log_prediction(a.game_id, a.tipoff, a.prob,
                                    source=a.source, out=out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
