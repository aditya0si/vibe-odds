// Court Evidence — NBA payload validation (dependency-free ES module).
//
// Parsers are pure: no DOM, no filesystem, no network. Each returns a tagged
// result so the UI can distinguish ready / empty / invalid and never fabricate
// a plausible prediction from a malformed row.
//
// NBA provenance rule: board rows are the pre-tip live log. A row is valid only
// when logged_at is timezone-bearing and STRICTLY before a timezone-bearing
// tipoff_ts, prob_home is finite in [0,1], and pick agrees with prob_home >= .5.

const HOME = "home";
const AWAY = "away";

function isObj(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function finite(v) {
  return typeof v === "number" && Number.isFinite(v);
}

export function isProbability(v) {
  return finite(v) && v >= 0 && v <= 1;
}

// Strict ISO-8601 validators: a real calendar, a real clock, and an explicit
// zone. Date.parse alone silently accepts "1", rolls "2030-02-30" into March,
// and normalises impossible clock fields, so it is never trusted on its own.
const ISO_DATETIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:?\d{2})$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function isLeapYear(year) {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInMonth(year, month) {
  const lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month === 2 && isLeapYear(year)) return 29;
  return lengths[month - 1];
}

function validCalendar(year, month, day) {
  if (month < 1 || month > 12) return false;
  return day >= 1 && day <= daysInMonth(year, month);
}

function validOffset(zone) {
  if (zone === "Z") return true;
  const hours = Number(zone.slice(1, 3));
  const minutes = Number(zone.slice(-2));
  if (hours > 14 || minutes > 59) return false;
  return hours !== 14 || minutes === 0;
}

export function isStrictIsoDateTime(v) {
  if (typeof v !== "string") return false;
  const m = ISO_DATETIME.exec(v);
  if (!m) return false;
  if (!validCalendar(Number(m[1]), Number(m[2]), Number(m[3]))) return false;
  if (Number(m[4]) > 23 || Number(m[5]) > 59 || Number(m[6]) > 59) return false;
  return validOffset(m[7]);
}

export function isStrictDate(v) {
  if (typeof v !== "string") return false;
  const m = ISO_DATE.exec(v);
  if (!m) return false;
  return validCalendar(Number(m[1]), Number(m[2]), Number(m[3]));
}

// A timestamp is trusted only when it is a strict, zone-bearing ISO instant.
export function hasTimezone(v) {
  return isStrictIsoDateTime(v);
}

function ms(v) {
  return Date.parse(v);
}

function inv(message) {
  return { kind: "invalid", rows: [], rejected: 0, reasons: [{ reason: message }], provenance: null };
}

// ---- board ----------------------------------------------------------------

export function parseBoard(payload, { fetchedAt = null } = {}) {
  if (!isObj(payload) || !Array.isArray(payload.board)) {
    return inv("board payload missing board array");
  }
  const raw = payload.board;
  if (!finite(payload.n) || payload.n !== raw.length) {
    return inv(`board count mismatch: n=${String(payload.n)} rows=${raw.length}`);
  }

  const rows = [];
  const reasons = [];
  raw.forEach((r, index) => {
    const res = validateBoardRow(r);
    if (res.ok) rows.push(res.row);
    else reasons.push({ index, reason: res.reason });
  });

  const provenance = {
    fetchedAt,
    source: "live-log",
    observed: "logged before tipoff",
    rejected: reasons.length,
  };

  if (raw.length === 0) {
    return { kind: "empty", rows: [], rejected: 0, reasons: [], provenance };
  }
  if (rows.length === 0) {
    return { kind: "invalid", rows: [], rejected: reasons.length, reasons, provenance };
  }
  return { kind: "ready", rows, rejected: reasons.length, reasons, provenance };
}

