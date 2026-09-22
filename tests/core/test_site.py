"""Site core: adapter injection contract (map step 11)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.site import inject_adapter


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


def test_inject_adapter_inserts_before_head_close():
    html = "<html><head><title>t</title></head><body></body></html>"
    out = inject_adapter(html, {"sport": "tennis", "n": 1})
    assert 'window.__ADAPTER__ = {"n": 1, "sport": "tennis"};' in out
    assert out.index("__ADAPTER__") < out.index("</head>")
    assert "<title>t</title>" in out and out.endswith("<body></body></html>")


def test_inject_adapter_is_idempotent():
    html = "<html><head></head></html>"
    once = inject_adapter(html, {"sport": "tennis"})
    twice = inject_adapter(once, {"sport": "tennis"})
    assert once == twice
    assert once.count("__ADAPTER__") == 1


def test_inject_adapter_replaces_stale_config():
    html = "<html><head></head></html>"
    old = inject_adapter(html, {"v": 1})
    new = inject_adapter(old, {"v": 2})
    assert '"v": 2' in new and '"v": 1' not in new


def test_inject_adapter_survives_adapter_js_mentions():
    """A template that READS window.__ADAPTER__ must still get the script."""
    html = ("<html><head></head><body>"
            "<script>var c = window.__ADAPTER__;</script></body></html>")
    out = inject_adapter(html, {"sport": "tennis"})
    assert out.count("__ADAPTER__") == 2  # our script + the page's read


def test_homepage_serves_adapter_config():
    from fastapi import FastAPI
    from backend.api.adapter import TennisAdapter
    from core.api import serve_frontend
    front = Path(__file__).resolve().parents[2] / "frontend"
    app = FastAPI()
    serve_frontend(app, front, config=TennisAdapter().config)
    s, b = _asgi(app, "GET", "/")
    assert s == 200
    assert b"window.__ADAPTER__" in b
    assert b'"sport": "tennis"' in b
    assert b"ATP tennis analytics" in b   # page body intact
