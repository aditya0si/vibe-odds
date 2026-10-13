import { test } from "node:test";
import assert from "node:assert/strict";
import { loadModule } from "./load-module.mjs";

const C = await loadModule("sports/nba/site/nba-contract.js");

function row(over = {}) {
  return {
    type: "predict",
    game_id: "G1",
    tipoff_ts: "2026-10-27T23:30:00Z",
    logged_at: "2026-10-27T20:00:00Z",
    prob_home: 0.62,
    pick: "home",
    source: "formula_v1",
    ...over,
  };
}

function boardOf(rows) {
  return { board: rows, n: rows.length };
}

test("empty_board", () => {
  const r = C.parseBoard(boardOf([]), { fetchedAt: 10 });
  assert.equal(r.kind, "empty");
  assert.deepEqual(r.rows, []);
  assert.equal(r.rejected, 0);
  assert.equal(r.provenance.fetchedAt, 10);
});

test("count_mismatch_rejected", () => {
  const r = C.parseBoard({ board: [row()], n: 2 }, { fetchedAt: 1 });
  assert.equal(r.kind, "invalid", "n must equal the raw row count");
  const r2 = C.parseBoard({ board: [row(), row({ game_id: "G2" })], n: 1 }, { fetchedAt: 1 });
  assert.equal(r2.kind, "invalid");
});

test("probability_range_and_finiteness", () => {
  const ok = C.parseBoard(boardOf([
    row({ prob_home: 0, pick: "away" }),
    row({ game_id: "G2", prob_home: 1, pick: "home" }),
    row({ game_id: "G3", prob_home: 0.5, pick: "home" }),
  ]), { fetchedAt: 1 });
  assert.equal(ok.kind, "ready");
  assert.equal(ok.rows.length, 3);

  for (const bad of [-0.01, 1.01, NaN, Infinity, "0.5", null]) {
    const r = C.parseBoard(boardOf([row({ prob_home: bad, pick: "home" })]), { fetchedAt: 1 });
    assert.equal(r.kind, "invalid", `prob_home=${String(bad)} must be rejected`);
  }
});

test("pick_threshold_consistency", () => {
  const ok = C.parseBoard(boardOf([
    row({ prob_home: 0.5, pick: "home" }),
    row({ game_id: "G2", prob_home: 0.49, pick: "away" }),
  ]), { fetchedAt: 1 });
  assert.equal(ok.kind, "ready");

  for (const bad of [{ prob_home: 0.5, pick: "away" }, { prob_home: 0.7, pick: "away" }, { prob_home: 0.2, pick: "home" }]) {
    const r = C.parseBoard(boardOf([row(bad)]), { fetchedAt: 1 });
    assert.equal(r.kind, "invalid", `${JSON.stringify(bad)} must be rejected`);
  }
});

test("timezone_required", () => {
  for (const bad of [
    row({ tipoff_ts: "2026-10-27T23:30:00" }),
    row({ logged_at: "2026-10-27T20:00:00" }),
    row({ tipoff_ts: "not-a-date" }),
    row({ logged_at: "2026-13-40T00:00:00Z" }),
  ]) {
    const r = C.parseBoard(boardOf([bad]), { fetchedAt: 1 });
    assert.equal(r.kind, "invalid", `${JSON.stringify(bad)} must be rejected`);
  }
});

test("equal_tipoff_rejected", () => {
  const ts = "2026-10-27T20:00:00Z";
  const r = C.parseBoard(boardOf([row({ tipoff_ts: ts, logged_at: ts })]), { fetchedAt: 1 });
  assert.equal(r.kind, "invalid");
  const after = C.parseBoard(boardOf([row({ tipoff_ts: ts, logged_at: "2026-10-27T21:00:00Z" })]), { fetchedAt: 1 });
  assert.equal(after.kind, "invalid");
});

test("partial_invalid_rows_quarantined", () => {
  const r = C.parseBoard(boardOf([
    row(),
    row({ game_id: "G2", prob_home: 5 }),
    row({ game_id: "G3", tipoff_ts: "2026-10-27T22:00:00Z", logged_at: "2026-10-27T23:00:00Z" }),
  ]), { fetchedAt: 7 });
  assert.equal(r.kind, "ready");
  assert.equal(r.rows.length, 1);
  assert.equal(r.rows[0].game_id, "G1");
  assert.equal(r.rejected, 2);
  assert.equal(r.provenance.rejected, 2);
  assert.equal(r.reasons.length, 2);
});

