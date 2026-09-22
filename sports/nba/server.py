"""NBA evidence server (PATH step 7): 5-tab site + read-only evidence API +
the keyed live-log write lane.

Run:  python -m sports.nba.server   (uvicorn, port 8010)
Everything served is derived — see sports/nba/evidence.py. The evidence API is
read-only by middleware; the ONLY write lane is POST /api/live/settle, keyed
by NBA_API_KEY (require_write_key: open in dev when unset).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException

load_dotenv()

from core.api import install_read_only, require_write_key, serve_frontend
from sports.nba import evidence as E
from sports.nba import live_log
from sports.nba.adapter import NbaAdapter
from sports.nba.db.paths import DATA, ROOT

app = FastAPI(title="NBA winner formula (evidence)")
SITE = ROOT / "sports" / "nba" / "site"
WRITE_KEY = os.getenv("NBA_API_KEY", "")
LOG = live_log.LOG_PATH   # module seam: tests redirect it (mirrors tennis VERDICTS_LOG)

serve_frontend(app, SITE, config=NbaAdapter().config)
install_read_only(app, exempt=("/api/live/settle",))


@app.get("/api/site_meta")
def site_meta():
    return E.site_meta()


@app.get("/api/model")
def model():
    return E.model_view()


@app.get("/api/experiments")
def experiments():
    return E.experiments_view()


@app.get("/api/sim")
def sim():
    return E.sim_view()


@app.get("/api/teams")
def teams(limit: int = 30):
    return E.teams_view(limit)


@app.get("/api/reliability")
def reliability(n_bins: int = 10):
    return E.reliability_view(n_bins)


@app.get("/api/predict")
def predict(game_id: str):
    return E.predict_view(game_id)


@app.get("/api/stats")
def stats():
    return E.stats_view()


@app.get("/api/board")
def board():
    """Tonight's slate = the live pre-tip log (the only forward-looking record)."""
    return {"board": live_log.board(out=LOG), "n": len(live_log.board(out=LOG))}


@app.get("/api/live/log")
def live_log_events():
    ev = live_log.events(out=LOG)
    return {"n_events": len(ev), "events": ev}


@app.post("/api/live/settle")
def live_settle(payload: dict, x_api_key: str | None = Header(default=None)):
    require_write_key(x_api_key, WRITE_KEY)
    try:
        gid = str(payload["game_id"])
        h, a = int(payload["home_score"]), int(payload["away_score"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail="need game_id, home_score, away_score")
    return live_log.settle(gid, h, a, out=LOG)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8010)
