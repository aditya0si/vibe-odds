import { test } from "node:test";
import assert from "node:assert/strict";
import { loadModule } from "./load-module.mjs";

const C = await loadModule("frontend/tennis-contract.js");

const HEALTH_OK = { ok: true, has_key: true };

function card(over = {}) {
  return {
    id: "E1",
    label: "Player A vs Player B",
    commence_time: "2030-01-01T00:00:00Z",
    n_books: 4,
    pick: "Player A",
    conf: 0.6,
    model_prob_a: 0.6,
    fair_source: "pinnacle",
    outcomes: {
      "Player A": { model_prob: 0.6, model_prob_free: 0.6, fair_prob: 0.58, best_odds: 1.8, best_book: "Pinnacle", edge: 0.05, kelly_half: 0.03, field_median: 1.75, n_quotes: 4 },
      "Player B": { model_prob: 0.4, model_prob_free: 0.4, fair_prob: 0.42, best_odds: 2.1, best_book: "Pinnacle", edge: null, kelly_half: 0, field_median: 2.05, n_quotes: 4 },
    },
    signals: {},
    reasons: [],
    unknown_players: [],
    flags: {},
    status: { started: false, thin: false, bettable: true },
    ...over,
  };
}

function board(over = {}) {
  const matches = over.matches !== undefined ? over.matches : [card()];
  return { group: "usopen", feed: "live", cache_age_s: 10, matches, summary: { n: matches.length, value: 0, disagree: 0 }, ...over };
}

test("mock_with_key_is_blocked", () => {
  const p = C.parseBoard(board({ feed: "mock" }), { fetchedAt: 1, health: HEALTH_OK });
  assert.equal(p.kind, "mock", "mock must stay blocked even with has_key=true");
  assert.equal(p.provenance.live, false);
  assert.equal(p.provenance.unverified, false);
});

test("missing_key_live_payload_is_unverified", () => {
  const p = C.parseBoard(board({ feed: "live" }), { fetchedAt: 1, health: { ok: true, has_key: false } });
  assert.equal(p.kind, "unverified");
  assert.equal(p.provenance.unverified, true);
  assert.equal(p.provenance.live, false);
});

test("unknown_feed_is_unverified", () => {
  for (const feed of ["init", null, undefined, "weird"]) {
    const p = C.parseBoard(board({ feed }), { fetchedAt: 1, health: HEALTH_OK });
    assert.equal(p.kind, "unverified", `feed=${String(feed)} must be unverified`);
  }
  // Health absent/inconsistent also downgrades a live claim.
  assert.equal(C.parseBoard(board({ feed: "live" }), { fetchedAt: 1 }).kind, "unverified");
  assert.equal(C.parseBoard(board({ feed: "live" }), { fetchedAt: 1, health: { ok: false, has_key: true } }).kind, "unverified");
});

test("cache_age_not_fetch_age", () => {
  const p = C.parseBoard(board({ feed: "cache", cache_age_s: 5 }), { fetchedAt: 9_999_999_999, health: HEALTH_OK });
  assert.equal(p.kind, "ready");
  assert.equal(p.provenance.cacheAgeS, 5, "cache age is the server value, not the fetch time");
  assert.equal(p.provenance.cached, true);
  assert.equal(p.stale, false);
});

test("age_60_boundary", () => {
  const fresh = C.parseBoard(board({ feed: "cache", cache_age_s: 60 }), { health: HEALTH_OK });
  assert.equal(fresh.stale, false, "age exactly 60 is NOT stale");
  assert.equal(fresh.freshness, "fresh");
  const stale = C.parseBoard(board({ feed: "cache", cache_age_s: 60.1 }), { health: HEALTH_OK });
  assert.equal(stale.stale, true, "age > 60 is stale");
  assert.equal(stale.freshness, "stale");
});

test("negative_or_null_age_unknown", () => {
  for (const age of [-1, null, undefined, NaN, "5"]) {
    const c = C.classifyAge(age);
    assert.equal(c.freshness, "unknown", `age=${String(age)} must be Unknown`);
    assert.equal(c.stale, false, "unknown age is never labelled stale");
  }
  assert.equal(C.isStaleAge(60), false);
  assert.equal(C.isStaleAge(61), true);
  assert.equal(C.isStaleAge(null), false);
});

test("demo_never_live_counts", () => {
  const d = C.buildDemoBoard();
  assert.equal(d.kind, "demo");
  assert.equal(d.liveCount, 0, "demo rows never enter live counts");
  assert.equal(d.provenance.live, false);
  assert.equal(d.provenance.demo, true);
  assert.equal(d.rows.length, 1);
});