test("historical_error_200", () => {
  const r = C.parseHistoricalPrediction({ error: "unknown game_id", game_id: "G1" }, "G1");
  assert.equal(r.kind, "invalid");
  assert.notEqual(r.kind, "ready");
});

test("historical_id_mismatch", () => {
  const payload = {
    game_id: "G1", date: "2024-01-05", home_team_id: "H", away_team_id: "A",
    result_home_win: 1, prob_home: 0.62, pick: "home", formula: "formula_v1.json", note: "n",
  };
  const r = C.parseHistoricalPrediction(payload, "G2");
  assert.equal(r.kind, "invalid");

  const good = C.parseHistoricalPrediction(payload, "G1");
  assert.equal(good.kind, "ready");
  assert.equal(good.provenance.kind, "historical");
});

// ---- Task 2 contract extensions -------------------------------------------

test("nba_tiers_use_passes", () => {
  assert.equal(C.tierVerdict({ passes: true }), "PASS");
  assert.equal(C.tierVerdict({ passes: false }), "FAIL");
  assert.equal(C.tierVerdict({ pass: true }), "Unknown", "legacy 'pass' must not be read as a verdict");
  assert.equal(C.tierVerdict({ pass: true, passes: false }), "FAIL");
  assert.equal(C.tierVerdict({}), "Unknown");

  const model = C.parseModel({
    formula: { features: ["a"], coef_raw: { a: 1 }, intercept_raw: 0, n_train: 2 },
    tiers: { T1: { passes: true, pass: false } },
  });
  assert.equal(model.kind, "ready");
  assert.equal(model.tierRows[0].verdict, "PASS");
});

test("nba_teams_rates_suppressed", () => {
  const r = C.parseTeams({ home_win_rate: 0.6, n_games: 3, teams: [{ team_id: 1, team: "A", wins: 2, games: 3, win_rate: 0.667 }] });
  assert.equal(r.kind, "ready");
  assert.equal(r.teams[0].wins, 2);
  assert.equal("win_rate" in r.teams[0], false);
  assert.equal("home_win_rate" in r, false);
  assert.equal(r.ratesSuppressed, true);
});

test("nba_scheduled_counts_are_separate", () => {
  const r = C.parseStats({ db_counts: { games: 10, games_completed: 8, games_scheduled: 2 }, seasons: [], artifacts: [] });
  assert.equal(r.kind, "ready");
  assert.equal(r.completed, 8);
  assert.equal(r.scheduled, 2);
  assert.equal(r.raw, 10);
});

test("nba_null_bins_preserved", () => {
  const r = C.parseReliability({ n: 10, bins: [{ lo: 0, hi: 0.1, n: 0, mean_p: null, rate: null }], ece: null, note: "x" });
  assert.equal(r.kind, "ready");
  assert.equal(r.bins[0].n, 0);
  assert.equal(r.bins[0].meanP, null, "missing mean must stay null, not 0");
  assert.equal(r.bins[0].rate, null, "missing rate must stay null, not 0");
  assert.equal(r.ece, null);
});

// ---- Repair R1: strict ISO calendar/clock validation (finding 4) -----------

test("strict_iso_rejects_impossible_calendar_and_clock", () => {
  for (const bad of [
    "2030-02-30T00:00:00Z", // impossible day (Date.parse would roll to Mar 2)
    "2023-02-29T00:00:00Z", // not a leap year
    "1900-02-29T00:00:00Z", // century non-leap
    "2030-01-01T24:00:00Z", // impossible hour
    "2030-01-01T00:60:00Z", // impossible minute
    "2030-01-01T00:00:60Z", // impossible second
    "2030-13-01T00:00:00Z", // impossible month
    "2030-01-00T00:00:00Z", // day zero
    "2030-01-01T00:00:00+15:00", // offset out of range
    "2030-01-01T00:00:00", // missing zone
    "1", // bare number: Date.parse accepts, this contract must not
  ]) {
    const r = C.parseBoard(boardOf([row({ tipoff_ts: bad })]), { fetchedAt: 1 });
    assert.equal(r.kind, "invalid", `tipoff_ts=${bad} must be rejected`);
  }
});

