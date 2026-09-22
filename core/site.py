"""Site core (sport-neutral, map step 11).

Serves a page shell with the sport adapter config injected as
window.__ADAPTER__ before </head>: templates keep their sport flavor, the
wiring lives here. (The map's symlink / data-hermes-* verification items
targeted files that do not exist in this repo; the real contract is: the
template is served with the adapter JSON injected, idempotently.)
"""

from __future__ import annotations

import json
import re

_SCRIPT_RE = re.compile(r"<script>window\.__ADAPTER__ = .*?;</script>", re.S)


def inject_adapter(html: str, config: dict) -> str:
    """Insert ``<script>window.__ADAPTER__ = {...};</script>`` before </head>.

    Idempotent: re-injection replaces the previous block (a stale config must
    never survive a restart). Templates may freely READ window.__ADAPTER__ in
    their own scripts — only our exact script block is matched.
    """
    tag = f'<script>window.__ADAPTER__ = {json.dumps(config, sort_keys=True)};</script>'
    if _SCRIPT_RE.search(html):
        return _SCRIPT_RE.sub(lambda _m: tag, html, count=1)
    return html.replace("</head>", tag + "\n</head>", 1)
