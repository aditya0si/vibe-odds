// Court Evidence — tennis payload validation (dependency-free ES module).
//
// Parsers are pure: no DOM, no filesystem, no network. Each returns a tagged
// result so the UI can distinguish ready / empty / mock / unverified / invalid
// and never fabricate a live prediction from a malformed or untrusted feed.
//
// FEED CONTAINMENT (the core tennis rule):
//   * feed === 'mock' is ALWAYS blocked from live presentation, even when
//     /api/health reports has_key=true (mock-with-key must not leak).
//   * a live-looking payload with a missing/absent credential, an inconsistent
//     health object, or an unknown/init/missing feed is UNVERIFIED: rows may be
//     listed but probabilities are withheld.
//   * cache age is the SERVER-reported group cache age, never the browser fetch
//     time. age > 60s is stale; age == 60 is NOT stale; null/negative/nonfinite
//     age is Unknown freshness.
//   * demo rows are explicitly opted in by the user and never enter live counts.

const HOME_FEEDS = ["live", "cache"];

function isObj(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function finite(v) {
  return typeof v === "number" && Number.isFinite(v);
}

export function isProbability(v) {
  return finite(v) && v >= 0 && v <= 1;
}

// A consumed count (matches, surface n, split n/wins, reliability bin n) is a
// finite non-negative number; anything else is an explicit gap, never 0.
function isCount(v) {
  return finite(v) && v >= 0;
}

// Strict ISO-8601: a real calendar, a real clock, and an explicit zone.
// Date.parse alone accepts "1" and rolls "2030-02-30" into March, so it is
// never trusted on its own.
const ISO_DATETIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:?\d{2})$/;

function isLeapYear(year) {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInMonth(year, month) {
  const lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month === 2 && isLeapYear(year)) return 29;
  return lengths[month - 1];
}

// A tennis history date is an integer YYYYMMDD that must be a real calendar day
// (leap years included). Date.parse is never used: "20260230" must not roll.
function validYyyymmdd(v) {
  if (!finite(v) || !Number.isInteger(v)) return false;
  const year = Math.trunc(v / 10000);
  const month = Math.trunc(v / 100) % 100;
  const day = v % 100;
  if (year < 1000 || year > 9999) return false;
  if (month < 1 || month > 12) return false;
  return day >= 1 && day <= daysInMonth(year, month);
}

export function meaningfulTime(v) {
  if (typeof v !== "string") return false;
  const m = ISO_DATETIME.exec(v);
  if (!m) return false;
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (month < 1 || month > 12 || day < 1 || day > daysInMonth(Number(m[1]), month)) return false;
  if (Number(m[4]) > 23 || Number(m[5]) > 59 || Number(m[6]) > 59) return false;
  const zone = m[7];
  if (zone !== "Z") {
    const hours = Number(zone.slice(1, 3));
    const minutes = Number(zone.slice(-2));
    if (hours > 14 || minutes > 59 || (hours === 14 && minutes !== 0)) return false;
  }
  return true;
}

// ---- feed + freshness classification --------------------------------------

const CONTRADICTORY_MODES = ["mock", "init", "error", "unknown"];

export function classifyFeed(feed, health) {
  if (feed === "mock") {
    return { mode: "mock", live: false, cached: false, unverified: false };
  }
  if (!isObj(health)) {
    return { mode: "unverified", live: false, cached: false, unverified: true };
  }
  // Live readiness requires an explicit health.ok === true. A missing ok, a
  // false ok, or a contradictory provider mode all fail closed.
  if (health.ok !== true) {
    return { mode: "unverified", live: false, cached: false, unverified: true };
  }
  const providerMode = isObj(health.provider) ? health.provider.mode : undefined;
  if (typeof providerMode === "string" && CONTRADICTORY_MODES.includes(providerMode)) {
    return { mode: "unverified", live: false, cached: false, unverified: true };
  }
  if (health.has_key === false) {
    // no credential: cache can be shown as cached evidence; a "live" claim cannot
    return feed === "cache"
      ? { mode: "cached", live: false, cached: true, unverified: false }
      : { mode: "unverified", live: false, cached: false, unverified: true };
  }
  if (health.has_key !== true) {
    return { mode: "unverified", live: false, cached: false, unverified: true };
  }
  if (feed === "live") return { mode: "live", live: true, cached: false, unverified: false };
  if (feed === "cache") return { mode: "cached", live: false, cached: true, unverified: false };
  return { mode: "unverified", live: false, cached: false, unverified: true };
}

