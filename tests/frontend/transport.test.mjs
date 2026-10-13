import { test } from "node:test";
import assert from "node:assert/strict";
import { loadModule } from "./load-module.mjs";

const { createReadClient, isSameOriginApiPath } = await loadModule("sports/nba/site/transport.js");
const tennis = await loadModule("frontend/transport.js");

function jsonRes(payload, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => payload };
}

test("get_only_same_origin", async () => {
  const calls = [];
  const client = createReadClient({
    now: () => 1234,
    fetchImpl: async (url, opts) => {
      calls.push({ url, method: opts.method, credentials: opts.credentials });
      return jsonRes({ board: [], n: 0 });
    },
  });

  const ok = await client.getJson("/api/board");
  assert.equal(ok.kind, "ok");
  assert.deepEqual(ok.payload, { board: [], n: 0 });
  assert.equal(ok.fetchedAt, 1234);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].url, "/api/board");

  for (const bad of ["https://evil.example/api/board", "//evil.example/api", "./api/board", "/api/../secret", "/static/nba-app.js"]) {
    const r = await client.getJson(bad);
    assert.equal(r.kind, "invalid", `${bad} must be rejected`);
  }
  assert.equal(calls.length, 1, "rejected paths must not reach fetch");
  assert.equal(isSameOriginApiPath("/api/live/log"), true);
  assert.equal(isSameOriginApiPath("api/board"), false);
});

test("http_error_is_not_empty", async () => {
  const client = createReadClient({ fetchImpl: async () => jsonRes({ board: [], n: 0 }, { ok: false, status: 500 }) });
  const r = await client.getJson("/api/board");
  assert.equal(r.kind, "http");
  assert.notEqual(r.kind, "ok");
  assert.equal(r.fetchedAt, null);
  assert.match(r.message, /500/);
});

test("invalid_json", async () => {
  const client = createReadClient({
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("bad json"); } }),
  });
  const r = await client.getJson("/api/board");
  assert.equal(r.kind, "invalid");
  assert.equal(r.fetchedAt, null);
});

test("network_failure", async () => {
  const client = createReadClient({ fetchImpl: async () => { throw new TypeError("Failed to fetch"); } });
  const r = await client.getJson("/api/board");
  assert.equal(r.kind, "offline");
  assert.equal(r.fetchedAt, null);
});

test("timeout", async () => {
  const client = createReadClient({
    timeoutMs: 20,
    fetchImpl: (url, opts) => new Promise((_resolve, reject) => {
      opts.signal.addEventListener("abort", () => {
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
      });
    }),
  });
  const r = await client.getJson("/api/board");
  assert.equal(r.kind, "timeout");
  assert.equal(r.fetchedAt, null);
});

test("abort_is_not_user_failure", async () => {
  const controller = new AbortController();
  const client = createReadClient({
    timeoutMs: 5000,
    fetchImpl: (url, opts) => new Promise((_resolve, reject) => {
      opts.signal.addEventListener("abort", () => {
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
      });
    }),
  });
  const pending = client.getJson("/api/board", { signal: controller.signal });
  controller.abort();
  const r = await pending;
  assert.equal(r.kind, "abort");
  assert.equal(r.fetchedAt, null);
  assert.notEqual(r.kind, "http");
  assert.notEqual(r.kind, "invalid");
});

test("tennis_transport_copy_is_get_only_same_origin", async () => {
  const calls = [];
  const client = tennis.createReadClient({
    now: () => 42,
    fetchImpl: async (url, opts) => {
      calls.push({ url, method: opts.method });
      return jsonRes({ group: "usopen", feed: "live", matches: [], summary: { n: 0, value: 0, disagree: 0 } });
    },
  });
  const ok = await client.getJson("/api/board?group=usopen");
  assert.equal(ok.kind, "ok");
  assert.equal(ok.fetchedAt, 42);
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].url, "/api/board?group=usopen");

  const bad = await client.getJson("https://evil.example/api/board");
  assert.equal(bad.kind, "invalid");
  assert.equal(calls.length, 1, "rejected paths must not reach fetch");

  // The two sport copies share the same path policy.
  for (const p of ["/api/board", "/api/players/Carlos%20Alcaraz", "/api/player/x"]) {
    assert.equal(tennis.isSameOriginApiPath(p), isSameOriginApiPath(p), `${p} policy must match across sports`);
  }
});

