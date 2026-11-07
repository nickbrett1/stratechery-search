// What the reader sees, as opposed to what the index stores.
//
// The API returns the archive exactly as it arrived: subjects still carry the
// "(Stratechery Update 3-4-2021)" tail the mailing list put on them, and `kind`
// still says `daily` for the era before Stratechery dropped the word. Both are
// normalised here, once, rather than in every component that renders a title or
// a kind.

// The tail repeats the date and the product that the card already shows in the
// pill and the date line — 20 to 35 characters of it, on every result, in text
// that has to fit a phone. "No Stratechery Daily Update" is how the archive marks
// a skipped day, so it is stripped too.
//
// Deliberately not one regex: an end-anchored pattern with optional prefixes in
// front of `[^()]*` backtracks super-linearly on a long title with a stray
// bracket, which eslint flags and a phone would feel. Locating the final
// parenthetical by index is linear and says what it means.
function tailStart(subject) {
  const end = subject.lastIndexOf(")");
  if (end === -1 || subject.slice(end + 1).trim() !== "") return -1;
  // Walk back through the opening brackets rather than taking the last one: two
  // subjects in the archive are missing their closing bracket —
  // "(Stratechery Daily Update (3-3-2020)" — so the outer "(" is the tail's.
  for (let start = subject.lastIndexOf("(", end); start !== -1;) {
    const inner = subject
      .slice(start + 1, end)
      .replace(/^(?:No|Free)\s+/i, "")
      .trimStart();
    if (/^Stratechery\b/i.test(inner)) return start;
    start = start > 0 ? subject.lastIndexOf("(", start - 1) : -1;
  }
  return -1;
}

export function displaySubject(subject) {
  if (!subject) return subject;
  const at = tailStart(subject);
  if (at === -1) return subject;
  const stripped = subject.slice(0, at).trim();
  // A subject that was nothing but the tail keeps it rather than becoming blank.
  return stripped || subject;
}

// "Daily Update" is one product with two names. Stratechery stopped writing
// "Daily" in the subject line in November 2021, so the archive carries `daily`
// for 2017–2021 and `update` from 2021 on: filtering by one returns nothing for
// half the archive, which is a trap nobody expects. The UI offers them as one
// facet and filters by both kinds at once, while the URL keeps the raw kinds so
// a shared link stays meaningful to the API.
export const KIND_GROUPS = [
  { id: "daily-update", label: "daily update", kinds: ["daily", "update"] },
];

export function kindLabel(kind) {
  const group = KIND_GROUPS.find((candidate) => candidate.kinds.includes(kind));
  return group ? group.label : kind;
}

export function groupKinds(byKind = []) {
  const seen = new Set();
  const groups = [];
  for (const group of KIND_GROUPS) {
    const members = byKind.filter((bucket) =>
      group.kinds.includes(bucket.kind),
    );
    if (!members.length) continue;
    members.forEach((bucket) => seen.add(bucket.kind));
    groups.push({
      id: group.id,
      label: group.label,
      kinds: group.kinds,
      articles: members.reduce(
        (sum, bucket) => sum + (bucket.articles ?? 0),
        0,
      ),
      count: members.reduce((sum, bucket) => sum + (bucket.count ?? 0), 0),
    });
  }
  for (const bucket of byKind) {
    if (seen.has(bucket.kind)) continue;
    groups.push({
      id: bucket.kind,
      label: bucket.kind,
      kinds: [bucket.kind],
      articles: bucket.articles ?? 0,
      count: bucket.count ?? 0,
    });
  }
  return groups.sort((left, right) => left.label.localeCompare(right.label));
}

export function kindActive(selected = [], kinds = []) {
  return kinds.every((kind) => selected.includes(kind));
}

export function toggleKindGroup(selected = [], kinds = []) {
  if (kindActive(selected, kinds))
    return selected.filter((kind) => !kinds.includes(kind));
  return [...new Set([...selected, ...kinds])];
}

// The words of a query, extracted exactly as the API extracts them
// (`_WORD_RE` in api.py). Anything that is not a word-run is punctuation the
// server has already made inert, and highlighting it would be a promise the
// result did not make.
// The same word-runs the API extracts (`_WORD_RE` in api.py), kept in step by
// hand: a divergence shows up as a term highlighted that was never searched for.
// It is the pattern the server runs per request, and each repetition consumes
// at least one character, so the scanner's ReDoS heuristic does not apply.
// eslint-disable-next-line security/detect-unsafe-regex
const WORD_RUNS = /[^\W_]+(?:[-'][^\W_]+)*/gu;

export function queryTerms(raw) {
  return [...(String(raw || "").match(WORD_RUNS) ?? [])];
}

// A drill-in keeps the question that produced it. Without it the article page
// cannot say what the reader was looking for, and a passage link from a search
// and a passage link from nowhere look identical.
//
// The query goes *before* the fragment: `#c152-3` has to survive for the
// scroll-to-passage to work, and a fragment is the last thing in a URL.
export function articleHref(href, raw) {
  const term = String(raw || "").trim();
  if (!term) return href;
  const [path, hash] = href.split("#");
  const joiner = path.includes("?") ? "&" : "?";
  const fragment = hash ? "#" + hash : "";
  return path + joiner + "q=" + encodeURIComponent(term) + fragment;
}

// The characters that would change the meaning of the pattern. Written as a
// blocklist rather than trusting the query: a reader can type `(` and `*`.
const REGEXP_SPECIALS = /[.*+?^${}()|[\]\\]/g;

function escapeRegExp(text) {
  return text.replace(REGEXP_SPECIALS, "\\$&");
}

// Split prose into matched and unmatched runs so the caller can render <mark>
// around the matches. It returns text, never HTML: the article body and the
// query are both untrusted as markup, and `{@html}` here would be a hole.
//
// The rule is "a word beginning with this term", not "this word exactly", and
// that is deliberate: the index is Porter-stemmed, so `integration` really does
// return `integrated` and `integrating`. An exact-word highlight would leave the
// passage the reader clicked dark and highlight nothing at all in the common
// case. Whole-word matching would be prettier and would misreport the search.
export function highlightParts(text, terms = []) {
  const words = terms.filter(Boolean);
  if (!words.length || !text) return [{ text, hit: false }];
  const pattern = words.map((word) => `${escapeRegExp(word)}[\\w-]*`).join("|");
  // The query is a `new RegExp` argument on purpose; every term in it went
  // through escapeRegExp above, so it contributes no operators.
  // eslint-disable-next-line security/detect-non-literal-regexp
  const matcher = new RegExp(`\\b(?:${pattern})`, "giu");
  const parts = [];
  let cursor = 0;
  for (const match of text.matchAll(matcher)) {
    if (match.index > cursor)
      parts.push({ text: text.slice(cursor, match.index), hit: false });
    parts.push({ text: match[0], hit: true });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length)
    parts.push({ text: text.slice(cursor), hit: false });
  return parts;
}
