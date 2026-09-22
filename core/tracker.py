"""Self-improving pick tracker: sqlite log of picks, CLV, calibration, tuning.

Sport-neutral (map step 6): the schema carries a ``sport`` column, so one
store can hold every adapter's picks. Paths are injected: every function takes
``path=`` and falls back to the module ``DB_PATH`` (tennis default:
data/tracker.db).

Schema rule (map R14): additive migrations only — never rename a column;
old rows must keep reading. ``surface`` is a context tag, not a key.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS picks(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ts DATETIME DEFAULT CURRENT_TIMESTAMP,
 sport TEXT, event_id TEXT, event_label TEXT,
 market TEXT, outcome TEXT, book TEXT,
 odds_taken REAL, fair_prob REAL, ev REAL,
 closing_fair_odds REAL, result TEXT DEFAULT 'pending', -- win|loss|push|pending
 note TEXT
);"""


def _resolve(path: Path | None) -> Path:
    return Path(path) if path is not None else DB_PATH


def _db(path: Path | None = None) -> sqlite3.Connection:
    db = _resolve(path)
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.execute(SCHEMA)
    for col in ("ALTER TABLE picks ADD COLUMN model_prob REAL",
                "ALTER TABLE picks ADD COLUMN surface TEXT DEFAULT ''"):
        try:
            c.execute(col)
        except sqlite3.OperationalError:
            pass  # column already exists
    c.execute("CREATE INDEX IF NOT EXISTS idx_picks_result ON picks(result)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_picks_surface ON picks(surface)")
    return c


def get_pick(pick_id: int, path: Path | None = None) -> dict | None:
    c = _db(path)
    row = c.execute(
        "SELECT id,sport,event_id,event_label,market,outcome,book,odds_taken,"
        " fair_prob,ev,closing_fair_odds,result,note,model_prob,surface"
        " FROM picks WHERE id=?", (pick_id,)).fetchone()
    c.close()
    if not row:
        return None
    keys = ["id", "sport", "event_id", "event_label", "market", "outcome", "book",
            "odds_taken", "fair_prob", "ev", "closing_fair_odds", "result", "note",
            "model_prob", "surface"]
    return dict(zip(keys, row))


def log_pick(sport: str, event_id: str, event_label: str, market: str, outcome: str,
             book: str, odds_taken: float, fair_prob: float, ev: float, note: str = "",
             model_prob: float | None = None, surface: str = "",
             path: Path | None = None) -> int:
    c = _db(path)
    cur = c.execute(
        "INSERT INTO picks(sport,event_id,event_label,market,outcome,book,odds_taken,fair_prob,ev,note,model_prob,surface)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (sport, event_id, event_label, market, outcome, book, odds_taken, fair_prob, ev, note, model_prob, surface))
    c.commit()
    pid = cur.lastrowid
    c.close()
    return int(pid)


def settle_pick(pick_id: int, result: str, closing_fair_odds: float | None = None,
                path: Path | None = None) -> None:
    assert result in ("win", "loss", "push", "pending")
    c = _db(path)
    if closing_fair_odds is None:
        c.execute("UPDATE picks SET result=? WHERE id=?", (result, pick_id))
    else:
        c.execute("UPDATE picks SET result=?, closing_fair_odds=? WHERE id=?",
                  (result, closing_fair_odds, pick_id))
    c.commit()
    c.close()


def stats(path: Path | None = None) -> dict:
    c = _db(path)
    rows = c.execute(
        "SELECT odds_taken, fair_prob, ev, closing_fair_odds, result FROM picks").fetchall()
    c.close()
    settled = [r for r in rows if r[4] in ("win", "loss", "push")]
    wins = sum(1 for r in settled if r[4] == "win")
    # naive ROI: 1u flat stakes
    profit = sum((r[0] - 1.0) if r[4] == "win" else (-1.0 if r[4] == "loss" else 0.0) for r in settled)
    clvs = []
    for o_taken, _, _, o_close, res in rows:
        if o_taken and o_close and o_close > 1.0:
            try:
                clvs.append((o_taken / o_close - 1.0) * 100.0)
            except Exception:
                pass
    avg_clv = sum(clvs) / len(clvs) if clvs else 0.0
    return {
        "total_picks": len(rows),
        "settled": len(settled),
        "win_rate": (wins / len(settled)) if settled else 0.0,
        "flat_profit_u": round(profit, 2),
        "roi_pct": round(profit / len(settled) * 100, 2) if settled else 0.0,
        "avg_clv_pct": round(avg_clv, 2),
        "n_clv": len(clvs),
    }


def calibration(n_bins: int = 5, path: Path | None = None) -> list[dict]:
    """Bin settled picks by MODEL prob (falling back to fair_prob for old rows),
    compare predicted vs actual win rate."""
    c = _db(path)
    rows = c.execute(
        "SELECT COALESCE(model_prob, fair_prob), result FROM picks WHERE result IN ('win','loss','push')").fetchall()
    c.close()
    if not rows:
        return []
    bins: dict[int, list[int]] = {}
    for p, res in rows:
        p = float(p or 0)
        b = min(n_bins - 1, int(p * n_bins))
        bins.setdefault(b, []).append(1 if res == "win" else 0)
    out = []
    for b in sorted(bins):
        hits = bins[b]
        lo, hi = b / n_bins, (b + 1) / n_bins
        out.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": len(hits),
                    "predicted": round((lo + hi) / 2, 3),
                    "actual": round(sum(hits) / len(hits), 3)})
    return out


def suggest_ev_threshold(path: Path | None = None) -> dict:
    """Self-tune: which min-EV cutoff would have maximized flat profit historically?"""
    c = _db(path)
    rows = c.execute(
        "SELECT ev, odds_taken, result FROM picks WHERE result IN ('win','loss')").fetchall()
    c.close()
    if not rows:
        return {"suggested_min_ev": 0.03, "reason": "no settled data yet, default 3%"}
    best = (0.03, float("-inf"))
    for thresh in [0.0, 0.02, 0.03, 0.05, 0.08, 0.10]:
        prof = sum((o - 1.0) if res == "win" else -1.0
                   for ev, o, res in rows if (ev or 0) >= thresh)
        if prof > best[1]:
            best = (thresh, prof)
    return {"suggested_min_ev": best[0], "flat_profit_u": round(best[1], 2),
            "reason": f"max flat profit over {len(rows)} settled picks"}
