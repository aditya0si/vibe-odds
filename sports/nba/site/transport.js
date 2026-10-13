// Court Evidence — NBA read transport (dependency-free ES module).
//
// createReadClient({fetchImpl, now, timeoutMs}) -> { getJson(path, {signal}) }
//
// GET only, same-origin relative /api/ paths only. Every outcome is a tagged
// result so callers never confuse a failure with an empty payload:
//   { kind:'ok',      payload, fetchedAt }
//   { kind:'http',    message, status, fetchedAt:null }   non-2xx
//   { kind:'invalid', message, fetchedAt:null }           bad path / non-JSON
//   { kind:'offline', message, fetchedAt:null }           transport failure
//   { kind:'timeout', message, fetchedAt:null }           deadline elapsed
//   { kind:'abort',   message, fetchedAt:null }           caller superseded it
//
// Never retries automatically. 'abort' is deliberately NOT a user-facing
// failure: a superseded request must not surface an error.

export function createReadClient({ fetchImpl, now, timeoutMs = 10000 } = {}) {
  const doFetch = fetchImpl || (typeof fetch === "function" ? fetch.bind(globalThis) : null);
  const clock = typeof now === "function" ? now : () => Date.now();
  // A monotonic elapsed-time source for the absolute deadline. `clock()` is only
  // used to stamp `fetchedAt`; the deadline must not be fooled by a wall-clock
  // jump and must still be enforced when a synchronous fetch/body blocks the
  // event loop past the deadline (the timer callback cannot run until the turn
  // yields, so a same-turn resolution could otherwise be stamped ok).
  const monotonic = (typeof performance === "object" && performance && typeof performance.now === "function")
    ? () => performance.now()
    : () => Date.now();

  return { getJson };

  async function getJson(path, { signal } = {}) {
    if (!isSameOriginApiPath(path)) {
      return inj(`invalid request path: ${String(path)}`);
    }
    if (!doFetch) {
      return { kind: "offline", message: "no fetch implementation", fetchedAt: null };
    }

    const controller = new AbortController();
    let timedOut = false;
    let settled = false;
    const deadlineAt = monotonic() + timeoutMs;
    const pastDeadline = () => monotonic() >= deadlineAt;

    // Superseding gate: it resolves the request the instant the deadline
    // elapses or the caller cancels, whether or not the fetch/body cooperates
    // with AbortSignal. The operation races it so a body that never settles can
    // never hang getJson, and a caller cancellation can never be overwritten by
    // a body that completes successfully afterwards.
    let gateResolve;
    const gate = new Promise((resolve) => { gateResolve = resolve; });
    const settle = (result) => { if (!settled) { settled = true; gateResolve(result); } };

    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
      settle(timeout());
    }, timeoutMs);

    let relay = null;
    if (signal) {
      if (signal.aborted) {
        clearTimeout(timer);
        settled = true;
        return aborted("request aborted before start");
      }
      relay = () => { controller.abort(); settle(aborted("request aborted")); };
      signal.addEventListener("abort", relay);
    }

    const operation = (async () => {
      try {
        const res = await doFetch(path, {
          method: "GET",
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!res || typeof res.ok !== "boolean") {
          return (timedOut || pastDeadline()) ? timeout() : inj("invalid fetch response");
        }
        if (!res.ok) {
          if (timedOut || pastDeadline()) return timeout();
          return { kind: "http", message: `HTTP ${res.status}`, status: res.status, fetchedAt: null };
        }
        let payload;
        try {
          payload = await res.json();
        } catch (err) {
          // A body read that aborts is classified by WHY it aborted. The inner
          // catch must not mask a deadline/cancel as "invalid JSON".
          if (timedOut) return timeout();
          if (signal && signal.aborted) return aborted("request aborted");
          if (pastDeadline()) return timeout();
          if (err && err.name === "AbortError") return timeout();
          return inj("invalid JSON");
        }
        // A successful body must still yield to a deadline already elapsed or a
        // caller cancellation already observed: never ok after the deadline and
        // never ok after cancellation. The elapsed check covers a same-turn body
        // that blocked the event loop past the deadline before the timer ran.
        if (timedOut) return timeout();
        if (signal && signal.aborted) return aborted("request aborted");
        if (pastDeadline()) return timeout();
        return { kind: "ok", payload, fetchedAt: clock() };
      } catch (err) {
        if (timedOut) return timeout();
        if (signal && signal.aborted) return aborted("request aborted");
        if (pastDeadline()) return timeout();
        if (err && err.name === "AbortError") return timeout();
        return { kind: "offline", message: String((err && err.message) || err), fetchedAt: null };
      }
    })();

    try {
      return await Promise.race([operation, gate]);
    } finally {
      settled = true;
      clearTimeout(timer);
      if (signal && relay) signal.removeEventListener("abort", relay);
    }
  }
}

function timeout() {
  return { kind: "timeout", message: "request timed out", fetchedAt: null };
}

function inj(message) {
  return { kind: "invalid", message, fetchedAt: null };
}

function aborted(message) {
  return { kind: "abort", message, fetchedAt: null };
}

// Any raw control character (NUL..US, DEL) or a percent-encoded control byte
// (%00..%1f, %7f) is forbidden anywhere in the request target: a query cannot
// smuggle a CRLF/NUL control sequence even though it cannot change the origin.
const FORBIDDEN_CONTROL = /[\u0000-\u001f\u007f]|%(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7[fF])/;

// Same-origin, absolute-path API route. Rejects schemes, protocol-relative
// hosts, backslashes, control characters, and real traversal segments.
//
// The check inspects the NORMALISED PATHNAME only. Percent-encoded dot segments
// ("%2e") are dot segments to a URL parser, so the path is normalised against a
// fixed same-origin base first: otherwise "/api/%2e%2e/static/x" would be
// accepted here but fetched as "/static/x". Because only the pathname is
// inspected, harmless query data ("?q=A..B", "?q=https://x") and a legitimate
// "A..B" path SEGMENT are not mistaken for traversal. Origin containment is
// enforced by requiring the parsed origin to equal the fixed base.
export function isSameOriginApiPath(path) {
  if (typeof path !== "string" || path.length === 0) return false;
  if (path[0] !== "/") return false;
  if (path.startsWith("//")) return false;
  if (path.includes("\\")) return false;
  if (FORBIDDEN_CONTROL.test(path)) return false;
  let url;
  try {
    url = new URL(path, "http://court.invalid");
  } catch (_e) {
    return false;
  }
  if (url.origin !== "http://court.invalid") return false;
  const segments = url.pathname.split("/");
  if (segments.includes(".") || segments.includes("..")) return false;
  return url.pathname.startsWith("/api/") || url.pathname === "/api";
}
