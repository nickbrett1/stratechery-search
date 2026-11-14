// The bridge between this API's facet payload and FilterPanel's one shape.
//
// The panel renders `[{ key, label, options: [{ value, label, count }] }]` and
// reports a selection as `{ [key]: [value, ...] }`. The API emits `by_year`,
// `by_kind`, `by_entity` and `has_images`, and the URL speaks `from`/`to`,
// `kind`, `entity`, `has_images`. Both translations live here rather than in the
// page: the query shape is this app's (scope §4 — the querystring *is* the
// state), and the selection shape is the block's.
//
// The year facet is one choice from N and drawn as a histogram, which is what
// 1.7.0 added `mode: "single"` and `layout: "bars"` for: a range is not a set
// of checkboxes, and "how much is in 2018" is a question the bar answers before
// the click.
import { groupKinds, kindActive, KIND_GROUPS } from "./display.js";

function yearFacet(facets) {
  const buckets = facets.by_year || [];
  if (!buckets.length) return null;
  return {
    key: "year",
    label: "Year",
    mode: "single",
    layout: "bars",
    options: buckets.map((bucket) => ({
      value: String(bucket.year),
      label: String(bucket.year),
      count: bucket.count,
    })),
    note: "passages per year",
  };
}

function kindFacet(facets) {
  const kinds = groupKinds(facets.by_kind || []);
  if (!kinds.length) return null;
  const total = kinds.reduce((sum, group) => sum + group.count, 0);
  return {
    key: "kind",
    label: "Kind",
    options: kinds.map((group) => ({
      value: group.id,
      label: group.label,
      count: group.count,
    })),
    note: `${total} passages across ${kinds.length} kinds`,
  };
}

// Alphabetical, not by count. The API hands entities over largest-first, which
// is the right order for *choosing* a facet dimension but the wrong order for
// *finding* one: 38 companies sorted by magnitude move whenever the corpus
// grows, so a name's position is never learnable. The count is still shown, so
// size is not hidden — it is just no longer the sort key.
function entityFacets(facets) {
  const rows = facets.by_entity || [];
  const byName = (left, right) => left.name.localeCompare(right.name);
  return [
    { title: "Company", rows: rows.filter((r) => r.kind === "company") },
    { title: "People", rows: rows.filter((r) => r.kind === "person") },
  ]
    .filter((dimension) => dimension.rows.length)
    .map((dimension) => ({
      // One key for both lists: a company and a person are the same filter, and
      // two keys would let the panel hold two selections the URL cannot.
      key: "entity",
      label: dimension.title,
      options: [...dimension.rows].sort(byName).map((row) => ({
        value: row.entity_id,
        label: row.name,
        count: row.articles,
      })),
      note: "articles mentioning each, not passages",
    }));
}

function imagesFacet(facets) {
  const counts = facets.has_images;
  if (!counts) return null;
  return {
    key: "images",
    label: "Images",
    mode: "single",
    options: [
      { value: "true", label: "with images", count: counts.true ?? 0 },
      { value: "false", label: "text only", count: counts.false ?? 0 },
    ],
  };
}

export function facetGroups(facets) {
  if (!facets) return [];
  return [
    yearFacet(facets),
    kindFacet(facets),
    ...entityFacets(facets),
    imagesFacet(facets),
  ].filter(Boolean);
}

// Which group ids the URL's `kind` covers. Un-grouped kinds (a URL can carry
// any kind the API accepts) are their own option, so a shared link still shows
// what it selected.
function kindSelection(selected = []) {
  const ids = new Set();
  for (const group of KIND_GROUPS) {
    if (kindActive(selected, group.kinds)) ids.add(group.id);
  }
  for (const kind of selected) {
    const grouped = KIND_GROUPS.some((group) => group.kinds.includes(kind));
    if (!grouped) ids.add(kind);
  }
  return [...ids];
}

export function selectionFromQuery(query) {
  const from = /^(\d{4})-01-01$/.exec(query.from || "");
  const year = from && query.to === `${from[1]}-12-31` ? [from[1]] : undefined;
  return {
    ...(year ? { year } : {}),
    ...(query.kind?.length ? { kind: kindSelection(query.kind) } : {}),
    ...(query.entity?.length ? { entity: query.entity } : {}),
    ...(query.hasImages ? { images: [query.hasImages] } : {}),
  };
}

export function queryFromSelection(query, selection = {}) {
  const year = selection.year?.[0];
  const kinds = (selection.kind ?? []).flatMap((id) => {
    const group = KIND_GROUPS.find((candidate) => candidate.id === id);
    return group ? group.kinds : [id];
  });
  return {
    ...query,
    offset: 0,
    from: year ? `${year}-01-01` : "",
    to: year ? `${year}-12-31` : "",
    kind: kinds,
    entity: selection.entity ?? [],
    hasImages: selection.images?.[0] ?? "",
  };
}