test("exactly_two_outcomes", () => {
  const one = card({ outcomes: { "Player A": { model_prob: 0.5, model_prob_free: 0.5, fair_prob: 0.5 } } });
  const p = C.parseBoard(board({ matches: [one] }), { health: HEALTH_OK });
  assert.equal(p.kind, "invalid");
  assert.equal(p.rejected, 1);

  const three = card({
    outcomes: {
      A: { model_prob: 0.4, model_prob_free: 0.4, fair_prob: 0.4 },
      B: { model_prob: 0.3, model_prob_free: 0.3, fair_prob: 0.3 },
      C: { model_prob: 0.3, model_prob_free: 0.3, fair_prob: 0.3 },
    },
    pick: "A",
  });
  assert.equal(C.parseBoard(board({ matches: [three] }), { health: HEALTH_OK }).kind, "invalid");
});

test("pick_must_be_outcome", () => {
  const bad = card({ pick: "Nobody" });
  const p = C.parseBoard(board({ matches: [bad] }), { health: HEALTH_OK });
  assert.equal(p.kind, "invalid");
  assert.equal(p.rejected, 1);
});

test("predict_event_id_mismatch", () => {
  const payload = {
    event: { id: "first-event", label: "A vs B", commence_time: "2030-01-01T00:00:00Z" },
    verdict: { pick: "A" },
    market: { A: { model_prob: 0.6 }, B: { model_prob: 0.4 } },
    feed: "live",
  };
  const r = C.parsePrediction(payload, "requested-other");
  assert.equal(r.kind, "invalid", "backend first-event fallback must be rejected");
  const ok = C.parsePrediction(payload, "first-event");
  assert.equal(ok.kind, "ready");
  assert.equal(C.parsePrediction({ error: "no events" }, "x").kind, "invalid");
});

test("tennis_error_card_not_prediction", () => {
  const errCard = { id: "e1", label: "x vs y", commence_time: "2030-01-01T00:00:00Z", n_books: 2, error: "needs exactly 2 outcomes", outcomes: ["x", "y"] };
  const p = C.parseBoard(board({ matches: [errCard] }), { health: HEALTH_OK });
  assert.equal(p.rows.length, 0, "an error card is never a prediction");
  assert.equal(p.errors.length, 1);
  assert.equal(p.liveCount, 0);
  assert.equal(p.kind, "empty");
});

test("equal_count_bins_not_nba_bins", () => {
  const r = C.parseReliability({ n: 100, bins: [{ p_mean: 0.1, frac_pos: 0.2, n: 10 }], ece_oof: 0.02, note: "x" });
  assert.equal(r.kind, "ready");
  assert.equal(r.bins[0].pMean, 0.1);
  assert.equal(r.bins[0].fracPos, 0.2);
  assert.equal("lo" in r.bins[0], false, "tennis reliability has no lo/hi boundaries");
  assert.equal("hi" in r.bins[0], false);
  assert.equal(r.boundaries, "equal-count");
});

test("tennis_missing_model_artifact", () => {
  const m = C.parseModel({ sim: null, calibration_validation: null, ablation: { error: "not built" } });
  assert.equal(m.kind, "ready");
  assert.equal(m.sim, null, "missing artifact stays null, not zero");
  assert.equal(m.calibration, null);
  assert.ok(m.ablation && m.ablation.error);
});

// ---- Repair R1 additions ---------------------------------------------------

test("health_ok_missing_fails_closed", () => {
  const p = C.parseBoard(board({ feed: "live" }), { fetchedAt: 1, health: { has_key: true } });
  assert.equal(p.kind, "unverified", "a missing health.ok cannot license a live claim");
  assert.equal(p.provenance.live, false);
});

test("provider_mode_mock_init_error_fails_closed", () => {
  for (const mode of ["mock", "init", "error"]) {
    const p = C.parseBoard(board({ feed: "live" }), { fetchedAt: 1, health: { ok: true, has_key: true, provider: { mode } } });
    assert.equal(p.kind, "unverified", `provider.mode=${mode} must not license live`);
    assert.equal(p.provenance.live, false);
  }
  const ok = C.parseBoard(board({ feed: "live" }), { fetchedAt: 1, health: { ok: true, has_key: true, provider: { mode: "live" } } });
  assert.equal(ok.kind, "ready");
  assert.equal(ok.provenance.live, true);
});

test("cache_with_provider_mock_fails_closed", () => {
  const p = C.parseBoard(board({ feed: "cache", cache_age_s: 5 }), { fetchedAt: 1, health: { ok: true, has_key: true, provider: { mode: "mock" } } });
  assert.equal(p.kind, "unverified", "a contradictory provider mode fails a cached claim too");
});

