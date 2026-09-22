"""HTTP API core (sport-neutral, map step 10).

Write guards and read-only enforcement for evidence APIs: a public prediction
API must be read-only unless a keyed write is explicitly intended (the tennis
tracker poisons weights/policy when writes leak). Static + page serving:
``serve_frontend`` (adapter injection lands with the site core, map step 11).
"""

from __future__ import annotations

from pathlib import Path

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def require_write_key(x_api_key: str | None, write_key: str) -> None:
    """401 unless X-API-Key matches. Empty write_key = open (local dev)."""
    if not write_key:
        return  # local dev: open, but /api/health says auth:false
    if x_api_key != write_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="bad or missing X-API-Key")


def install_read_only(app, exempt: tuple[str, ...] = ()) -> None:
    """Mount the read-only guard: 405 on anything but GET/HEAD/OPTIONS.

    ``exempt`` path prefixes are the explicit write lanes (e.g. the keyed
    live-log settle endpoint) — everything else stays read-only.
    """

    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _guard(request, call_next):
        if request.method not in SAFE_METHODS and not any(
                request.url.path.startswith(x) for x in exempt):
            return JSONResponse({"detail": "read-only API"}, status_code=405)
        return await call_next(request)


def serve_frontend(app, front: Path | str, config: dict | None = None) -> None:
    """Mount /static + serve front/index.html at /.

    ``config`` (step 11): the sport adapter dict injected as window.__ADAPTER__
    before </head>. Without it the file is served byte-equal.
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    front = Path(front)
    if front.exists():
        app.mount("/static", StaticFiles(directory=str(front)), name="static")

    @app.get("/")
    def root():
        idx = front / "index.html"
        if not idx.exists():
            return {"ok": True, "hint": "frontend/index.html missing"}
        if config is None:
            return FileResponse(str(idx))
        from fastapi.responses import HTMLResponse
        from core.site import inject_adapter
        return HTMLResponse(inject_adapter(idx.read_text(encoding="utf-8"), config))
