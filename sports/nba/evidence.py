"""Evidence views (PATH step 7): the site/API renders THESE numbers only.

Every value is derived from the frozen ledgers or the as-ingested DB — nothing
on the site is hand-typed. Predictions shown anywhere are re-derived from the
published coefficients (formula_v1.json) and say so.
"""

from __future__ import annotations

import json
import sqlite3

from sports.nba.db.paths import DATA, DB

ARTIFACTS = {
    "formula": "formula_v1.json",
    "walkforward": "nba_walkforward_v1.json",
    "walkforward_contaminated": "nba_walkforward_v1_contaminated.json",
    "availability": "nba_availability_v1.json",
    "calibration": "nba_calibration_v1.json",
    "ablation": "nba_ablation_v1.json",
    "formula_report": "formula_v1_report.json",
}

TABS = ["Matches", "Teams", "Model", "Experiments", "Methodology"]

RE_DERIVED_NOTE = ("re-derived from the published coefficients (formula_v1.json) "
                   "on as-of features; deterministic, not a new fit")


def artifact(name: str) -> dict:
    return json.loads((DATA / ARTIFACTS[name]).read_text())


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    return con


def site_meta() -> dict:
    arts = []
    for k, f in ARTIFACTS.items():
        p = DATA / f
        entry = {"key": k, "file": f, "bytes": p.stat().st_size if p.exists() else None}
        if p.exists() and f.endswith(".json"):
            try:
                entry["generated_by"] = json.loads(p.read_text()).get("generated_by")
            except Exception:
                pass
        arts.append(entry)
    return {"sport": "nba", "tabs": TABS, "artifacts": arts,
            "scope": "regular-season moneyline winner (incl. OT); 2005-06..2025-26",
            "formula": "formula_v1.json (published coefficients)"}


def model_view() -> dict:
    w = artifact("walkforward")
    r = artifact("formula_report")
    return {"formula": w["formula"], "tiers": w["tiers"],
            "pooled_all_test": w["pooled_all_test"],
            "pooled_with_close": w["pooled_with_close"],
            "pooled_with_open": w["pooled_with_open"],
            "n_test_games": w["n_test_games"], "notes": w["notes"],
            "by_season": r["by_season"],
            "paired_vs_close": r["pooled_test_paired_formula_vs_market"],
            "tuning_paired": r["tuning_season_paired_formula_vs_market"]}


def experiments_view() -> dict:
    return {"availability": artifact("availability"),
            "calibration": artifact("calibration"),
            "ablation": artifact("ablation")}


def sim_view() -> dict:
    """The walk-forward ledger IS the NBA season-sim equivalent."""
    return artifact("walkforward")


def _team_names(con: sqlite3.Connection) -> dict:
    cols = [r["name"] for r in con.execute("PRAGMA table_info(teams)")]
    name_col = next((c for c in ("team_name", "full_name", "nickname", "abbr", "name")
                     if c in cols), None)
    id_col = next((c for c in ("team_id", "id") if c in cols), None)
    if not id_col:
        return {}
    out = {}
    for r in con.execute(f"SELECT * FROM teams"):
        out[r[id_col]] = r[name_col] if name_col else r[id_col]
    return out


def teams_view(limit: int = 30) -> dict:
    con = _con()
    try:
        names = _team_names(con)
        rows = con.execute("""
            SELECT team_id, SUM(w) AS wins, COUNT(*) AS games FROM (
                SELECT home_team_id AS team_id, (home_score > away_score) AS w
                FROM games WHERE LOWER(season_type) LIKE 'reg%'
                UNION ALL
                SELECT away_team_id AS team_id, (away_score > home_score) AS w
                FROM games WHERE LOWER(season_type) LIKE 'reg%')
            GROUP BY team_id ORDER BY wins DESC LIMIT ?""", (limit,)).fetchall()
        home = con.execute("""
            SELECT COUNT(*) n, SUM(home_score > away_score) hw FROM games
            WHERE LOWER(season_type) LIKE 'reg%'""").fetchone()
    finally:
        con.close()
    return {"home_win_rate": round(home["hw"] / home["n"], 4) if home["n"] else None,
            "n_games": home["n"],
            "teams": [{"team_id": r["team_id"], "team": names.get(r["team_id"], r["team_id"]),
                       "wins": r["wins"], "games": r["games"],
                       "win_rate": round(r["wins"] / r["games"], 4)} for r in rows]}


def _test_rows_and_labels():
    """(rows, labels) for the frozen test seasons, as-of features + real results."""
    from sports.nba.model.formula import TEST_SEASONS, load_dataset, predict  # noqa: F401
    con = _con()
    try:
        season = {r["game_id"]: r["season"] for r in
                  con.execute("SELECT game_id, season FROM games")}
        labels = {r["game_id"]: int(r["home_score"] > r["away_score"]) for r in
                  con.execute("SELECT game_id, home_score, away_score FROM games")
                  if r["home_score"] is not None and r["away_score"] is not None}
        rows = load_dataset(con, "v1")
    finally:
        con.close()
    from sports.nba.model.formula import TEST_SEASONS
    rows = [r for r in rows if season.get(r["game_id"]) in TEST_SEASONS]
    return rows, labels


def reliability_view(n_bins: int = 10) -> dict:
    from sports.nba.model.formula import predict
    rows, labels = _test_rows_and_labels()
    f = artifact("formula")
    ps = [predict(f, r) for r in rows]
    ys = [labels[r["game_id"]] for r in rows]
    bins = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        sel = [(p, y) for p, y in zip(ps, ys) if (lo <= p < hi) or (i == n_bins - 1 and p == 1.0)]
        bins.append({"lo": lo, "hi": hi, "n": len(sel),
                     "mean_p": round(sum(p for p, _ in sel) / len(sel), 4) if sel else None,
                     "rate": round(sum(y for _, y in sel) / len(sel), 4) if sel else None})
    ece = round(sum(b["n"] / len(ps) * abs(b["mean_p"] - b["rate"]) for b in bins if b["n"]), 4) if ps else None
    return {"n": len(ps), "bins": bins, "ece": ece,
            "note": RE_DERIVED_NOTE}


def predict_view(game_id: str) -> dict:
    from sports.nba.model.formula import load_dataset, predict
    con = _con()
    try:
        g = con.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
        rows = {r["game_id"]: r for r in load_dataset(con, "v1")}
    finally:
        con.close()
    if g is None:
        return {"error": "unknown game_id", "game_id": game_id}
    row = rows.get(game_id)
    if row is None:
        return {"error": "no as-of feature row (first games of a season lack history)",
                "game_id": game_id}
    f = artifact("formula")
    p_home = predict(f, row)
    return {"game_id": game_id, "date": g["game_date"],
            "home_team_id": g["home_team_id"], "away_team_id": g["away_team_id"],
            "result_home_win": int(g["home_score"] > g["away_score"]) if g["home_score"] is not None else None,
            "prob_home": round(p_home, 4), "pick": "home" if p_home >= 0.5 else "away",
            "formula": ARTIFACTS["formula"], "note": RE_DERIVED_NOTE}


def stats_view() -> dict:
    con = _con()
    try:
        counts = {t: con.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                  for t in ("games", "odds_snapshots", "features", "game_inactives",
                            "game_traditional", "game_officials")}
        seasons = [{"season": r["season"], "n": r["n"]} for r in
                   con.execute("SELECT season, COUNT(*) n FROM games GROUP BY season ORDER BY season")]
    finally:
        con.close()
    return {"db_counts": counts, "seasons": seasons,
            "artifacts": site_meta()["artifacts"]}
