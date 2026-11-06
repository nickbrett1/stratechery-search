// Thin client for the frozen §3 contract. No state, no cache: the URL is the
// state, and a reload must produce the same request the reader sees.

const JSON_HEADERS = { Accept: "application/json" };

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
  if (state.limit) params.set("limit", String(state.limit));
  if (state.offset) params.set("offset", String(state.offset));
  // One row per article, its other matching passages folded under the best one.
  // The reader is looking for articles, not for the same 2018 update four times
  // because it happens to be chunked; `limit` and `offset` then count articles.
  params.set("group", "article");
  return params.toString();
}

export const search = (state) => getJson("/search", searchQuery(state));
export const article = (messageId) =>
  getJson(`/article/${encodeURIComponent(messageId)}`);
export const stats = () => getJson("/stats");
export const health = () => getJson("/healthz");