// Cache age boundary: exactly 60s is fresh; strictly greater is stale.
export function isStaleAge(age) {
  return finite(age) && age > 60;
}

export function classifyAge(age) {
  if (!finite(age) || age < 0) return { freshness: "unknown", stale: false, ageS: null };
  if (age > 60) return { freshness: "stale", stale: true, ageS: age };
  return { freshness: "fresh", stale: false, ageS: age };
}

// ---- board ----------------------------------------------------------------

export function parseBoard(payload, { fetchedAt = null, health = null, now = null, demo = false } = {}) {
  if (!isObj(payload) || !Array.isArray(payload.matches)) {
    return inv("board payload missing matches array", fetchedAt);
  }
  const raw = payload.matches;
  const feed = typeof payload.feed === "string" ? payload.feed : null;
  const age = payload.cache_age_s;
  const feedState = classifyFeed(feed, health);
  const ageState = classifyAge(age);

  const rows = [];
  const errors = [];
  const reasons = [];
  raw.forEach((card, index) => {
    const res = validateCard(card);
    if (res.ok && res.error) errors.push(res.error);
    else if (res.ok) rows.push(res.row);
    else reasons.push({ index, reason: res.reason });
  });

  const provenance = {
    fetchedAt,
    feed,
    feedMode: feedState.mode,
    cacheAgeS: ageState.ageS,
    freshness: ageState.freshness,
    stale: ageState.stale,
    live: feedState.live,
    cached: feedState.cached,
    unverified: feedState.unverified,
    hasKey: isObj(health) ? health.has_key === true : null,
    demo: !!demo,
  };

  let kind;
  if (demo) kind = "demo";
  else if (feedState.mode === "mock") kind = "mock";
  else if (feedState.mode === "unverified") kind = "unverified";
  else if (raw.length === 0) kind = "empty";
  else if (rows.length === 0 && reasons.length > 0) kind = "invalid";
  else if (rows.length === 0) kind = "empty"; // only server error cards: no predictions
  else kind = "ready";

  return {
    kind,
    rows,
    errors,
    rejected: reasons.length,
    reasons,
    feed,
    cacheAgeS: ageState.ageS,
    stale: ageState.stale,
    freshness: ageState.freshness,
    liveCount: kind === "ready" ? rows.length : 0,
    summary: isObj(payload.summary) ? payload.summary : null,
    provenance,
  };
}

function inv(message, fetchedAt) {
  return {
    kind: "invalid", rows: [], errors: [], rejected: 0,
    reasons: [{ reason: message }], feed: null, cacheAgeS: null,
    stale: false, freshness: "unknown", liveCount: 0, summary: null,
    provenance: { fetchedAt, feedMode: "invalid", live: false, cached: false, unverified: true, demo: false },
  };
}