test("strict_iso_accepts_leap_and_offsets", () => {
  const good = [
    "2024-02-29T00:00:00Z",
    "2000-02-29T00:00:00Z",
    "2030-01-01T00:00:00+14:00",
    "2030-01-01T00:00:00-05:00",
    "2030-06-30T23:59:59+05:30",
    "2030-01-01T00:00:00.250Z",
  ];
  for (const ts of good) {
    const r = C.parseBoard(boardOf([row({ tipoff_ts: ts, logged_at: "1999-12-31T00:00:00Z" })]), { fetchedAt: 1 });
    assert.equal(r.kind, "ready", `tipoff_ts=${ts} must be accepted`);
  }
});

test("historical_date_is_strict_calendar", () => {
  const base = {
    game_id: "G1", home_team_id: "H", away_team_id: "A",
    result_home_win: 1, prob_home: 0.62, pick: "home",
  };
  assert.equal(C.parseHistoricalPrediction({ ...base, date: "2024-01-05" }, "G1").kind, "ready");
  assert.equal(C.parseHistoricalPrediction({ ...base, date: "2024-02-29" }, "G1").kind, "ready");
  for (const bad of ["2024-02-30", "2023-02-29", "2024-13-01", "2024-1-5", "20240105", "", "2024-00-10"]) {
    const r = C.parseHistoricalPrediction({ ...base, date: bad }, "G1");
    assert.equal(r.kind, "invalid", `date=${bad} must be rejected`);
  }
});

// ---- Repair R2 followup: model nested schema guards (consumed fields only) --
//
// parseModel must validate the nested model payload it actually consumes. A
// wrong nested type or a malformed numeric (string, non-finite, negative count)
// is sanitised to an explicit gap (null / {}), never passed through to be
// stringified or joined into a misleading value. Genuinely valid data is
// preserved exactly.

function modelBase(over = {}) {
  return {
    formula: { features: ["a"], coef_raw: { a: 1 } },
    tiers: {},
    ...over,
  };
}

test("model_nested_wrong_types_coerced_not_passed_through", () => {
  const m = C.parseModel(modelBase({
    tiers: "not-an-object",
    pooled_all_test: ["not", "an", "object"],
    pooled_with_close: "nope",
    pooled_with_open: null,
    n_test_games: "4910",
    by_season: 42,
    paired_vs_close: "x",
    tuning_paired: [],
  }));
  assert.equal(m.kind, "ready");
  assert.deepEqual(m.tiers, {}, "non-object tiers degrade to an empty map");
  assert.deepEqual(m.pooledAllTest, {}, "array pooled block is not passed through");
  assert.deepEqual(m.pooledWithClose, {}, "string pooled block is not passed through");
  assert.deepEqual(m.pooledWithOpen, {}, "null pooled block degrades to an empty map");
  assert.equal(m.nTestGames, null, "non-object cohort counts degrade to null");
  assert.deepEqual(m.bySeason, {}, "non-object by_season degrades to an empty map");
  assert.equal(m.pairedVsClose, null, "string paired record degrades to null");
  assert.equal(m.tuningPaired, null, "array paired record degrades to null");
});

