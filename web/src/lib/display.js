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
const SUBJECT_TAIL = /\s*\(\s*(?:No\s+|Free\s+)*Stratechery\b[^()]*\)\s*$/i;

export function displaySubject(subject) {
  if (!subject) return subject;
  const stripped = subject.replace(SUBJECT_TAIL, "").trim();
  // A subject that was nothing but the tail keeps it rather than becoming blank.
  return stripped || subject;
}

// "Daily Update" is one product with two names. Stratechery stopped writing
// "Daily" in the subject line in November 2021, so the archive carries `daily`
// for 2017–2021 and `update` from 2021 on: filtering by one returns nothing for
// half the archive, which is a trap nobody expects. The UI offers them as one
// facet and filters by both kinds at once, while the URL keeps the raw kinds so
// a shared link stays meaningful to the API.
export const KIND_GROUPS = [{ id: "daily-update", label: "daily update", kinds: ["daily", "update"] }];

export function kindLabel(kind) {
  const group = KIND_GROUPS.find((candidate) => candidate.kinds.includes(kind));
  return group ? group.label : kind;
}

export function groupKinds(byKind = []) {
  const seen = new Set();
  const groups = [];
  for (const group of KIND_GROUPS) {
    const members = byKind.filter((bucket) => group.kinds.includes(bucket.kind));
    if (!members.length) continue;
    members.forEach((bucket) => seen.add(bucket.kind));
    groups.push({
      id: group.id,
      label: group.label,
      kinds: group.kinds,
      articles: members.reduce((sum, bucket) => sum + (bucket.articles ?? 0), 0),
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
  if (kindActive(selected, kinds)) return selected.filter((kind) => !kinds.includes(kind));
  return [...new Set([...selected, ...kinds])];
}
