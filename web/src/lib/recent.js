// The reader's own recent searches — the one piece of client state the URL does
// not carry, because a shared link must not leak what someone else typed.
//
// Everything here is best-effort: Safari's private mode and a browser with site
// data disabled both throw on `localStorage`, and a search box that breaks
// because a history list could not be written would be a worse app than one with
// no history at all.

const KEY = "stratechery-search:recent";
const LIMIT = 5;

function store() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readRecent() {
  try {
    const raw = store()?.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(list)) return [];
    return list
      .filter((term) => typeof term === "string" && term.trim())
      .slice(0, LIMIT);
  } catch {
    return [];
  }
}

function write(list) {
  try {
    store()?.setItem(KEY, JSON.stringify(list.slice(0, LIMIT)));
  } catch {
    // No history, then. Nothing else depends on it.
  }
}

// Re-searching a term moves it to the front rather than adding a duplicate, and
// the comparison ignores case: "Amazon" and "amazon" are the same search.
export function remember(term) {
  const clean = (term ?? "").trim();
  if (!clean) return readRecent();
  const rest = readRecent().filter(
    (existing) => existing.toLowerCase() !== clean.toLowerCase(),
  );
  const list = [clean, ...rest].slice(0, LIMIT);
  write(list);
  return list;
}

export function forget(term) {
  const list = readRecent().filter((existing) => existing !== term);
  write(list);
  return list;
}

export function forgetAll() {
  write([]);
  return [];
}