test("model_numeric_counts_reject_string_nonfinite_negative", () => {
  const m = C.parseModel(modelBase({
    n_test_games: { all: "4910", with_close: -3, with_open: NaN },
    pooled_all_test: {
      formula: { n: "100", brier: "0.21", acc: Infinity, ece: -1 },
      good: { n: 10, brier: 0.2, acc: 0.6, ece: 0.01 },
    },
    paired_vs_close: { n: "5", mean_diff: Infinity, sigma_d: "x", ci95_blocked: [1, "y", 2], blocks: -1 },
  }));
  assert.equal(m.kind, "ready");
  assert.equal(m.nTestGames.all, null, "string count rejected");
  assert.equal(m.nTestGames.with_close, null, "negative count rejected");
  assert.equal(m.nTestGames.with_open, null, "non-finite count rejected");
  assert.equal(m.pooledAllTest.formula.n, null, "string arm count rejected");
  assert.equal(m.pooledAllTest.formula.brier, null, "string metric rejected");
  assert.equal(m.pooledAllTest.formula.acc, null, "non-finite metric rejected");
  assert.equal(m.pooledAllTest.formula.ece, null, "negative metric rejected");
  assert.deepEqual(m.pooledAllTest.good, { n: 10, brier: 0.2, acc: 0.6, ece: 0.01 }, "valid arm preserved exactly");
  assert.equal(m.pairedVsClose.n, null, "string paired count rejected");
  assert.equal(m.pairedVsClose.mean_diff, null, "non-finite mean_diff rejected");
  assert.equal(m.pairedVsClose.sigma_d, null, "string sigma_d rejected");
  assert.equal(m.pairedVsClose.ci95_blocked, null, "an interval with a non-numeric member is rejected whole, never filtered");
  assert.equal(m.pairedVsClose.blocks, null, "negative block count rejected");

  const goodCounts = C.parseModel(modelBase({
    n_test_games: { all: 4910, with_close: 4890, with_open: 4888 },
  }));
  assert.deepEqual(goodCounts.nTestGames, { all: 4910, with_close: 4890, with_open: 4888 }, "valid cohort counts preserved exactly");
});

test("model_mixed_invalid_test_seasons_filtered", () => {
  const m = C.parseModel(modelBase({
    formula: { features: ["a"], coef_raw: { a: 1 }, test_seasons: ["2022-23", "2023-24", { bad: 1 }, 42, "", "2022", null, "2024-25"] },
  }));
  assert.equal(m.kind, "ready");
  assert.deepEqual(m.formula.test_seasons, ["2022-23", "2023-24", "2024-25"], "only real season labels survive");

  const notArray = C.parseModel(modelBase({ formula: { features: ["a"], coef_raw: { a: 1 }, test_seasons: "2022-23" } }));
  assert.deepEqual(notArray.formula.test_seasons, [], "non-array test_seasons degrades to empty, never a crash");

  const good = ["2022-23", "2023-24", "2024-25", "2025-26"];
  const preserved = C.parseModel(modelBase({ formula: { features: ["a"], coef_raw: { a: 1 }, test_seasons: good } }));
  assert.deepEqual(preserved.formula.test_seasons, good, "valid test seasons preserved exactly");
});

test("model_paired_interval_malformed_fields_gap", () => {
  const m = C.parseModel(modelBase({
    paired_vs_close: { n: -10, mean_diff: "x", sigma_d: null, ci95_blocked: ["a", "b"], blocks: NaN },
  }));
  assert.equal(m.pairedVsClose.n, null);
  assert.equal(m.pairedVsClose.mean_diff, null);
  assert.equal(m.pairedVsClose.sigma_d, null);
  assert.equal(m.pairedVsClose.ci95_blocked, null, "all-string interval is rejected whole as null, never filtered to []");
  assert.equal(m.pairedVsClose.blocks, null);

  const good = { n: 4890, mean_diff: -0.01077, sigma_d: 0.09044, ci95_blocked: [-0.01324, -0.00823], blocks: 489 };
  const kept = C.parseModel(modelBase({ paired_vs_close: good }));
  assert.deepEqual(kept.pairedVsClose, good, "valid paired interval preserved exactly (negative mean_diff allowed)");
});

