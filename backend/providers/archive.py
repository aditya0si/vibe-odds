"""Back-compat shim + backfill CLI — mechanics moved to ``core.providers.archive``.

Old import paths keep working during the migration window.
"""

from core.providers.archive import ARCH_DIR, manifest, store  # noqa: F401


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="usopen")
    ap.add_argument("--manifest", action="store_true")
    a = ap.parse_args()
    if a.manifest:
        for row in manifest():
            print(row)
    else:
        from dotenv import load_dotenv
        load_dotenv()
        from backend.providers import the_odds_api as prov
        for sk in prov.SPORT_GROUPS[a.group]:
            evs = prov.fetch_odds(sk)
            p = store(sk, evs, {"group": a.group, "status": prov.last_status()})
            print(f"{sk}: {len(evs)} events -> {p}")