function validateBoardRow(r) {
  if (!isObj(r)) return { ok: false, reason: "row is not an object" };
  if (r.type !== "predict") return { ok: false, reason: "type is not predict" };
  if (typeof r.game_id !== "string" || r.game_id.length === 0) {
    return { ok: false, reason: "missing game_id" };
  }
  if (!hasTimezone(r.tipoff_ts)) return { ok: false, reason: "tipoff_ts lacks a timezone" };
  if (!hasTimezone(r.logged_at)) return { ok: false, reason: "logged_at lacks a timezone" };
  if (!(ms(r.logged_at) < ms(r.tipoff_ts))) {
    return { ok: false, reason: "logged_at is not strictly before tipoff_ts" };
  }
  if (!isProbability(r.prob_home)) return { ok: false, reason: "prob_home not finite in [0,1]" };
  if (r.pick !== HOME && r.pick !== AWAY) return { ok: false, reason: "pick is not home/away" };
  const expected = r.prob_home >= 0.5 ? HOME : AWAY;
  if (r.pick !== expected) return { ok: false, reason: "pick disagrees with prob_home" };
  if (typeof r.source !== "string" || r.source.length === 0) {
    return { ok: false, reason: "missing source" };
  }

  const row = {
    type: "predict",
    game_id: r.game_id,
    tipoff_ts: r.tipoff_ts,
    logged_at: r.logged_at,
    prob_home: r.prob_home,
    pick: r.pick,
    source: r.source,
  };
  // A settlement is consumed evidence: it is trusted only when both scores are
  // non-negative INTEGERS, the game is NOT tied, settled_at is a timezone-bearing
  // strict ISO instant not before tipoff, and any supplied correctness flag
  // agrees with the score. Correctness is DERIVED from the score and the pick, so
  // a contradictory flag is rejected whole (never silently overridden) and a
  // missing flag is filled only when the outcome is unambiguous. An invalid
  // settlement can NEVER assert correctness; the valid pre-tip row is preserved
  // as pending with a warning.
  if (r.settled !== undefined && r.settled !== null) {
    const settled = validateSettled(r.settled, r);
    if (settled) {
      row.settled = settled;
      row.correct = settled.home_score > settled.away_score ? r.pick === HOME : r.pick === AWAY;
    } else {
      row.warning = "Settlement record failed validation (non-negative integer non-tied scores, a timezone-bearing settled_at not before tipoff, and a correctness flag consistent with the score are required); it was ignored and the row is treated as awaiting settlement.";
    }
  } else if (typeof r.correct === "boolean") {
    row.warning = "Correctness flag without a valid settlement record is unverifiable; it is not asserted.";
  }
  return { ok: true, row };
}

function validateSettled(s, r) {
  if (!isObj(s)) return null;
  if (!Number.isInteger(s.home_score) || s.home_score < 0) return null;
  if (!Number.isInteger(s.away_score) || s.away_score < 0) return null;
  if (s.home_score === s.away_score) return null;
  if (!hasTimezone(s.settled_at)) return null;
  if (!(ms(s.settled_at) >= ms(r.tipoff_ts))) return null;
  if (typeof r.correct === "boolean") {
    const derived = s.home_score > s.away_score ? r.pick === HOME : r.pick === AWAY;
    if (r.correct !== derived) return null;
  }
  return { home_score: s.home_score, away_score: s.away_score, settled_at: s.settled_at };
}

// ---- historical reconstruction -------------------------------------------
//
// /api/predict re-derives a past result from published coefficients. It is NOT
// a prospective record, so the caller keeps it in a separate, permanently
// labelled historical view — never in the live board.

export function parseHistoricalPrediction(payload, requestedId) {
  if (!isObj(payload)) {
    return { kind: "invalid", message: "empty historical payload", requestedId };
  }
  if (payload.error) {
    return { kind: "invalid", message: String(payload.error), requestedId };
  }
  if (typeof payload.game_id !== "string" || payload.game_id !== requestedId) {
    return { kind: "invalid", message: "returned game_id does not match request", requestedId };
  }
  if (!isProbability(payload.prob_home)) {
    return { kind: "invalid", message: "prob_home not finite in [0,1]", requestedId };
  }
  const expected = payload.prob_home >= 0.5 ? HOME : AWAY;
  if (payload.pick !== expected) {
    return { kind: "invalid", message: "pick disagrees with prob_home", requestedId };
  }
  if (!(payload.result_home_win === null || payload.result_home_win === 0 || payload.result_home_win === 1)) {
    return { kind: "invalid", message: "result_home_win is not null/0/1", requestedId };
  }
  if (!isStrictDate(payload.date)) {
    return { kind: "invalid", message: "game date is not a strict YYYY-MM-DD calendar date", requestedId };
  }

  return {
    kind: "ready",
    requestedId,
    row: {
      game_id: payload.game_id,
      date: payload.date,
      home_team_id: payload.home_team_id,
      away_team_id: payload.away_team_id,
      result_home_win: payload.result_home_win,
      prob_home: payload.prob_home,
      pick: payload.pick,
    },
    provenance: {
      kind: "historical",
      formula: payload.formula || null,
      note: payload.note || null,
    },
  };
}