// Repair R2 final: a blocked CI is trusted only when it is EXACTLY two finite,
// ordered numeric endpoints. A malformed interval is rejected WHOLE as null —
// it is never filtered down to a plausible-looking subset, and no endpoint is
// ever invented. This fail-closed rule applies to BOTH paired_vs_close and
// tuning_paired.
test("model_paired_interval_malformed_rejected_whole_not_filtered", () => {
  const malformed = [
    ["missing endpoint", [0.1]],
    ["empty array", []],
    ["null endpoint", [0.1, null]],
    ["leading null endpoint", [null, 0.2]],
    ["string endpoints", ["0.1", "0.2"]],
    ["mixed string endpoint", [0.1, "0.2"]],
    ["NaN endpoint", [NaN, 0.2]],
    ["NaN trailing endpoint", [0.1, NaN]],
    ["infinite endpoint", [0.1, Infinity]],
    ["negative infinite endpoint", [-Infinity, 0.2]],
    ["reversed endpoints", [0.2, 0.1]],
    ["extra endpoint", [0.1, 0.2, 0.3]],
    ["four endpoints", [0.1, 0.2, 0.3, 0.4]],
    ["non-array string", "nope"],
    ["non-array number", 3],
    ["non-array object", { lo: 0.1, hi: 0.2 }],
  ];
  for (const [label, bad] of malformed) {
    for (const field of ["paired_vs_close", "tuning_paired"]) {
      const m = C.parseModel(modelBase({ [field]: { n: 10, mean_diff: 0.01, sigma_d: 0.02, ci95_blocked: bad, blocks: 3 } }));
      const pair = field === "paired_vs_close" ? m.pairedVsClose : m.tuningPaired;
      assert.equal(pair.ci95_blocked, null, `${field}: ${label} interval rejected whole as null`);
      assert.equal(pair.mean_diff, 0.01, `${field}: ${label} keeps the surrounding record (unknown UX)`);
      assert.equal(pair.n, 10, `${field}: ${label} keeps the surrounding record (unknown UX)`);
    }
  }
});

test("model_paired_interval_valid_two_endpoints_survive_zero_and_negative", () => {
  const valid = [[0, 0], [-0.01324, -0.00823], [-0.5, 0], [0, 0.5], [-0.002, 0.0], [0.0, 1.0]];
  for (const good of valid) {
    for (const field of ["paired_vs_close", "tuning_paired"]) {
      const m = C.parseModel(modelBase({ [field]: { n: 4890, mean_diff: -0.01, sigma_d: 0.09, ci95_blocked: good, blocks: 489 } }));
      const pair = field === "paired_vs_close" ? m.pairedVsClose : m.tuningPaired;
      assert.deepEqual(pair.ci95_blocked, good, `${field}: valid interval ${JSON.stringify(good)} preserved exactly`);
    }
  }
});

test("sanitize_interval_rejects_whole_and_keeps_exact_pair", () => {
  assert.deepEqual(C.sanitizeInterval([-0.01077, -0.00823]), [-0.01077, -0.00823]);
  assert.deepEqual(C.sanitizeInterval([0, 0]), [0, 0]);
  assert.deepEqual(C.sanitizeInterval([-0.5, 0.0]), [-0.5, 0.0]);
  assert.deepEqual(C.sanitizeInterval([0.1, 0.1]), [0.1, 0.1], "equal endpoints are ordered");
  for (const bad of [[0.2, 0.1], [0.1, -0.1], [1], [], [1, 2, 3], [1, null], ["a", "b"], [NaN, 1], [1, Infinity], "x", 5, {}, null]) {
    assert.equal(C.sanitizeInterval(bad), null, `malformed interval ${JSON.stringify(bad)} rejected whole`);
  }
});

test("model_tier_missing_claim_explicit_never_empty", () => {
  const m = C.parseModel(modelBase({
    tiers: {
      T1_beats_naive_baselines: { passes: true },
      T2: { passes: false, claim: "beats the opening line" },
      T3: { passes: false, claim: "" },
      T4: { passes: "yes", claim: 42 },
    },
  }));
  const byKey = Object.fromEntries(m.tierRows.map((t) => [t.key, t]));
  assert.equal(byKey.T1_beats_naive_baselines.claim, "—", "absent claim is explicit, never empty");
  assert.equal(byKey.T2.claim, "beats the opening line", "real claim preserved");
  assert.equal(byKey.T3.claim, "—", "empty claim is explicit");
  assert.equal(byKey.T4.claim, "—", "non-string claim is explicit");
  assert.equal(byKey.T1_beats_naive_baselines.verdict, "PASS");
  assert.equal(byKey.T3.verdict, "FAIL");
  assert.equal(byKey.T4.verdict, "Unknown", "non-boolean passes stays Unknown");
});

