"""Free network probe: which hosts resolve from this host (2026-09-28 tick context).

Distinguishes "odds host is blocked/intercepted" from "no network at all".
No API calls, no credits.
"""
import socket

HOSTS = [
    "api.the-odds-api.com",
    "github.com",
    "stats.nba.com",
]

for h in HOSTS:
    try:
        infos = socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        print(f"{h}: RESOLVED {ips}")
    except Exception as e:  # noqa: BLE001
        print(f"{h}: FAIL {type(e).__name__}: {e}")