function validateCard(card) {
  if (!isObj(card)) return { ok: false, reason: "card is not an object" };
  if (typeof card.id !== "string" || card.id.length === 0) return { ok: false, reason: "missing id" };
  if (typeof card.label !== "string" || card.label.length === 0) return { ok: false, reason: "missing label" };
  if (!meaningfulTime(card.commence_time)) return { ok: false, reason: "commence_time is not a meaningful time" };
  if (!finite(card.n_books)) return { ok: false, reason: "n_books is not a number" };

  // An error card is a legitimate server response, never a prediction.
  if (card.error) {
    return {
      ok: true,
      error: {
        id: card.id, label: card.label, commence_time: card.commence_time,
        n_books: card.n_books, error: String(card.error),
        outcomes: Array.isArray(card.outcomes) ? card.outcomes : [],
      },
    };
  }

  // Confidence fields are nullable by contract, but when present they must be
  // finite probabilities. They are never clamped or coerced into range.
  for (const f of ["conf", "model_prob_a"]) {
    const v = card[f];
    if (v !== null && v !== undefined && !isProbability(v)) {
      return { ok: false, reason: `${f} not finite in [0,1]` };
    }
  }

  const outcomes = card.outcomes;
  if (!isObj(outcomes)) return { ok: false, reason: "outcomes is not a map" };
  const keys = Object.keys(outcomes);
  if (keys.length !== 2) return { ok: false, reason: `needs exactly 2 outcomes, saw ${keys.length}` };
  if (typeof card.pick !== "string" || !keys.includes(card.pick)) {
    return { ok: false, reason: "pick is not one of the outcomes" };
  }
  for (const k of keys) {
    const o = outcomes[k];
    if (!isObj(o)) return { ok: false, reason: `outcome ${k} is not an object` };
    for (const f of ["model_prob", "model_prob_free", "fair_prob"]) {
      if (!isProbability(o[f])) {
        return { ok: false, reason: `${f} for ${k} not finite in [0,1]` };
      }
    }
  }

  return {
    ok: true,
    row: {
      id: card.id,
      label: card.label,
      commence_time: card.commence_time,
      n_books: card.n_books,
      pick: card.pick,
      conf: finite(card.conf) ? card.conf : null,
      outcomes,
      modelProbA: finite(card.model_prob_a) ? card.model_prob_a : null,
      fairSource: typeof card.fair_source === "string" ? card.fair_source : null,
      warning: typeof card.warning === "string" ? card.warning : null,
      unknownPlayers: Array.isArray(card.unknown_players) ? card.unknown_players : [],
      status: isObj(card.status) ? card.status : null,
      coldStart: isObj(card.cold_start) ? card.cold_start : null,
      flags: isObj(card.flags) ? card.flags : null,
      reasons: Array.isArray(card.reasons) ? card.reasons : [],
      signals: isObj(card.signals) ? card.signals : {},
      policy: typeof card.policy === "string" ? card.policy : null,
    },
  };
}

// ---- event prediction (secondary detail; prefer board cards) ---------------

export function parsePrediction(payload, requestedId) {
  const invalid = (message) => ({ kind: "invalid", message, requestedId });
  if (!isObj(payload)) return invalid("empty prediction payload");
  if (payload.error) return invalid(String(payload.error));

  const event = payload.event;
  if (!isObj(event) || typeof event.id !== "string" || event.id.length === 0) return invalid("missing event");
  // The backend falls back to the first event for an unknown id: reject it.
  if (event.id !== requestedId) return invalid("returned event id does not match request");
  if (typeof event.label !== "string" || event.label.length === 0) return invalid("missing event label");
  if (!meaningfulTime(event.commence_time)) return invalid("event commence_time is not a strict time");

  const verdict = payload.verdict;
  if (!isObj(verdict) || typeof verdict.pick !== "string") return invalid("missing verdict");
  // Every probability/confidence field the real /api/predict verdict may carry
  // (model_prob_a, model_prob_cal_a, model_prob_free_a, model_prob_pick, conf)
  // must be a finite probability WHEN PRESENT. Not all are guaranteed, so none
  // is invented as required; none is ever clamped or normalised.
  for (const f of ["conf", "model_prob_a", "model_prob_cal_a", "model_prob_free_a", "model_prob_pick"]) {
    const v = verdict[f];
    if (v !== null && v !== undefined && !isProbability(v)) return invalid(`verdict ${f} not finite in [0,1]`);
  }

  const market = payload.market;
  if (!isObj(market)) return invalid("missing market");
  const keys = Object.keys(market);
  if (keys.length !== 2) return invalid("market needs exactly 2 outcomes");
  if (!keys.includes(verdict.pick)) return invalid("pick is not a market outcome");
  for (const k of keys) {
    const o = market[k];
    if (!isObj(o)) return invalid(`market outcome ${k} is not an object`);
    // model_prob is the core contract probability. model_prob_free and fair_prob
    // are validated WHEN PRESENT: the real /api/predict market omits fair_prob,
    // so it is never required, but a present out-of-range value (e.g. 42) must
    // never become a ready prediction.
    if (!isProbability(o.model_prob)) return invalid(`model_prob for ${k} not finite in [0,1]`);
    for (const f of ["model_prob_free", "fair_prob"]) {
      const v = o[f];
      if (v !== null && v !== undefined && !isProbability(v)) return invalid(`${f} for ${k} not finite in [0,1]`);
    }
  }

  // A detail prediction is only ready on a feed that the containment rules
  // already trust: mock/init/error/unknown feeds never become a prediction.
  const feed = typeof payload.feed === "string" ? payload.feed : null;
  if (feed !== "live" && feed !== "cache") return invalid("feed is not a verified live/cache feed");

  return {
    kind: "ready",
    requestedId,
    event,
    verdict,
    market,
    feed,
    fairSource: typeof payload.fair_source === "string" ? payload.fair_source : null,
    warning: typeof payload.warning === "string" ? payload.warning : null,
  };
}

