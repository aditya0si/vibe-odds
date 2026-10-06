"""Free quota probe: GET /v4/sports/ costs 0 credits and returns x-requests-remaining/used/last.
Never prints the key. Safe to run any time.
"""
import os
import pathlib
import re

KEY = None
envf = pathlib.Path(".env")
if envf.exists():
    for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*(?:export\s+)?ODDS_API_KEY\s*=\s*(.+?)\s*$", line)
        if m:
            KEY = m.group(1).strip().strip("'\"")
            break
if not KEY:
    KEY = os.environ.get("ODDS_API_KEY")

print("key_found:", bool(KEY), "len:", len(KEY) if KEY else 0)
if not KEY:
    raise SystemExit("no ODDS_API_KEY available")

try:
    import requests
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"requests import failed: {exc!r}")

url = "https://api.the-odds-api.com/v4/sports/"
try:
    r = requests.get(url, params={"apiKey": KEY}, timeout=25)
    print("http_status:", r.status_code)
    for h in ("x-requests-remaining", "x-requests-used", "x-requests-last"):
        print(f"{h}: {r.headers.get(h)}")
    if r.status_code == 200:
        data = r.json()
        nba = [s for s in data if "basketball_nba" in str(s.get("key", ""))]
        print("nba_sports_keys:", [(s.get("key"), s.get("active"), s.get("has_outrights")) for s in nba])
    else:
        print("body_head:", r.text[:200])
except Exception as exc:
    # requests embeds query params in the exception URL -> redact before printing.
    msg = str(exc).replace(KEY, "***REDACTED***")
    msg = re.sub(r"apiKey=[^&\s'\"]+", "apiKey=***", msg)
    print("probe_error:", type(exc).__name__, msg[:300])
