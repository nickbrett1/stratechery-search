// The querystring *is* the state (scope §4). Every route keeps its parameters
// here, so a result set is linkable, reloadable, and shareable to the phone.

const KINDS = ["update", "daily", "weekly", "interview", "other"];
const DEFAULTS = { q: "", from: "", to: "", kind: [], sender: [], hasImages: "", includeTransactional: false, limit: 20, offset: 0 };

function readSearchParams(search) {
  const params = new URLSearchParams(search);
  return {
    q: params.get("q") || "",
    from: params.get("from") || "",
    to: params.get("to") || "",
    kind: params.getAll("kind").filter((kind) => KINDS.includes(kind)),
    sender: params.getAll("sender").filter(Boolean),
    hasImages: ["true", "false"].includes(params.get("has_images")) ? params.get("has_images") : "",
    includeTransactional: params.get("include_transactional") === "true",
    limit: Number(params.get("limit")) || DEFAULTS.limit,
    offset: Number(params.get("offset")) || 0,
  };
}

export function readState() {
  return { ...DEFAULTS, ...readSearchParams(window.location.search) };
}

export function writeState(state, { replace = false } = {}) {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  for (const kind of state.kind || []) params.append("kind", kind);
  for (const sender of state.sender || []) params.append("sender", sender);
  if (state.hasImages) params.set("has_images", state.hasImages);
  if (state.includeTransactional) params.set("include_transactional", "true");
  if (state.limit && state.limit !== DEFAULTS.limit) params.set("limit", String(state.limit));
  if (state.offset) params.set("offset", String(state.offset));
  const next = `/${params.toString() ? `?${params}` : ""}`;
  if (replace) window.history.replaceState({}, "", next);
  else window.history.pushState({}, "", next);
}

export function toggle(list, value) {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

export { KINDS };