// ---- Repair: the deadline governs the WHOLE request, through body completion.
//
// A fetch that resolves before the deadline but whose JSON body completes AFTER
// it must never surface as `ok`/with a `fetchedAt`: the response was not
// observed within the deadline. A native AbortError raised while reading the
// body is classified by WHY it aborted: the deadline -> timeout, the caller's
// signal -> abort. Both sport-local transport copies share this behaviour.

function slowJson(payload, ms) {
  return { ok: true, status: 200, json: async () => { await new Promise((r) => setTimeout(r, ms)); return payload; } };
}
function bodyAbortsOnSignal() {
  return (url, opts) => ({
    ok: true, status: 200,
    json: () => new Promise((_resolve, reject) => {
      opts.signal.addEventListener("abort", () => { const e = new Error("aborted"); e.name = "AbortError"; reject(e); });
    }),
  });
}
const transports = [
  ["nba", () => createReadClient],
  ["tennis", () => tennis.createReadClient],
];

for (const [label, factory] of transports) {
  test(`${label}_deadline_enforced_through_body_completion`, async () => {
    const client = factory()({ timeoutMs: 15, fetchImpl: async () => slowJson({ late: true }, 60) });
    const r = await client.getJson("/api/board");
    assert.equal(r.kind, "timeout", "a body completing after the deadline is never ok");
    assert.equal(r.fetchedAt, null, "no fetchedAt may be stamped after the deadline");
    assert.notEqual(r.kind, "ok");
  });

  test(`${label}_native_body_abort_timeout_and_cancel_classified`, async () => {
    const timeoutClient = factory()({ timeoutMs: 15, fetchImpl: bodyAbortsOnSignal() });
    const t = await timeoutClient.getJson("/api/board");
    assert.equal(t.kind, "timeout", "a deadline abort during the body read is a timeout");
    assert.equal(t.fetchedAt, null);

    const controller = new AbortController();
    const cancelClient = factory()({ timeoutMs: 5000, fetchImpl: bodyAbortsOnSignal() });
    const pending = cancelClient.getJson("/api/board", { signal: controller.signal });
    setTimeout(() => controller.abort(), 20);
    const c = await pending;
    assert.equal(c.kind, "abort", "a caller abort during the body read is an abort, not a failure");
    assert.equal(c.fetchedAt, null);
  });

  test(`${label}_encoded_dot_segment_api_escape_rejected`, async () => {
    const calls = [];
    const client = factory()({ fetchImpl: async (url) => { calls.push(url); return jsonRes({}); } });
    const isFn = label === "nba" ? isSameOriginApiPath : tennis.isSameOriginApiPath;

    for (const bad of ["/api/%2e%2e/static/x", "/api/%2e%2e/%2e%2e/secret", "/api/%2E%2E/static/x"]) {
      assert.equal(isFn(bad), false, `${bad} must be rejected (URL-normalised to a non-/api/ path)`);
      const r = await client.getJson(bad);
      assert.equal(r.kind, "invalid", `${bad} must never reach fetch`);
    }
    assert.equal(calls.length, 0, "encoded traversal never reaches fetch");

    // Valid encoded controls and ordinary API paths still work.
    for (const good of ["/api/board", "/api/board?group=usopen", "/api/players/Carlos%20Alcaraz", "/api", "/api/"]) {
      assert.equal(isFn(good), true, `${good} must stay allowed`);
    }
    assert.equal((await client.getJson("/api/players/Carlos%20Alcaraz")).kind, "ok");
    assert.equal(calls.length, 1);
  });

  test(`${label}_containment_uses_normalised_pathname`, async () => {
    // Containment must inspect the NORMALISED PATHNAME: harmless query dots and
    // a legitimate "A..B" path segment are not traversal, while encoded
    // traversal, origin smuggling, and raw/encoded query control characters are
    // rejected. Valid controls keep working.
    const calls = [];
    const client = factory()({ fetchImpl: async (url) => { calls.push(url); return jsonRes({}); } });
    const isFn = label === "nba" ? isSameOriginApiPath : tennis.isSameOriginApiPath;

    const good = [
      "/api/players?q=A..B",
      "/api/players?q=https://example.com/x",
      "/api/players?q=foo%2E%2Ebar",
      "/api/players/A..B",
      "/api/players/Carlos%20Alcaraz",
      "/api/board?group=usopen",
    ];
    for (const p of good) {
      assert.equal(isFn(p), true, `${p} is a same-origin API path`);
      assert.equal((await client.getJson(p)).kind, "ok", `${p} reaches fetch`);
    }

    const bad = [
      "/api/%2e%2e/static/x",
      "/api/%2E%2E/%2E%2E/secret",
      "/api/../secret",
      "https://evil.example/api/board",
      "//evil.example/api/board",
      "/api/board\n",
      "/api/board\r\nX-Injected: 1",
      "/api/board%0a",
      "/api/board?q=%0d%0aX-Injected:%201",
      "/api/board?q=%00",
      "/api/board\u0000",
      "\\api\\board",
    ];
    for (const p of bad) {
      assert.equal(isFn(p), false, `${JSON.stringify(p)} is rejected`);
      assert.equal((await client.getJson(p)).kind, "invalid", `${JSON.stringify(p)} never reaches fetch`);
    }
    assert.equal(calls.length, good.length, "only the valid requests reached fetch");
  });

  test(`${label}_blocking_same_turn_fetch_or_body_cannot_be_ok_after_deadline`, async () => {
    // A synchronous busy-wait inside the fetch OR the body monopolises the turn
    // past the deadline, so the timeout timer callback cannot run first. The
    // absolute elapsed deadline must still classify the result as a timeout and
    // never stamp an ok/fetchedAt.
    const blockUntil = (ms) => { const end = Date.now() + ms; while (Date.now() < end) { /* block */ } };
    const blockedFetch = factory()({
      timeoutMs: 5,
      fetchImpl: async () => { blockUntil(40); return { ok: true, status: 200, json: async () => ({ late: "fetch" }) }; },
    });
    const a = await blockedFetch.getJson("/api/board");
    assert.equal(a.kind, "timeout", "a same-turn fetch past the deadline is not ok");
    assert.equal(a.fetchedAt, null);

    const blockedBody = factory()({
      timeoutMs: 5,
      fetchImpl: async () => ({ ok: true, status: 200, json: async () => { blockUntil(40); return { late: "body" }; } }),
    });
    const b = await blockedBody.getJson("/api/board");
    assert.equal(b.kind, "timeout", "a same-turn body past the deadline is not ok");
    assert.equal(b.fetchedAt, null);
    assert.notEqual(b.kind, "ok");
  });

  test(`${label}_deadline_actively_resolves_a_noncooperative_body`, async () => {
    // A body that ignores AbortSignal and NEVER settles must still be resolved
    // BY THE DEADLINE rather than awaited forever. The promise race against a
    // HUNG sentinel keeps a broken transport from hanging the suite.
    const client = factory()({
      timeoutMs: 25,
      fetchImpl: async () => ({ ok: true, status: 200, json: () => new Promise(() => {}) }),
    });
    const raced = await Promise.race([
      client.getJson("/api/board"),
      new Promise((resolve) => setTimeout(() => resolve({ kind: "HUNG" }), 1500)),
    ]);
    assert.equal(raced.kind, "timeout", "the deadline must actively resolve a never-settling body");
    assert.equal(raced.fetchedAt, null);
  });

  test(`${label}_caller_cancel_beats_a_late_successful_abort_ignoring_body`, async () => {
    // The caller cancels; the body ignores AbortSignal and ONLY THEN completes
    // successfully. The result must be abort, and it must never become ok.
    const controller = new AbortController();
    let releaseBody = () => {};
    const client = factory()({
      timeoutMs: 5000,
      fetchImpl: async () => ({
        ok: true, status: 200,
        json: () => new Promise((resolve) => { releaseBody = () => resolve({ late: true }); }),
      }),
    });
    const pending = client.getJson("/api/board", { signal: controller.signal });
    await new Promise((r) => setTimeout(r, 20));
    controller.abort();
    const raced = await Promise.race([
      pending,
      new Promise((resolve) => setTimeout(() => resolve({ kind: "HUNG" }), 1500)),
    ]);
    assert.equal(raced.kind, "abort", "caller cancellation must resolve even when the body ignores abort");
    assert.notEqual(raced.kind, "ok");
    assert.equal(raced.fetchedAt, null);
    // Let the abort-ignoring body complete successfully; the settled result
    // must remain abort (never ok) because the caller's signal is aborted.
    releaseBody();
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(await pending, raced, "a late successful body cannot turn a cancellation into ok");
  });
}