// ---- players --------------------------------------------------------------

export function parsePlayers(payload) {
  if (!isObj(payload) || !Array.isArray(payload.players)) {
    return { kind: "invalid", reason: "players payload missing" };
  }
  const players = payload.players.filter(isObj).map((p) => ({
    name: typeof p.name === "string" ? p.name : null,
    matches: isCount(p.matches) ? p.matches : null,
    overall: finite(p.overall) ? p.overall : null,
    hard: finite(p.hard) ? p.hard : null,
    clay: finite(p.clay) ? p.clay : null,
    grass: finite(p.grass) ? p.grass : null,
    blended: finite(p.blended) ? p.blended : null,
    coldStart: p.cold_start === true,
  }));
  return {
    kind: "ready",
    surface: typeof payload.surface === "string" ? payload.surface : null,
    players,
  };
}

// Detail performs a canonical/substring fallback; the UI must display the
// RETURNED canonical name, never assume the query identity.
export function parsePlayerDetail(payload) {
  if (!isObj(payload) || typeof payload.name !== "string") {
    return { kind: "invalid", reason: "player detail payload missing a canonical name" };
  }
  if (payload.unknown === true) {
    return {
      kind: "unknown",
      name: payload.name,
      note: typeof payload.note === "string" ? payload.note : "no rating on record (cold-start containment)",
    };
  }
  const surfaces = {};
  if (isObj(payload.surfaces)) {
    for (const s of ["hard", "clay", "grass"]) {
      const v = payload.surfaces[s];
      surfaces[s] = isObj(v) ? { rating: finite(v.rating) ? v.rating : null, n: isCount(v.n) ? v.n : null } : null;
    }
  }
  const form = isObj(payload.form_last10) ? payload.form_last10 : null;
  const splits = {};
  if (isObj(payload.surface_splits)) {
    for (const [s, v] of Object.entries(payload.surface_splits)) {
      splits[s] = isObj(v)
        ? { n: isCount(v.n) ? v.n : null, wins: isCount(v.wins) ? v.wins : null, winRate: isProbability(v.win_rate) ? v.win_rate : null }
        : null;
    }
  }
  const recent = Array.isArray(payload.recent) ? payload.recent.filter(isObj).map((r) => ({
    date: validYyyymmdd(r.date) ? r.date : null,
    surface: typeof r.surface === "string" ? r.surface : null,
    round: typeof r.round === "string" ? r.round : null,
    opponent: typeof r.opponent === "string" ? r.opponent : null,
    result: r.result === "W" || r.result === "L" ? r.result : null,
  })) : [];
  const trend = Array.isArray(payload.form_trend) ? payload.form_trend.filter(isObj).map((t) => ({
    date: validYyyymmdd(t.date) ? t.date : null,
    winRate10: isProbability(t.win_rate_10) ? t.win_rate_10 : null,
  })) : [];
  return {
    kind: "ready",
    name: payload.name,
    matches: isCount(payload.matches) ? payload.matches : null,
    overall: finite(payload.overall) ? payload.overall : null,
    coldStart: payload.cold_start === true,
    surfaces,
    formLast10: form ? { n: isCount(form.n) ? form.n : null, winRate: isProbability(form.win_rate) ? form.win_rate : null } : null,
    surfaceSplits: splits,
    recent,
    formTrend: trend,
    h2hVs: isObj(payload.h2h_vs) ? payload.h2h_vs : null,
  };
}

