"""Player-prop price capture into `prop_lines` (NBA 2026-27).

    python -m tools.props_capture capture --source sharpapi --dry-run
    python -m tools.props_capture capture --source sharpapi
    python -m tools.props_capture capture --source sportsgameodds --dump-raw
    python -m tools.props_capture report

WHY these sources (decided 2026-10-13, see core/providers/sharpapi.py):
  * sharpapi        — reachable from this network on the free tier (12 req/min,
                      2 books, 60s delay, pre-match only). Primary board.
  * sportsgameodds  — 9 books + settlement data, free tier includes props
                      (2,500 objects/month). NOT reachable locally (Sophos
                      gambling block), so it runs from a GitHub Actions runner.

Discipline this tool keeps (repo rules, not preferences):
  * PRE-TIP ONLY. A price captured at/after tip-off is a result, not a quote, so
    a row whose event start time is in the past is dropped as `post_tip`. This
    mirrors tools/t60_snapshot.py's require_pre_tip.
  * Nothing is invented. Missing line, missing price, placeholder prices (0, 1.0),
    suspended markets and unmappable shapes are DROPPED with a reason appended to
    sports/nba/data/props_dropped.jsonl — a board that shrinks silently is how a
    capture pipeline stops collecting without anyone noticing.
  * Raw payloads are archived at fetch time under sports/nba/data/raw/props/.
    Timestamps cannot be reconstructed afterwards, and a line ladder without the
    capture times is not evidence.
  * Re-running is idempotent: the prop_lines PK includes the line, so the same
    price twice is one row and a MOVED line is a new row (that is the ladder).

The game join: neither source knows our game_id, so rows are matched on
(home team, away team, date +/-1 day) against `games` after resolving team ids
from names/abbreviations. An unresolved row is dropped, never guessed onto a
nearby game.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):                    # script mode: repo importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.names import canonical as canonical_name          # noqa: E402
from core.providers import sharpapi, sportsgameodds          # noqa: E402
from sports.nba.db import build, paths                       # noqa: E402

PROPS_RAW = paths.RAW / "props"
DROPS_LOG = paths.DATA / "props_dropped.jsonl"

# Provider abbreviations that differ from the nba_api 3-letter codes in `teams`.
# Verified against the first live payload; an unknown code drops the row rather
# than matching a wrong team.
ABBR_ALIASES = {
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA",
    "WSH": "WAS", "PHO": "PHX", "BRK": "BKN", "CHO": "CHA", "NOR": "NOP",
    "LAC": "LAC", "LAL": "LAL",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------- game join
def resolve_team_id(con: sqlite3.Connection, name: str | None, abbr: str | None) -> int | None:
    """Resolve a source team name/abbreviation to our team_id, or None.

    Order: exact full_name, exact abbr, aliased abbr, then a unique
    last-word (nickname) match. Ambiguity is a miss, not a coin flip.
    """
    if abbr:
        code = ABBR_ALIASES.get(abbr.strip().upper(), abbr.strip().upper())
        row = con.execute("SELECT team_id FROM teams WHERE UPPER(abbr)=?", (code,)).fetchone()
        if row:
            return row["team_id"]
    if name:
        row = con.execute("SELECT team_id FROM teams WHERE LOWER(full_name)=LOWER(?)",
                          (name.strip(),)).fetchone()
        if row:
            return row["team_id"]
        nick = name.strip().split()[-1]
        rows = con.execute("SELECT team_id FROM teams WHERE LOWER(full_name) LIKE ?",
                           (f"%{nick.lower()}",)).fetchall()
        if len(rows) == 1:
            return rows[0]["team_id"]
    return None


def resolve_game_id(con: sqlite3.Connection, home: str | None, away: str | None,
                    home_abbr: str | None, away_abbr: str | None,
                    start_iso: str | None) -> str | None:
    """Match (home, away, date) to a game_id, or None."""
    start = _parse_iso(start_iso)
    if start is None:
        return None
    hid = resolve_team_id(con, home, home_abbr)
    aid = resolve_team_id(con, away, away_abbr)
    if not hid or not aid:
        return None
    d = start.date()
    for delta in (0, -1, 1):
        row = con.execute(
            "SELECT game_id FROM games WHERE game_date=? AND home_team_id=? AND away_team_id=?",
            ((d + timedelta(days=delta)).isoformat(), hid, aid)).fetchone()
        if row:
            return row["game_id"]
    return None


# --------------------------------------------------------------- store
PROP_COLUMNS = (
    "game_id", "source", "book", "player_name", "player_key", "market", "market_type",
    "side", "line", "price_decimal", "price_raw", "implied_prob", "is_alternate_line",
    "market_segment", "event_start_time", "source_ts", "flags", "captured_at",
    "snapshot_kind", "asof_ts",
)


def ensure_table(con: sqlite3.Connection) -> None:
    have = con.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='prop_lines'"
    ).fetchone()["n"]
    if not have:
        build.migrate(verbose=False, con=con)


def store_rows(con: sqlite3.Connection, rows: list[dict], *, dry_run: bool = False) -> int:
    """INSERT OR IGNORE the normalized rows; returns the number actually written.

    `total_changes` is the honest counter — `INSERT OR IGNORE` reports success
    even when the PK already held the row, so a row count before/after is the
    only way to tell an idempotent re-run from a real capture.
    """
    if dry_run or not rows:
        return 0
    before = con.total_changes
    sql = (f"INSERT OR IGNORE INTO prop_lines ({', '.join(PROP_COLUMNS)}) "
           f"VALUES ({', '.join('?' for _ in PROP_COLUMNS)})")
    for r in rows:
        con.execute(sql, tuple(r.get(c) for c in PROP_COLUMNS))
    con.commit()
    return con.total_changes - before


def write_drops(drops: list[dict]) -> None:
    if not drops:
        return
    DROPS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DROPS_LOG.open("a", encoding="utf-8") as fh:
        for d in drops:
            fh.write(json.dumps({**d, "dropped_at": now_iso()}, default=str) + "\n")


def archive_raw(source: str, payload: dict, out: Path | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = (out or PROPS_RAW) / f"{source}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------- capture
def prepare_rows(con: sqlite3.Connection, rows: list[dict], *, asof: str) -> tuple[list[dict], list[dict]]:
    """Attach game_id + player_key + asof_ts; split off the unpriced/unjoined rows."""
    kept: list[dict] = []
    rejected: list[dict] = []
    for r in rows:
        start = _parse_iso(r.get("event_start_time"))
        if start is not None and start <= datetime.now(timezone.utc):
            rejected.append({**r, "reason": "post_tip"})
            continue
        game_id = resolve_game_id(con, r.get("home_team"), r.get("away_team"),
                                  r.get("home_abbr"), r.get("away_abbr"),
                                  r.get("event_start_time"))
        if not game_id:
            rejected.append({**r, "reason": "unresolved_game"})
            continue
        kept.append({**r,
                     "game_id": game_id,
                     "player_key": canonical_name(r["player_name"]),
                     "asof_ts": asof})
    return kept, rejected


def capture(args: argparse.Namespace) -> int:
    asof = now_iso()

    # Fail fast on a missing key BEFORE opening the DB: a skipped capture must not
    # leave a schema migration or a write lock behind (nor make a test suite that
    # exercises the skip path touch the live store).
    provider = sharpapi if args.source == "sharpapi" else sportsgameodds
    if not provider.available():
        try:
            provider.api_key()
        except provider.MissingKey as exc:
            print(f"SKIP {args.source}: {exc}")
            return 2

    con = build.connect()
    ensure_table(con)

    if args.source == "sharpapi":
        try:
            payload = sharpapi.fetch_props(limit=args.limit, sportsbook=args.book)
        except sharpapi.MissingKey as exc:
            print(f"SKIP sharpapi: {exc}")
            return 2
        except sharpapi.AuthFailed as exc:
            print(f"AUTH FAILED sharpapi: {exc}")
            return 3
        except sharpapi.RateLimited as exc:
            print(f"RATE LIMITED sharpapi: {exc} — the next scheduled run picks it up")
            return 4
        rows, drops = sharpapi.normalize_rows(payload)
    else:
        try:
            payload = sportsgameodds.fetch_events(league=args.league, limit=args.limit)
        except sportsgameodds.MissingKey as exc:
            print(f"SKIP sportsgameodds: {exc}")
            return 2
        rows, drops = sportsgameodds.normalize_events(payload)

    raw_path = archive_raw(args.source, payload, Path(args.out) if args.out else None)
    kept, rejected = prepare_rows(con, rows, asof=asof)
    written = store_rows(con, kept, dry_run=args.dry_run)
    write_drops(drops + rejected)

    books = sorted({r["book"] for r in kept})
    players = sorted({r["player_key"] for r in kept})
    print(f"{args.source}: {len(rows)} priced rows -> {len(kept)} joined "
          f"-> {written} new / {len(kept) - written} already held"
          f"{' (dry run: nothing written)' if args.dry_run else ''}")
    print(f"  books: {', '.join(books) or '-'}")
    print(f"  players: {len(players)}  games: {len({r['game_id'] for r in kept})}")
    print(f"  dropped: {len(drops)} in-parse + {len(rejected)} at join; raw -> {raw_path}")
    if drops or rejected:
        reasons: dict[str, int] = {}
        for d in drops + rejected:
            reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        print("  reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    return 0


def report(_: argparse.Namespace) -> int:
    con = build.connect()
    ensure_table(con)
    n = con.execute("SELECT COUNT(*) AS n FROM prop_lines").fetchone()["n"]
    print(f"prop_lines: {n} rows")
    if not n:
        print("  (empty — run: python -m tools.props_capture capture --source sharpapi)")
        return 0
    for row in con.execute(
            "SELECT snapshot_kind, source, book, COUNT(*) AS n, COUNT(DISTINCT game_id) AS games, "
            "COUNT(DISTINCT player_key) AS players, MIN(line) AS min_line, MAX(line) AS max_line, "
            "MIN(asof_ts) AS first_ts, MAX(asof_ts) AS last_ts "
            "FROM prop_lines GROUP BY 1,2,3 ORDER BY 1,2,3"):
        print(f"  {row['snapshot_kind']:<6} {row['source']:<16} {row['book']:<18} "
              f"rows={row['n']:<6} games={row['games']:<4} players={row['players']:<4} "
              f"line {row['min_line']}..{row['max_line']}")
    print("\n  by market:")
    for row in con.execute("SELECT market, COUNT(*) AS n FROM prop_lines GROUP BY 1 ORDER BY n DESC"):
        print(f"    {row['market']:<18} {row['n']}")
    print(f"\n  drop log: {DROPS_LOG} ({DROPS_LOG.stat().st_size if DROPS_LOG.exists() else 0} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Capture NBA player-prop prices into prop_lines")
    sub = ap.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="fetch + store a prop board")
    cap.add_argument("--source", choices=("sharpapi", "sportsgameodds"), required=True)
    cap.add_argument("--league", default="NBA", help="sportsgameodds leagueID")
    cap.add_argument("--book", default=None, help="sharpapi single-book filter")
    cap.add_argument("--limit", type=int, default=200)
    cap.add_argument("--out", default=None, help="raw payload directory (default sports/nba/data/raw/props)")
    cap.add_argument("--dry-run", action="store_true", help="fetch + report, write nothing to the DB")
    cap.set_defaults(func=capture)

    rep = sub.add_parser("report", help="what prop_lines currently holds")
    rep.set_defaults(func=report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