// ---- model ----------------------------------------------------------------

export function tierVerdict(tier) {
  if (!isObj(tier)) return "Unknown";
  if (tier.passes === true) return "PASS";
  if (tier.passes === false) return "FAIL";
  return "Unknown";
}

// A season label is exactly the frozen shape YYYY-YY ("2022-23"). Anything else
// (a non-string, a bare year, a blank) is not a season and is dropped rather
// than joined into the rendered window as "[object Object]" or "42".
const SEASON_LABEL = /^\d{4}-\d{2}$/;

// Consumed numerics are validated, not trusted. Counts (games, blocks) are
// finite and non-negative; metrics (brier/acc/ece) are finite and non-negative.
// A malformed value becomes null — an explicit gap the UI already renders "—".
function sanitizeCount(v) {
  return finite(v) && v >= 0 ? v : null;
}

function sanitizeMetric(v) {
  return finite(v) && v >= 0 ? v : null;
}

// A consumed confidence interval is trusted only when it is EXACTLY two finite,
// ordered numeric endpoints (lo <= hi). Anything else — a missing/null/string/
// non-finite/reversed/extra endpoint, or a non-array — is rejected WHOLE as
// null. It is never filtered down to a plausible-looking subset of members, so
// malformed evidence can never be normalised into a fabricated pair.
export function sanitizeInterval(v) {
  if (!Array.isArray(v) || v.length !== 2) return null;
  const [lo, hi] = v;
  if (!finite(lo) || !finite(hi)) return null;
  if (lo > hi) return null;
  return [lo, hi];
}

function sanitizeTestSeasons(v) {
  if (!Array.isArray(v)) return [];
  return v.filter((s) => typeof s === "string" && SEASON_LABEL.test(s));
}

// Pooled evaluation blocks: keep only object arms; sanitize the fields modelView
// actually reads (n, brier, acc, ece) while preserving every other key exactly.
function sanitizePooled(p) {
  if (!isObj(p)) return {};
  const out = {};
  for (const [arm, v] of Object.entries(p)) {
    if (!isObj(v)) continue;
    const a = { ...v };
    if ("n" in a) a.n = sanitizeCount(a.n);
    for (const k of ["brier", "acc", "ece"]) {
      if (k in a) a[k] = sanitizeMetric(a[k]);
    }
    out[arm] = a;
  }
  return out;
}

// Distinct availability cohorts differ by which market prices exist and are
// never pooled: a non-object block is unknown, and a bad count is a gap.
function sanitizeCounts(o) {
  if (!isObj(o)) return null;
  const out = {};
  for (const k of ["all", "with_close", "with_open"]) {
    if (k in o) out[k] = sanitizeCount(o[k]);
  }
  return out;
}

function sanitizeSeasonRow(v) {
  if (!isObj(v)) return null;
  const row = { ...v };
  if (isObj(row.formula)) {
    const fm = { ...row.formula };
    if ("n" in fm) fm.n = sanitizeCount(fm.n);
    for (const k of ["brier", "ece", "acc"]) {
      if (k in fm) fm[k] = sanitizeMetric(fm[k]);
    }
    row.formula = fm;
  } else if ("formula" in row) {
    row.formula = {};
  }
  for (const k of ["elo_only", "climatological", "market_close (market)"]) {
    if (isObj(row[k])) {
      const a = { ...row[k] };
      if ("brier" in a) a.brier = sanitizeMetric(a.brier);
      row[k] = a;
    } else if (k in row) {
      row[k] = {};
    }
  }
  return row;
}

function sanitizeBySeason(b) {
  if (!isObj(b)) return {};
  const out = {};
  for (const [season, v] of Object.entries(b)) {
    const row = sanitizeSeasonRow(v);
    if (row) out[season] = row;
  }
  return out;
}

