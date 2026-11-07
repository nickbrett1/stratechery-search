// TEMPORARY. The instrument for the type-ahead latency gate (design note §7:
// perceived latency under ~150 ms p95, measured on the phone, over the tailnet).
// Remove with the flag: delete this file, the `perf` calls in api.js and
// SearchPage.svelte, and the <PerfHud /> block in App.svelte.
//
// Enabled by loading any page with `?perf=1`, which is remembered in
// sessionStorage because the URL is rewritten on every keystroke and would
// otherwise drop the flag on the first one. `?perf=0` turns it off again.
//
// What it measures is *perceived* latency: from the keystroke that asked the
// question to the frame that shows the answer. That includes the debounce,
// because the reader waits for it too.
//
// The first version of this file paired the paint with the *latest keystroke*
// rather than with the keystroke its request was dispatched for, which produced
// negative debounces and 7 ms "latencies" for requests that had been in flight
// before the key was pressed. So a request now owns its sample from dispatch:
// the keystroke time is snapshotted when the debounce fires (noteDispatch), and
// the paint is measured from that. Requests superseded by a newer keystroke are
// dropped by the component and never paint, so the visible answer belongs to the
// most recent dispatch.

const STORAGE_KEY = "stratechery.perf";

function readEnabled() {
  try {
    const value = new URLSearchParams(window.location.search).get("perf");
    if (value !== null) {
      const on = value !== "0";
      sessionStorage.setItem(STORAGE_KEY, on ? "1" : "0");
      return on;
    }
    return sessionStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    // Private mode with storage denied: the flag still works for this page,
    // it just will not survive the first URL rewrite.
    return new URLSearchParams(window.location.search).has("perf");
  }
}

// A phone on cellular and a phone on wi-fi are different products; record which
// one produced the numbers, when the browser will say (Chrome, not Safari).
function connectionLabel() {
  const conn = navigator.connection;
  if (!conn) return "unknown";
  const down = conn.downlink ? `${conn.downlink}Mb/s` : "";
  return [conn.effectiveType, down].filter(Boolean).join(" ") || "unknown";
}

export const perf = $state({
  enabled: readEnabled(),
  samples: [],
  zero: 0,
  conn: connectionLabel(),
  pendingPartial: false,
});

// The sample being assembled. Module scope, not state: it is written by three
// call sites in two components and read by none.
let keyAt = 0; // the keystroke that will ask the next question
let dispatchKeyAt = 0; // …and the one the in-flight request was dispatched for
let dispatchedAt = 0;
let dispatchedTerm = "";
let fetchAt = 0;

export function noteKey() {
  if (!perf.enabled) return;
  keyAt = performance.now();
}

// Called when the request is actually issued, which is when the debounce fires —
// or immediately, for Enter.
export function noteDispatch(partial, term = "") {
  if (!perf.enabled) return;
  dispatchKeyAt = keyAt || performance.now();
  dispatchedAt = performance.now();
  dispatchedTerm = term;
  fetchAt = 0;
  perf.pendingPartial = Boolean(partial);
}

export function noteFetchStart(at) {
  if (!perf.enabled) return;
  fetchAt = at;
}

// Called from a requestAnimationFrame after the response has been rendered, so
// `now` is after a frame that had the cards in it.
export function notePaint(body) {
  if (!perf.enabled || !dispatchKeyAt) return;
  const query = body.query || {};
  // A browse (a year chip), an article load or a superseded dispatch is not the
  // answer to the question this sample is about. Only the dispatch whose term
  // this response echoes paints the sample.
  if ((query.raw ?? "") !== dispatchedTerm) return;
  const paintAt = performance.now();
  const sample = {
    q: query.raw ?? "",
    partial: Boolean(perf.pendingPartial),
    total: body.total ?? 0,
    keyToPaint: Math.round(paintAt - dispatchKeyAt),
    debounce: Math.round(dispatchedAt - dispatchKeyAt),
    network: fetchAt ? Math.round(paintAt - fetchAt) : null,
    server: typeof body.took_ms === "number" ? body.took_ms : null,
    resolved: (query.resolved || []).length,
    at: new Date().toISOString(),
  };
  dispatchKeyAt = 0; // one sample per dispatch
  if (sample.partial && sample.total === 0) perf.zero += 1;
  perf.samples = [...perf.samples, sample].slice(-300);
}

export function percentile(values, fraction) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(
    sorted.length - 1,
    Math.ceil(fraction * sorted.length) - 1,
  );
  return sorted[Math.max(0, index)];
}

export function clearSamples() {
  perf.samples = [];
  perf.zero = 0;
  dispatchKeyAt = 0;
}
