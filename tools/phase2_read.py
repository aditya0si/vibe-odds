"""The single 2026-27 claim read (plan Task 9) - code now, run ONCE at season end.

    python tools/phase2_read.py --confirm-final

Scores the SEALED arm (nba_phase2_arm_2026_27.json, digest-verified) on the
settled 2026-27 regular season and writes nba_phase2_read_2026_27.json with the
pre-registered tiers (docs/preregistration.md 13):

  T2'  formula@T-60 vs the opening price
  T2'' formula@T-60 vs the market at T-60 (same-information fairness)
  T3   formula@T-60 vs the closing price

Verdict rule is the registered one (formula.py): pass iff mean_diff > 0 and the
blocked 95% CI lies entirely above zero. T2g/T3g report "not-run, not failed".
A published null is a first-class endpoint. Run it ONCE: the tool refuses to run
without --confirm-final, refuses a tampered arm, and refuses unsettled games.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):                   # script mode (cron / manual): repo importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build, paths
from sports.nba.model import formula as F
from sports.nba.model.phase2 import A7_FEATURES
from tools.season_gate import ARM_PATH, CLAIM_SEASON, arm_digest

READ_PATH = paths.DATA / "nba_phase2_read_2026_27.json"
NOT_RUN = "not-run, not failed"


def tier_verdict(st: dict) -> bool:
    """The registered rule: pass iff the blocked 95% CI lies entirely above zero."""
    return bool(st.get("n") and st.get("mean_diff") is not None
                and st["mean_diff"] > 0 and st["ci95_blocked"][0] > 0)


def verify_arm(arm: dict) -> None:
    if arm_digest(arm) != arm.get("digest"):
        raise RuntimeError("sealed arm digest mismatch - refusing to score against it")


def read_record(digest: str, tiers: dict, n_games: dict) -> dict:
    """The artifact builder: a prospective read IS claim-eligible (unlike retro)."""
    return {
        "claim_eligible": True,
        "read_once": True,
        "season": CLAIM_SEASON,
        "sealed_arm_digest": digest,
        "n_games": n_games,
        "tiers": tiers,
        "g_tier": {"T2g": NOT_RUN, "T3g": NOT_RUN},
        "extension_rule": "2027-28: same read, arm refit through 2026-27 per prereg 13",
    }


def score(season_rows: list[dict], formula: dict,
          market: dict[str, dict[str, float]], label: str = "formula@T-60") -> dict:
    """Score the sealed arm against open / market@T-60 / close. Pure core (testable)."""
    labels = {r["game_id"]: r["home_win"] for r in season_rows}
    dates = {r["game_id"]: r["game_date"] for r in season_rows}
    p_formula = {r["game_id"]: F.predict(formula, r) for r in season_rows}

    def tier(name: str, kind: str) -> dict:
        tgt = market[kind]
        games = [g for g in p_formula if g in tgt]
        st = F.paired_stats(p_formula, tgt, labels, games, dates)
        return {"tier": name, "n_games": st.get("n"), "formula_vs": st,
                "passes": tier_verdict(st)}

    return {
        "T2_prime_formula_at_T60_vs_opening": tier("T2'", "open"),
        "T2_double_prime_formula_at_T60_vs_market_at_T60": tier("T2''", "t60"),
        "T3_formula_at_T60_vs_closing": tier("T3", "close"),
    }


def read(con, arm: dict, confirm_final: bool = False, out=None) -> dict:
    """The once-only read. Everything here is guard-railed."""
    verify_arm(arm)
    if not confirm_final:
        raise RuntimeError("this is the single claim read: only --confirm-final at season end runs it")
    rows = [r for r in F.load_dataset(con, version="v3", feature_names=A7_FEATURES)
            if r["season"] == CLAIM_SEASON]
    if not rows:
        raise RuntimeError(f"no settled {CLAIM_SEASON} rows")
    unsettled = [r["game_id"] for r in rows if r.get("home_win") is None]
    if unsettled:
        raise RuntimeError(f"{len(unsettled)} unsettled {CLAIM_SEASON} game(s) - the read waits for season end")
    market = {kind: F.market_probs(con, kind) for kind in ("open", "t60", "close")}
    tiers = score(rows, arm["formula"], market)
    n_games = {"all": len(rows),
               "with_open": tiers["T2_prime_formula_at_T60_vs_opening"]["n_games"],
               "with_t60": tiers["T2_double_prime_formula_at_T60_vs_market_at_T60"]["n_games"],
               "with_close": tiers["T3_formula_at_T60_vs_closing"]["n_games"]}
    rec = read_record(arm["digest"], tiers, n_games)
    (out or READ_PATH).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main() -> int:
    confirm = "--confirm-final" in sys.argv
    arm = json.loads(ARM_PATH.read_text(encoding="utf-8"))
    rec = read(build.init(verbose=False), arm, confirm_final=confirm)
    for name, t in rec["tiers"].items():
        st = t["formula_vs"]
        print(f"{name}: n={t['n_games']} mean_diff={st.get('mean_diff')} "
              f"ci95={st.get('ci95_blocked')} passes={t['passes']}")
    print(f"g-tier: T2g/T3g {NOT_RUN}")
    print(f"-> {READ_PATH.name} (the single {CLAIM_SEASON} claim read)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