// Paired intervals (paired_vs_close / tuning_paired) feed mean_diff (signed) and
// a blocked CI. A malformed interval is a gap, never a fabricated zero-effect.
function sanitizePaired(p) {
  if (!isObj(p)) return null;
  const out = { ...p };
  out.n = sanitizeCount(p.n);
  out.mean_diff = finite(p.mean_diff) ? p.mean_diff : null;
  out.sigma_d = finite(p.sigma_d) ? p.sigma_d : null;
  out.ci95_blocked = sanitizeInterval(p.ci95_blocked);
  out.blocks = sanitizeCount(p.blocks);
  return out;
}

export function parseModel(payload) {
  if (!isObj(payload)) return { kind: "invalid", reason: "model payload missing" };
  const f = payload.formula;
  if (!isObj(f) || !Array.isArray(f.features) || !isObj(f.coef_raw)) {
    return { kind: "invalid", reason: "formula coefficients missing" };
  }
  const formula = { ...f, test_seasons: sanitizeTestSeasons(f.test_seasons) };
  const tiers = isObj(payload.tiers) ? payload.tiers : {};
  const tierRows = Object.keys(tiers).map((key) => {
    const t = isObj(tiers[key]) ? tiers[key] : null;
    const claim = t && typeof t.claim === "string" && t.claim.length > 0 ? t.claim : "—";
    return {
      key,
      claim,
      verdict: tierVerdict(t),
      passes: t ? t.passes : undefined,
    };
  });
  return {
    kind: "ready",
    formula,
    tiers,
    tierRows,
    pooledAllTest: sanitizePooled(payload.pooled_all_test),
    pooledWithClose: sanitizePooled(payload.pooled_with_close),
    pooledWithOpen: sanitizePooled(payload.pooled_with_open),
    nTestGames: sanitizeCounts(payload.n_test_games),
    bySeason: sanitizeBySeason(payload.by_season),
    pairedVsClose: sanitizePaired(payload.paired_vs_close),
    tuningPaired: sanitizePaired(payload.tuning_paired),
    notes: payload.notes || "",
  };
}

// ---- teams ----------------------------------------------------------------
//
// Denominators include scheduled (unscored) rows, so win_rate / home_win_rate
// are deliberately dropped rather than published as completed-game rates.

export function parseTeams(payload) {
  if (!isObj(payload) || !Array.isArray(payload.teams)) {
    return { kind: "invalid", reason: "teams payload missing" };
  }
  const teams = payload.teams.filter(isObj).map((t) => ({
    team_id: t.team_id,
    team: t.team,
    wins: t.wins,
    games: t.games,
  }));
  return { kind: "ready", teams, nGames: finite(payload.n_games) ? payload.n_games : null, ratesSuppressed: true };
}

// ---- stats ----------------------------------------------------------------

export function parseStats(payload) {
  if (!isObj(payload) || !isObj(payload.db_counts)) {
    return { kind: "invalid", reason: "stats payload missing" };
  }
  const c = payload.db_counts;
  return {
    kind: "ready",
    counts: c,
    raw: finite(c.games) ? c.games : null,
    completed: finite(c.games_completed) ? c.games_completed : null,
    scheduled: finite(c.games_scheduled) ? c.games_scheduled : null,
    seasons: Array.isArray(payload.seasons) ? payload.seasons : [],
    artifacts: Array.isArray(payload.artifacts) ? payload.artifacts : [],
  };
}

// ---- reliability ----------------------------------------------------------
//
// Empty bins must stay visible as gaps. A missing mean/rate is null, never 0.

export function parseReliability(payload) {
  if (!isObj(payload) || !Array.isArray(payload.bins)) {
    return { kind: "invalid", reason: "reliability payload missing" };
  }
  const bins = payload.bins.map((b, index) => {
    if (!isObj(b)) return { index, lo: null, hi: null, n: null, meanP: null, rate: null };
    return {
      index,
      lo: finite(b.lo) ? b.lo : null,
      hi: finite(b.hi) ? b.hi : null,
      n: finite(b.n) ? b.n : null,
      meanP: finite(b.mean_p) ? b.mean_p : null,
      rate: finite(b.rate) ? b.rate : null,
    };
  });
  return {
    kind: "ready",
    n: finite(payload.n) ? payload.n : null,
    bins,
    ece: finite(payload.ece) ? payload.ece : null,
    note: typeof payload.note === "string" ? payload.note : "",
  };
}