test("meaningful_time_is_strict", () => {
  assert.equal(C.meaningfulTime("2030-01-01T18:00:00Z"), true);
  assert.equal(C.meaningfulTime("2030-01-01T18:00:00+14:00"), true);
  assert.equal(C.meaningfulTime("2024-02-29T00:00:00Z"), true);
  for (const bad of [
    "1", "2030-02-30T18:00:00Z", "2023-02-29T00:00:00Z",
    "2030-01-01T25:00:00Z", "2030-01-01T18:60:00Z", "2030-01-01T18:00:00",
    "2030-13-01T00:00:00Z", "garbage",
  ]) {
    assert.equal(C.meaningfulTime(bad), false, `time=${bad} must be rejected`);
  }
});

test("board_rejects_impossible_commence_time", () => {
  const bad = card({ commence_time: "2030-02-30T00:00:00Z" });
  const p = C.parseBoard(board({ matches: [bad] }), { health: HEALTH_OK });
  assert.equal(p.kind, "invalid");
  assert.equal(p.rejected, 1);
  assert.equal(C.parseBoard(board({ matches: [card({ commence_time: "1" })] }), { health: HEALTH_OK }).kind, "invalid");
});

test("conf_and_model_prob_a_range_enforced", () => {
  for (const bad of [{ conf: 42 }, { conf: -0.1 }, { conf: 1.01 }, { model_prob_a: 1.5 }, { model_prob_a: -1 }, { model_prob_a: NaN }]) {
    const p = C.parseBoard(board({ matches: [card(bad)] }), { health: HEALTH_OK });
    assert.equal(p.kind, "invalid", `${JSON.stringify(bad)} must be rejected, not clamped`);
    assert.equal(p.rejected, 1);
  }
});

test("outcome_probability_fields_must_be_finite_in_range", () => {
  const bad = card({
    outcomes: {
      "Player A": { model_prob: 1.2, model_prob_free: 0.6, fair_prob: 0.58 },
      "Player B": { model_prob: 0.4, model_prob_free: 0.4, fair_prob: 0.42 },
    },
  });
  assert.equal(C.parseBoard(board({ matches: [bad] }), { health: HEALTH_OK }).kind, "invalid");

  const nulled = card({
    outcomes: {
      "Player A": { model_prob: null, model_prob_free: 0.6, fair_prob: 0.58 },
      "Player B": { model_prob: 0.4, model_prob_free: 0.4, fair_prob: 0.42 },
    },
  });
  assert.equal(C.parseBoard(board({ matches: [nulled] }), { health: HEALTH_OK }).kind, "invalid", "null is not contract-permitted for model_prob");
});

test("model_prob_a_absent_is_permitted", () => {
  const c = card();
  delete c.model_prob_a;
  const p = C.parseBoard(board({ matches: [c] }), { health: HEALTH_OK });
  assert.equal(p.kind, "ready");
  assert.equal(p.rows[0].modelProbA, null);
});

test("predict_same_id_malformed_rejected", () => {
  const good = {
    event: { id: "e1", label: "A vs B", commence_time: "2030-01-01T00:00:00Z" },
    verdict: { pick: "A", conf: 0.6, model_prob_a: 0.6 },
    market: { A: { model_prob: 0.6 }, B: { model_prob: 0.4 } },
    feed: "live",
  };
  assert.equal(C.parsePrediction(good, "e1").kind, "ready");

  const cases = [
    { market: { A: { model_prob: 1.4 }, B: { model_prob: 0.4 } } },
    { market: { A: {}, B: { model_prob: 0.4 } } },
    { market: { A: { model_prob: "0.6" }, B: { model_prob: 0.4 } } },
    { verdict: { pick: "A", conf: 42 } },
    { verdict: { pick: "A", model_prob_a: -0.2 } },
    { event: { id: "e1", label: "A vs B", commence_time: "2030-02-30T00:00:00Z" } },
    { event: { id: "e1", label: "", commence_time: "2030-01-01T00:00:00Z" } },
    { market: { A: { model_prob: 1 } } },
    { verdict: { pick: "C" } },
    { feed: "mock" },
    { feed: "init" },
    { feed: null },
  ];
  for (const over of cases) {
    const r = C.parsePrediction({ ...good, ...over }, "e1");
    assert.equal(r.kind, "invalid", `same-id payload ${JSON.stringify(over)} must not become ready`);
  }
});