// Repair: a settlement is trusted only when both scores are finite and
// non-negative AND settled_at is a timezone-bearing strict ISO instant. An
// invalid settlement must never render as settled and must never assert
// correctness. The valid pre-tip row is preserved as an explicit pending row
// with a warning rather than rejected.
test("invalid_settlement_cannot_assert_correctness", () => {
  const badSettlements = [
    { home_score: NaN, away_score: 100, settled_at: "2030-01-02T00:00:00Z" },
    { home_score: -1, away_score: 100, settled_at: "2030-01-02T00:00:00Z" },
    { home_score: 110, away_score: -1, settled_at: "2030-01-02T00:00:00Z" },
    { home_score: Infinity, away_score: 100, settled_at: "2030-01-02T00:00:00Z" },
    { home_score: "110", away_score: 100, settled_at: "2030-01-02T00:00:00Z" },
    { home_score: null, away_score: 100, settled_at: "2030-01-02T00:00:00Z" },
    { home_score: 110, away_score: 100, settled_at: "nonsense" },
    { home_score: 110, away_score: 100, settled_at: "2030-01-02T00:00:00" },
    { home_score: 110, away_score: 100, settled_at: "2030-02-30T00:00:00Z" },
    { home_score: 110, away_score: 100 },
  ];
  for (const settled of badSettlements) {
    const r = C.parseBoard(boardOf([row({ settled, correct: true })]), { fetchedAt: 1 });
    assert.equal(r.kind, "ready", `${JSON.stringify(settled)} keeps the valid pre-tip row`);
    assert.equal(r.rejected, 0, "the pre-tip row itself is valid and not quarantined");
    assert.equal(r.rows[0].settled, undefined, "an invalid settlement is never rendered as settled");
    assert.equal(r.rows[0].correct, undefined, "an invalid settlement must not assert correctness");
    assert.equal(typeof r.rows[0].warning, "string", "the preserved pending row carries an explicit warning");
  }
});

test("correctness_without_a_valid_settlement_is_not_asserted", () => {
  const r = C.parseBoard(boardOf([row({ correct: true })]), { fetchedAt: 1 });
  assert.equal(r.kind, "ready");
  assert.equal(r.rows[0].settled, undefined);
  assert.equal(r.rows[0].correct, undefined, "correct:true without a valid settlement is unverifiable and dropped");
  assert.equal(typeof r.rows[0].warning, "string");
});

test("valid_settlement_preserves_scores_and_correctness", () => {
  const win = C.parseBoard(boardOf([
    row({ settled: { home_score: 110, away_score: 100, settled_at: "2030-01-02T00:00:00Z" }, correct: true }),
  ]), { fetchedAt: 1 });
  assert.equal(win.kind, "ready");
  assert.deepEqual(win.rows[0].settled, { home_score: 110, away_score: 100, settled_at: "2030-01-02T00:00:00Z" });
  assert.equal(win.rows[0].correct, true, "a valid settled record keeps its correctness");
  assert.equal(win.rows[0].warning, undefined);

  const loss = C.parseBoard(boardOf([
    row({ prob_home: 0.4, pick: "away", settled: { home_score: 98, away_score: 105, settled_at: "2030-01-02T00:00:00+05:30" }, correct: true }),
  ]), { fetchedAt: 1 });
  assert.equal(loss.rows[0].correct, true, "an away pick winning is correct");
  assert.equal(loss.rows[0].settled.home_score, 98);
  assert.equal(loss.rows[0].settled.settled_at, "2030-01-02T00:00:00+05:30");

  const wrong = C.parseBoard(boardOf([
    row({ settled: { home_score: 90, away_score: 105, settled_at: "2030-01-02T00:00:00Z" }, correct: false }),
  ]), { fetchedAt: 1 });
  assert.equal(wrong.rows[0].correct, false, "a home pick losing is incorrect");
  assert.deepEqual(wrong.rows[0].settled, { home_score: 90, away_score: 105, settled_at: "2030-01-02T00:00:00Z" });
});

