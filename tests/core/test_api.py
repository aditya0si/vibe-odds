"""HTTP API core: write guard + read-only enforcement (map step 10).

Drives the ASGI app directly (no httpx in the venv — no new deps).
"""

from __future__ import annotations

import asyncio

import pytest


def _asgi(app, method: str = "GET", path: str = "/"):
    chunks, status = [], {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            status["s"] = m["status"]
        elif m["type"] == "http.response.body":
            chunks.append(m.get("body", b""))

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"t")], "client": ("127.0.0.1", 1),
             "server": ("t", 80), "root_path": "", "state": {}}
    asyncio.run(app(scope, receive, send))
    return status["s"], b"".join(chunks)


def test_require_write_key_contract():
    from fastapi import HTTPException
    from core.api import require_write_key
    require_write_key("anything", "")          # unset key = open (dev)
    require_write_key("k", "k")                # match passes
    with pytest.raises(HTTPException) as e:
        require_write_key("wrong", "k")
    assert e.value.status_code == 401
    with pytest.raises(HTTPException):
        require_write_key(None, "k")


def test_read_only_guard_405s_writes():
    from fastapi import FastAPI
    from core.api import install_read_only
    app = FastAPI()
    install_read_only(app)

    @app.get("/x")
    def x():
        return {"ok": True}

    @app.post("/x")
    def xp():
        return {"ok": False}

    assert _asgi(app, "GET", "/x")[0] == 200
    s, b = _asgi(app, "POST", "/x")
    assert s == 405 and b"read-only" in b
    assert _asgi(app, "DELETE", "/x")[0] == 405


def test_serve_frontend_serves_index_byte_equal(tmp_path):
    from fastapi import FastAPI
    from core.api import serve_frontend
    (tmp_path / "index.html").write_text("<html><head></head><body>hi</body></html>")
    app = FastAPI()
    serve_frontend(app, tmp_path)
    s, b = _asgi(app, "GET", "/")
    assert s == 200 and b == b"<html><head></head><body>hi</body></html>"