test("predict_market_present_fair_prob_must_be_probability", () => {
  const good = {
    event: { id: "e1", label: "A vs B", commence_time: "2030-01-01T00:00:00Z" },
    verdict: { pick: "A", conf: 0.6, model_prob_a: 0.6 },
    market: { A: { model_prob: 0.6, model_prob_free: 0.6 }, B: { model_prob: 0.4, model_prob_free: 0.4 } },
    feed: "live",
  };
  // The real /api/predict market omits fair_prob; its absence must stay ready.
  assert.equal(C.parsePrediction(good, "e1").kind, "ready");
  for (const bad of [42, -0.1, 1.01, NaN, "0.5"]) {
    const p = {
      ...good,
      market: {
        A: { model_prob: 0.6, model_prob_free: 0.6, fair_prob: bad },
        B: { model_prob: 0.4, model_prob_free: 0.4, fair_prob: 0.42 },
      },
    };
    assert.equal(C.parsePrediction(p, "e1").kind, "invalid", `present fair_prob=${String(bad)} must be rejected, not become ready`);
  }
});

test("predict_all_present_probability_and_confidence_fields_validated", () => {
  const base = {
    event: { id: "e1", label: "A vs B", commence_time: "2030-01-01T00:00:00Z" },
    verdict: { pick: "A", conf: 0.6, model_prob_a: 0.6, model_prob_cal_a: 0.6, model_prob_free_a: 0.6, model_prob_pick: 0.6 },
    market: { A: { model_prob: 0.6, model_prob_free: 0.6 }, B: { model_prob: 0.4, model_prob_free: 0.4 } },
    feed: "live",
  };
  assert.equal(C.parsePrediction(base, "e1").kind, "ready", "a full real-contract verdict is ready");
  const bads = [
    { verdict: { pick: "A", model_prob_cal_a: 1.2 } },
    { verdict: { pick: "A", model_prob_free_a: -0.1 } },
    { verdict: { pick: "A", model_prob_pick: NaN } },
    { verdict: { pick: "A", model_prob_pick: 42 } },
    { market: { A: { model_prob: 1.5, model_prob_free: 0.6 }, B: { model_prob: 0.4, model_prob_free: 0.4 } } },
    { market: { A: { model_prob: 0.6, model_prob_free: 1.2 }, B: { model_prob: 0.4, model_prob_free: 0.4 } } },
  ];
  for (const over of bads) {
    assert.equal(C.parsePrediction({ ...base, ...over }, "e1").kind, "invalid", `${JSON.stringify(over)} must not become ready`);
  }
});

test("tennis_players_and_canonical_detail", () => {
  const pl = C.parsePlayers({ surface: "clay", players: [{ name: "Carlos Alcaraz", matches: 120, overall: 2100, hard: 2050, clay: 2150, grass: 1950, blended: 2150, cold_start: false }] });
  assert.equal(pl.kind, "ready");
  assert.equal(pl.surface, "clay");
  assert.equal(pl.players[0].clay, 2150);

  const unknown = C.parsePlayerDetail({ name: "No Such Player", unknown: true, note: "no rating" });
  assert.equal(unknown.kind, "unknown");
  assert.equal(unknown.name, "No Such Player");

  const detail = C.parsePlayerDetail({
    name: "Carlos Alcaraz", unknown: false, matches: 120, overall: 2100,
    surfaces: { hard: { rating: 2050, n: 60 }, clay: { rating: 2150, n: 40 }, grass: { rating: 1950, n: 20 } },
    cold_start: false, form_last10: { n: 10, win_rate: 0.7 },
    surface_splits: { hard: { n: 60, wins: 45, win_rate: 0.75 } },
    recent: [{ date: 20260105, surface: "hard", round: "F", opponent: "Jannik Sinner", result: "W" }],
    form_trend: [{ date: 20260105, win_rate_10: 0.7 }],
  });
  assert.equal(detail.kind, "ready");
  assert.equal(detail.name, "Carlos Alcaraz");
  assert.equal(detail.recent[0].date, 20260105, "dates stay integer YYYYMMDD, not ISO");
  assert.equal(detail.surfaces.clay.rating, 2150);
});

// ---- Repair: tennis analytics probability ranges, counts and calendar dates.
//
// A probability field (win_rate / win_rate_10) must be finite in [0,1]; a count
// (n / wins / matches) must be a finite non-negative number; a date must be a
// real calendar YYYYMMDD integer (leap years included). Anything else is an
// explicit gap (null), never a clamped value or a fabricated zero.