// Repair R5: settlement correctness is DERIVED from valid integer, non-negative,
// non-tied scores and the recognised pick. A supplied flag that contradicts the
// score is rejected whole (never silently overridden); a missing flag is filled
// only when the outcome is unambiguous. Ties, fractional scores and a settled_at
// before tipoff are all invalid -> pending + warning.
test("settlement_derives_correctness_only_when_unambiguous", () => {
  const homeWin = C.parseBoard(boardOf([
    row({ settled: { home_score: 112, away_score: 105, settled_at: "2030-01-02T00:00:00Z" } }),
  ]), { fetchedAt: 1 });
  assert.equal(homeWin.rows[0].correct, true, "derived: home pick and home won");
  assert.equal(homeWin.rows[0].warning, undefined);

  const homeLoss = C.parseBoard(boardOf([
    row({ settled: { home_score: 98, away_score: 101, settled_at: "2030-01-02T00:00:00Z" } }),
  ]), { fetchedAt: 1 });
  assert.equal(homeLoss.rows[0].correct, false, "derived: home pick and home lost");

  const awayWin = C.parseBoard(boardOf([
    row({ prob_home: 0.3, pick: "away", settled: { home_score: 98, away_score: 101, settled_at: "2030-01-02T00:00:00Z" } }),
  ]), { fetchedAt: 1 });
  assert.equal(awayWin.rows[0].correct, true, "derived: away pick and away won");

  const awayLoss = C.parseBoard(boardOf([
    row({ prob_home: 0.3, pick: "away", settled: { home_score: 112, away_score: 105, settled_at: "2030-01-02T00:00:00Z" } }),
  ]), { fetchedAt: 1 });
  assert.equal(awayLoss.rows[0].correct, false, "derived: away pick and away lost");
});

test("contradictory_settlement_correctness_is_rejected_whole", () => {
  // home pick, but the supplied flag says incorrect even though home won.
  const r = C.parseBoard(boardOf([
    row({ settled: { home_score: 90, away_score: 100, settled_at: "2030-01-02T00:00:00Z" }, correct: true }),
  ]), { fetchedAt: 1 });
  assert.equal(r.kind, "ready", "the valid pre-tip row is preserved");
  assert.equal(r.rejected, 0, "the contradiction is not the row itself being malformed");
  assert.equal(r.rows[0].settled, undefined, "a contradictory settlement is dropped whole");
  assert.equal(r.rows[0].correct, undefined, "a contradictory flag never asserts correctness");
  assert.match(r.rows[0].warning, /settlement/i, "the row states why it is pending");
  assert.equal("correct" in r.rows[0], false, "no correctness property is asserted on the preserved row");
});

test("tie_fractional_and_early_settlements_are_rejected", () => {
  const cases = [
    ["tie", { home_score: 100, away_score: 100, settled_at: "2030-01-02T00:00:00Z" }],
    ["fractional home", { home_score: 100.5, away_score: 90.1, settled_at: "2030-01-02T00:00:00Z" }],
    ["fractional away", { home_score: 100, away_score: 90.5, settled_at: "2030-01-02T00:00:00Z" }],
    ["settled before tipoff", { home_score: 100, away_score: 90, settled_at: "2020-01-01T00:00:00Z" }],
  ];
  for (const [label, settled] of cases) {
    const r = C.parseBoard(boardOf([row({ settled, correct: true })]), { fetchedAt: 1 });
    assert.equal(r.rows[0].settled, undefined, `${label} settlement is never rendered as settled`);
    assert.equal(r.rows[0].correct, undefined, `${label} settlement must not assert correctness`);
    assert.equal(typeof r.rows[0].warning, "string", `${label} keeps an explicit warning`);
  }
});

test("valid_offset_settlement_and_both_picks_survive", () => {
  const offset = C.parseBoard(boardOf([
    row({ settled: { home_score: 112, away_score: 105, settled_at: "2030-01-02T00:00:00+05:30" }, correct: true }),
  ]), { fetchedAt: 1 });
  assert.equal(offset.rows[0].correct, true);
  assert.equal(offset.rows[0].settled.settled_at, "2030-01-02T00:00:00+05:30", "strict offset date survives");

  const away = C.parseBoard(boardOf([
    row({ prob_home: 0.3, pick: "away", settled: { home_score: 99, away_score: 104, settled_at: "2030-01-02T00:00:00-08:00" }, correct: true }),
  ]), { fetchedAt: 1 });
  assert.equal(away.rows[0].correct, true, "away win with a negative offset survives");
  assert.equal(away.rows[0].settled.away_score, 104);
});
