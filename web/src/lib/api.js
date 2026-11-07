// Thin client for the frozen §3 contract. No state, no cache: the URL is the
// state, and a reload must produce the same request the reader sees.

const JSON_HEADERS = { Accept: "application/json" };
// Cards per response while typing. See the note in searchQuery().
const TYPEAHEAD_LIMIT = 10;

async function getJson(path, params) {
  const url = params ? `${path}?${params.toString()}` : path;
  const response = await fetch(url, { headers: JSON_HEADERS });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = body.error || {};
    const failure = new Error(
      error.message || `request failed (${response.status})`,
    );
    failure.code = error.code || "request_failed";
    failure.detail = error.detail || {};
    failure.status = response.status;
    throw failure;
  }
  return body;
}

export function searchQuery(state) {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  for (const kind of state.kind || []) params.append("kind", kind);
  for (const sender of state.sender || []) params.append("sender", sender);
  if (state.hasImages === "true" || state.hasImages === "false") {
    params.set("has_images", state.hasImages);
  }
  // Ten cards while the reader is still typing, not twenty: half the payload is
  // half the wait on a phone (11.7 KB → 6.9 KB gzipped, measured 2026-11-07), and
  // a half-typed word rarely has twenty results worth reading. Enter asks for
  // the page size again.
  const limit = state.partial
    ? Math.min(state.limit || 20, TYPEAHEAD_LIMIT)
    : state.limit;
  if (limit) params.set("limit", String(limit));
  if (state.offset) params.set("offset", String(state.offset));
  // One row per article, its other matching passages folded under the best one.
  // The reader is looking for articles, not for the same 2018 update four times
  // because it happens to be chunked; `limit` and `offset` then count articles.
  params.set("group", "article");
  // Type-ahead only: the last token is half-typed, so it is matched by prefix
  // instead of as a whole word. Additive and off by default — the MCP adapter and
  // a bookmarked URL keep meaning "a complete question" (design note §7).
  if (state.partial) params.set("partial", "1");
  return params.toString();
}

export const search = (state) => getJson("/search", searchQuery(state));
export const article = (messageId) =>
  getJson(`/article/${encodeURIComponent(messageId)}`);
export const stats = () => getJson("/stats");
export const health = () => getJson("/healthz");