test("player_detail_probabilities_counts_calendar_validated", () => {
  const detail = C.parsePlayerDetail({
    name: "A", unknown: false, matches: 120, overall: 2100, cold_start: false,
    surfaces: { hard: { rating: 2050, n: 60 }, clay: { rating: 2150, n: -3 }, grass: null },
    form_last10: { n: 10, win_rate: 0.7 },
    surface_splits: { hard: { n: 60, wins: 45, win_rate: 0.75 }, clay: { n: -1, wins: -2, win_rate: 1.5 } },
    recent: [{ date: 20240229, result: "W" }, { date: 20230229 }, { date: 20260230 }, { date: 20260105 }],
    form_trend: [{ date: 20240229, win_rate_10: 0.7 }, { date: 20260230, win_rate_10: -1 }, { date: 20261301, win_rate_10: 2 }],
  });
  assert.equal(detail.kind, "ready");
  assert.equal(detail.matches, 120);
  assert.equal(detail.formLast10.winRate, 0.7, "a valid win rate is preserved");
  assert.equal(detail.surfaces.hard.rating, 2050);
  assert.equal(detail.surfaces.clay.n, null, "a negative surface count is a gap");
  assert.equal(detail.surfaceSplits.hard.winRate, 0.75);
  assert.equal(detail.surfaceSplits.clay.winRate, null, "an out-of-range win rate is a gap, not clamped");
  assert.equal(detail.surfaceSplits.clay.n, null, "a negative split count is a gap");
  assert.equal(detail.surfaceSplits.clay.wins, null, "negative wins is a gap");
  assert.equal(detail.recent[0].date, 20240229, "a leap-day YYYYMMDD is valid");
  assert.equal(detail.recent[1].date, null, "a non-leap Feb 29 is rejected");
  assert.equal(detail.recent[2].date, null, "Feb 30 is rejected");
  assert.equal(detail.recent[3].date, 20260105, "a valid date is preserved");
  assert.equal(detail.formTrend[0].winRate10, 0.7);
  assert.equal(detail.formTrend[0].date, 20240229);
  assert.equal(detail.formTrend[1].date, null, "invalid trend date is a gap");
  assert.equal(detail.formTrend[1].winRate10, null, "a negative probability is a gap");
  assert.equal(detail.formTrend[2].date, null, "an impossible month is rejected");
  assert.equal(detail.formTrend[2].winRate10, null, "an out-of-range probability is a gap");
});

test("player_detail_invalid_probability_and_count_fields_are_gaps_not_zero", () => {
  for (const bad of [42, -1, 1.5, NaN, Infinity, -Infinity, "0.7", null, undefined]) {
    const d = C.parsePlayerDetail({ name: "A", form_last10: { n: 10, win_rate: bad } });
    assert.equal(d.formLast10.winRate, null, `win_rate=${String(bad)} must be a gap`);
  }
  for (const bad of [-1, NaN, Infinity, -Infinity, "10", null, undefined]) {
    const d = C.parsePlayerDetail({ name: "A", matches: bad });
    assert.equal(d.matches, null, `matches=${String(bad)} must be a gap`);
  }
  const zero = C.parsePlayerDetail({ name: "A", form_last10: { n: 0, win_rate: 0 }, matches: 0 });
  assert.equal(zero.formLast10.winRate, 0, "a real zero win rate survives");
  assert.equal(zero.formLast10.n, 0);
  assert.equal(zero.matches, 0);
});

test("reliability_probability_ranges_and_nonnegative_counts", () => {
  const r = C.parseReliability({
    n: 7,
    bins: [{ p_mean: 2, frac_pos: -1, n: -4 }, { p_mean: 0.5, frac_pos: 0.4, n: 3 }],
    ece_oof: 0.1,
  });
  assert.equal(r.kind, "ready");
  assert.equal(r.bins[0].pMean, null, "p_mean outside [0,1] is a gap");
  assert.equal(r.bins[0].fracPos, null, "frac_pos outside [0,1] is a gap");
  assert.equal(r.bins[0].n, null, "a negative bin count is a gap");
  assert.equal(r.bins[1].pMean, 0.5);
  assert.equal(r.bins[1].fracPos, 0.4);
  assert.equal(r.bins[1].n, 3);
  assert.equal(r.n, 7);

  const bad = C.parseReliability({ n: -1, bins: [{ p_mean: NaN, frac_pos: 1.2, n: -2 }] });
  assert.equal(bad.n, null, "a negative overall n is a gap");
  assert.equal(bad.bins[0].pMean, null);
  assert.equal(bad.bins[0].fracPos, null);
  assert.equal(bad.bins[0].n, null, "a negative bin count is a gap");
});
