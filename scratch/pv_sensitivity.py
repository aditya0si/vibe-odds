"""R3 iteration 2: A9b sensitivity to the frozen player-value constants (lambda, K, m).

Pre-stated protocol (docs/phase3-candidates.md, R3):
  * one-at-a-time around the frozen constants lambda=3.0 / K=8 / m=16.0;
  * variants: lambda in {1, 10, 30}; K in {6, 12}; m in {8, 32};
  * metric: the A9b arm (A7 features + pv_*) fitted <= 2020-21, scored on the BURNED
    window 2022-23..2025-26. The burned window may be used for selection (its results
    can never be claimed); the tune season is NOT - it is reserved for one-shot gates;
  * rule (fixed a priori): a variant is MATERIAL iff its paired CI vs the frozen arm
    excludes zero AND the mean improvement is >= 0.0010 Brier; otherwise the frozen
    constants stand and the robustness is documented. No hyperparameter shopping:
    a material variant would be pre-stated as a NEW arm and gated once.

Implementation warning: LAMBDA_GRID / ARCH_K / ARCH_SHRINK are bound as DEFAULT
ARGUMENTS at function-definition time, so monkeypatching the module constants is a
silent no-op. The variants are forced through wrappers, and a fingerprint canary
proves the variants actually differ (a wrapper that failed to take would produce
identical fingerprints).

Output: sports/nba/data/nba_phase3_sensitivity_a9.json (labeled burned/claim_eligible
false) + a console table.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build, paths
from sports.nba.features import player_value as PV
from sports.nba.model import formula as F
from sports.nba.model import phase3 as P3
from sports.nba.model import retro as RETRO

OUT_PATH = paths.DATA / "nba_phase3_sensitivity_a9.json"

# (knob, value) - one-at-a-time around the frozen constants.
VARIANTS = [("lambda", 1.0), ("lambda", 10.0), ("lambda", 30.0),
            ("K", 6), ("K", 12),
            ("m", 8.0), ("m", 32.0)]
FROZEN = {"lambda": 3.0, "K": 8, "m": 16.0}
RULE = ("material iff the paired CI vs the frozen arm excludes zero AND the mean "
        "improvement is >= 0.0010 Brier; otherwise the frozen constants stand "
        "(robustness documented). A material variant would be pre-stated as a NEW "
        "arm and gated once - never shopped.")


def fingerprint(pv_rows: dict) -> str:
    h = hashlib.sha256()
    for gid in sorted(pv_rows):
        row = pv_rows[gid]
        h.update(f"{gid}:{row.get('pv_missing_diff')}:{row.get('pv_share_home')}:"
                 f"{row.get('pv_top_out_home')}".encode())
    return h.hexdigest()[:12]


def merged_from(con, pv_rows: dict, feature_names) -> list[dict]:
    """P3.load_merged_dataset, but against an in-memory v5 payload dict."""
    v3: dict[str, dict] = {}
    for r in con.execute("SELECT payload FROM features WHERE feature_version='v3'"):
        row = json.loads(r["payload"])
        v3[row["game_id"]] = row
    out: list[dict] = []
    for gid, row in pv_rows.items():
        base = v3.get(gid)
        if base is None:
            continue
        merged = {**base, **row}
        merged["x"] = {name: F._feat(merged, name) for name in feature_names}
        if any(v is None for v in merged["x"].values()):
            continue
        out.append(merged)
    out.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return out


def arm_probs(rows: list[dict], feature_names) -> tuple[float, dict, list[str]]:
    """Fit <= 2020-21, score the burned window -> (brier, probs, games)."""
    train = [r for r in rows if r["season"] <= F.TRAIN_END]
    retro_rows = [r for r in rows if r["season"] in RETRO.RETRO_SEASONS]
    formula = F.fit_formula(train, tuple(feature_names))
    probs = {r["game_id"]: F.predict(formula, r) for r in retro_rows}
    labels = {r["game_id"]: r["home_win"] for r in retro_rows}
    games = [r["game_id"] for r in retro_rows]
    brier = sum((probs[g] - labels[g]) ** 2 for g in games) / len(games)
    return brier, probs, games


def build_variant(con, knob: str, value) -> dict:
    """build_player_value under a forced knob value (wrappers - see module docstring)."""
    orig_cv, orig_shrink = PV.time_blocked_cv, PV.archetype_shrink
    try:
        if knob == "lambda":
            def cv(X, y, grid=None, folds=None, _lam=value):
                return orig_cv(X, y, grid=(_lam,), folds=PV.CV_FOLDS)
            PV.time_blocked_cv = cv
        elif knob == "K":
            def shrink(V, exposure, profiles, prior, _k=value):
                return orig_shrink(V, exposure, profiles, prior, k=_k,
                                   seed=PV.ARCH_SEED, m_shrink=PV.ARCH_SHRINK,
                                   min_minutes=PV.ARCH_MIN_MINUTES)
            PV.archetype_shrink = shrink
        elif knob == "m":
            def shrink(V, exposure, profiles, prior, _m=value):
                return orig_shrink(V, exposure, profiles, prior, k=PV.ARCH_K,
                                   seed=PV.ARCH_SEED, m_shrink=_m,
                                   min_minutes=PV.ARCH_MIN_MINUTES)
            PV.archetype_shrink = shrink
        else:
            raise ValueError(knob)
        return PV.build_player_value(con)
    finally:
        PV.time_blocked_cv, PV.archetype_shrink = orig_cv, orig_shrink


def main() -> int:
    con = build.init(verbose=False)

    # Reference: the frozen v5 features exactly as shipped.
    ref_rows = P3.load_merged_dataset(con, P3.A9B_FEATURES)
    ref_brier, p_ref, ref_games = arm_probs(ref_rows, P3.A9B_FEATURES)
    ref_pv = {}
    for r in con.execute("SELECT payload FROM features WHERE feature_version='v5'"):
        row = json.loads(r["payload"])
        ref_pv[row["game_id"]] = row
    ref_fp = fingerprint(ref_pv)
    print(f"reference (frozen lambda=3/K=8/m=16): burned Brier {ref_brier:.5f} on "
          f"{len(ref_games)} games; fingerprint {ref_fp}")

    labels = {r["game_id"]: r["home_win"] for r in ref_rows
              if r["season"] in RETRO.RETRO_SEASONS}
    dates = {r["game_id"]: r["game_date"] for r in ref_rows
             if r["season"] in RETRO.RETRO_SEASONS}

    variants_out = []
    seen_fp: dict[str, str] = {"__frozen__": ref_fp}
    for knob, value in VARIANTS:
        pv_rows = build_variant(con, knob, value)
        fp = fingerprint(pv_rows)
        dup = seen_fp.get(fp)
        if dup is not None:
            print(f"  !! FINGERPRINT COLLISION {fp}: {knob}={value} matches {dup} - wrapper suspect")
        seen_fp[fp] = f"{knob}={value}"
        rows = merged_from(con, pv_rows, P3.A9B_FEATURES)
        brier, probs, games = arm_probs(rows, P3.A9B_FEATURES)
        block = [g for g in ref_games if g in probs]
        paired = F.paired_stats(probs, p_ref, labels, block, dates)
        variants_out.append({
            "knob": knob,
            "value": value,
            "fingerprint": fp,
            "n_retro": len(games),
            "brier": round(brier, 5),
            "paired_vs_frozen": paired,
        })
        print(f"  {knob}={value}: burned Brier {brier:.5f}  vs frozen "
              f"mean_diff {paired.get('mean_diff')} ci95 {paired.get('ci95_blocked')} "
              f"n {paired.get('n')}  fp {fp}")

    material = [v for v in variants_out
                if v["paired_vs_frozen"].get("ci95_blocked")
                and v["paired_vs_frozen"]["ci95_blocked"][0] > 0
                and v["paired_vs_frozen"]["mean_diff"] >= 0.0010]
    worse = [v for v in variants_out
             if v["paired_vs_frozen"].get("ci95_blocked")
             and v["paired_vs_frozen"]["ci95_blocked"][1] < 0]
    if material:
        outcome = ("MATERIAL: " + "; ".join(f"{v['knob']}={v['value']} "
                   f"(+{v['paired_vs_frozen']['mean_diff']})" for v in material)
                   + " - pre-state as a new arm and gate once.")
    else:
        outcome = ("FLAT: no variant's paired CI excludes zero with a >= 0.0010 improvement; "
                   "the frozen constants stand. " +
                   ("Load-bearing only in the bad direction: " +
                    "; ".join(f"{v['knob']}={v['value']} ({v['paired_vs_frozen']['mean_diff']})"
                              for v in worse) + ". " if worse else "") +
                   "Sensitivity documented; nothing changes.")

    rec = {
        "burned_test_set": True,
        "claim_eligible": False,
        "label": RETRO.LABEL,
        "what": ("R3 iteration 2 - A9b arm sensitivity to the frozen player-value constants "
                 "(one-at-a-time around lambda=3.0 / K=8 / m=16.0)."),
        "arm": "A9b (A7 features + pv_*); informational variant, never gates",
        "fit": {"train_end": F.TRAIN_END, "scored_seasons": list(RETRO.RETRO_SEASONS)},
        "n_retro": len(ref_games),
        "frozen_constants": FROZEN,
        "reference_brier": round(ref_brier, 5),
        "variants": variants_out,
        "rule": RULE,
        "outcome": outcome,
    }
    OUT_PATH.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(f"outcome: {outcome}")
    print(f"-> {OUT_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