// ---- model ----------------------------------------------------------------
//
// Missing disk artifacts may be null or nested error objects. They must render
// as missing (—), never as zero or an invented success.

export function parseModel(payload) {
  if (!isObj(payload)) return { kind: "invalid", reason: "model payload missing" };
  const anyPresent = ["sim", "calibration_validation", "column_tournament", "ablation", "gbm_freeze"]
    .some((k) => payload[k] !== undefined);
  if (!anyPresent) return { kind: "invalid", reason: "model payload has no sections" };
  return {
    kind: "ready",
    sim: isObj(payload.sim) ? payload.sim : null,
    calibration: isObj(payload.calibration_validation) ? payload.calibration_validation : null,
    tournament: isObj(payload.column_tournament) ? payload.column_tournament : null,
    ablation: isObj(payload.ablation) ? payload.ablation : null,
    freeze: isObj(payload.gbm_freeze) ? payload.gbm_freeze : null,
  };
}

// ---- reliability -----------------------------------------------------------
//
// Tennis reliability uses EQUAL-COUNT bins with p_mean / frac_pos / n. There
// are no lo/hi boundaries; don't invent them (that is the NBA schema).

export function parseReliability(payload) {
  if (!isObj(payload) || !Array.isArray(payload.bins)) {
    return { kind: "invalid", reason: "reliability payload missing" };
  }
  const bins = payload.bins.map((b, index) => {
    if (!isObj(b)) return { index, pMean: null, fracPos: null, n: null };
    return {
      index,
      pMean: isProbability(b.p_mean) ? b.p_mean : null,
      fracPos: isProbability(b.frac_pos) ? b.frac_pos : null,
      n: isCount(b.n) ? b.n : null,
    };
  });
  return {
    kind: "ready",
    n: isCount(payload.n) ? payload.n : null,
    bins,
    eceOof: finite(payload.ece_oof) ? payload.ece_oof : null,
    note: typeof payload.note === "string" ? payload.note : "",
    boundaries: "equal-count",
  };
}

// ---- demo fixtures --------------------------------------------------------
//
// Opt-in only. Never counted as live, never merged into evidence totals.

export function buildDemoBoard() {
  return parseBoard(
    {
      group: "usopen",
      feed: "demo",
      cache_age_s: null,
      matches: [
        {
          id: "demo_1", label: "Demo Player A vs Demo Player B",
          commence_time: "2030-01-01T00:00:00Z", n_books: 4, pick: "Demo Player A",
          conf: 0.61, model_prob_a: 0.61, fair_source: "demo",
          outcomes: {
            "Demo Player A": { model_prob: 0.61, model_prob_free: 0.61, fair_prob: 0.58, best_odds: 1.8, best_book: "demo", edge: 0.05, kelly_half: 0.03, field_median: 1.75, n_quotes: 4 },
            "Demo Player B": { model_prob: 0.39, model_prob_free: 0.39, fair_prob: 0.42, best_odds: 2.1, best_book: "demo", edge: null, kelly_half: 0, field_median: 2.05, n_quotes: 4 },
          },
          status: { started: false, thin: true, hours_old: -24, bettable: false },
          reasons: ["demo fixture — not a real prediction"],
          signals: {}, unknown_players: [], flags: {},
        },
      ],
      summary: { n: 1, value: 0, disagree: 0 },
    },
    { demo: true },
  );
}
