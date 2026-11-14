/**
 * The design-system conversion, checked as rendered output rather than as
 * source. Two properties are only visible in a browser:
 *
 * 1. a token is *applied*, not merely present - so every assertion reads a
 *    computed style, which is the only oracle that distinguishes "the theme
 *    installed into app.css" from "the theme is sitting in app.css unused";
 * 2. the house blocks are on screen with their own data - a StatStrip with four
 *    cards, a StatusView lane per kind, a ResultList row per neighbour, and an
 *    EmptyState whose advice matches the cause of the emptiness.
 *
 * The API is stubbed with `page.route`, so this measures the UI and not the
 * archive: the backend is Python and its correctness is not what is under test.
 */
import { test, expect } from "@playwright/test";

const stats = {
  articles: 1691,
  searchable_articles: 1662,
  chunks: 38104,
  transactional: 29,
  images: { distinct_assets: 812, articles_with_images: 640 },
  by_year: [
    { year: 2018, articles: 11 },
    { year: 2019, articles: 92 },
    { year: 2024, articles: 240 },
  ],
  by_kind: [
    { kind: "daily", articles: 1058 },
    { kind: "update", articles: 556 },
    { kind: "interview", articles: 48 },
  ],
  notes: [],
  first: "2018-01-02T00:00:00Z",
  last: "2026-07-15T00:00:00Z",
  searchable: { daily_from: "2019-06-03" },
  index: {
    flattener_version: "3",
    chunker_version: "2",
    built_at: "2026-11-01T00:00:00Z",
  },
};

const emptySearch = {
  query: { q: "zzz", expansions: [] },
  results: [],
  total: 0,
  article_total: 0,
  offset: 0,
  limit: 10,
  took_ms: 12,
};

const article = {
  message_id: "152",
  kind: "daily",
  sent_at: "2026-07-15T00:00:00Z",
  subject: "IBM Misses",
  from_header: "Stratechery <stratechery@stratechery.com>",
  words: 1200,
  chars: 7200,
  est_tokens: 1800,
  is_transactional: false,
  images: [],
  text: "First paragraph about IBM.\n\nSecond paragraph about Amazon.",
  chunks: [],
};

const similar = {
  results: [
    {
      chunk_id: "152:1",
      article_href: "/article/150",
      subject: "More on Amazon",
      kind: "daily",
      sent_at: "2026-07-10T00:00:00Z",
      excerpt: "Amazon's logistics build-out",
      distance: 0.187,
      more_passages: 2,
      matched_passages: 3,
    },
  ],
};

// Only the app's own `fetch` calls are stubbed. `**/stats` also matches the
// document navigation to /stats, and fulfilling that turned the page into a JSON
// document with no #app at all - the stub has to yield to the server for
// anything that is not a fetch.
function stubJson(page, pattern, body) {
  return page.route(pattern, (route) =>
    route.request().resourceType() === "fetch"
      ? route.fulfill({ json: body })
      : route.fallback(),
  );
}

async function stub(page) {
  // Registration order is match priority, last first: `/article/152/similar`
  // must reach the `similar` stub, not the article one.
  await stubJson(page, "**/stats", stats);
  await stubJson(page, "**/article/**", article);
  await stubJson(page, "**/similar**", similar);
  await stubJson(page, "**/search**", emptySearch);
}

test("the registry theme is applied, not merely installed", async ({
  page,
}) => {
  await stub(page);
  await page.goto("/stats");

  // --bg is a Tailwind `@theme inline` variable in the consumer's app.css. If
  // the merge wrote the tokens but nothing resolved them, this reads "".
  const ground = await page.evaluate(() =>
    getComputedStyle(document.body).getPropertyValue("--bg").trim(),
  );
  expect(ground).toBe("#0f172a");

  const bodyBackground = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor,
  );
  expect(bodyBackground).toBe("rgb(15, 23, 42)");
});

test("the corpus page is the house blocks", async ({ page }) => {
  await stub(page);
  await page.goto("/stats");

  // StatStrip: four KPI cards, each with a tabular figure.
  await expect(page.locator(".stat-strip .stat-card")).toHaveCount(4);
  await expect(page.locator(".stat-strip .stat-value").first()).toHaveText(
    "1,691",
  );

  // The card fill and radius come from the theme, not from this project.
  const radius = await page.evaluate(() => {
    const card = document.querySelector(".stat-strip .stat-card");
    return card ? getComputedStyle(card).borderRadius : "";
  });
  expect(radius).toBe("8px");

  // StatusView: one lane per kind, counts as pills. Two lanes, not three:
  // daily and update are one product under two names, which is a rule of the
  // corpus rather than of the block. The count is the group's, 1,058 + 556.
  await expect(page.locator(".status-card .lane-row")).toHaveCount(2);
  await expect(page.locator(".status-card .lane-row .pill").first()).toHaveText(
    "1,614",
  );
});

test("the article page uses the ranked list for neighbours", async ({
  page,
}) => {
  await stub(page);
  await page.goto("/article/152");

  await expect(page.locator("header.card h1")).toBeVisible();
  const list = page.locator(".result-list-wrap");
  await expect(list).toHaveCount(1);
  await expect(list.locator(".row-title")).toHaveText("More on Amazon");
  await expect(list.locator(".row-meta")).toContainText("distance 0.187");
  await expect(list.locator(".pill")).toHaveText("daily update");
});

test("zero results advise on the cause, not on the symptom", async ({
  page,
}) => {
  await stub(page);
  await page.goto("/?q=zzz");

  const empty = page.locator(".empty-state");
  await expect(empty).toBeVisible();
  // The block supplies the shape; the sentence stays the project's, because it
  // is contract copy ("No passages match" is what the suite and the design note
  // both pin - it must never read as "this corpus has nothing for you").
  await expect(empty).toContainText("No passages match. Try fewer words.");
  // A query that matched nothing is not a filter problem: no clear button.
  await expect(empty.getByRole("button")).toHaveCount(0);
});

test("the app is inside the shell, and the shell is not the page's heading", async ({
  page,
}) => {
  await stub(page);
  await page.goto("/article/152");

  // AppShell, not a bespoke header: the bar, its 68rem inner and its slots are
  // the block's, and the back control is the app's own button handed into the
  // `back` slot -- a link would push an entry where history.back() pops one.
  const bar = page.locator(".topbar .topbar-inner");
  await expect(bar).toBeVisible();
  await expect(bar.locator(".mark img")).toBeVisible();
  await expect(bar.getByRole("button", { name: /back/i })).toHaveCount(1);
  await expect(bar.locator("a.pill.back")).toHaveCount(0);

  // `titleTag="div"`: the wordmark is the product's name, and the article's
  // subject is the one h1 on the page. Two h1s would be a document outline with
  // two things claiming to be the same thing.
  await expect(page.locator(".wordmark")).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expect(page.locator("h1.wordmark")).toHaveCount(0);

  // The bar is one row: the wordmark truncates rather than wrapping the sticky
  // header into two, and the nav is not squeezed into a second row either.
  const barBox = await page.locator(".topbar").boundingBox();
  expect(barBox.height).toBeLessThan(70);
});

test("the year facet is one choice from N, drawn as a histogram", async ({
  page,
}) => {
  // The last piece that was not a block. A year range is not a set of
  // checkboxes: 1.7.0's `mode: "single"` renders radios, selecting replaces,
  // and clicking the active one clears the range — and `layout: "bars"` draws
  // each year's count behind it, so the archive's thin start is visible before
  // the click.
  const facets = {
    by_year: [
      { year: 2018, articles: 11, count: 120 },
      { year: 2019, articles: 92, count: 480 },
    ],
    by_kind: [{ kind: "daily", articles: 1058, count: 9000 }],
  };
  await page.route("**/search**", (route) =>
    route.request().resourceType() === "fetch"
      ? route.fulfill({
          json: { ...emptySearch, facets, query: { q: "ibm", expansions: [] } },
        })
      : route.fallback(),
  );
  await page.goto("/?q=ibm");

  const group = (title) =>
    page.locator(".facet-group").filter({ hasText: title });
  const year = group("Year");

  // Radios, all in one group, so the control says what it does.
  const radios = year.locator('input[type="radio"]');
  await expect(radios).toHaveCount(2);
  const names = await radios.evaluateAll((all) =>
    all.map((input) => input.name),
  );
  expect(new Set(names).size).toBe(1);

  // A histogram, not a number at the end of a row: 120 against 480 is a
  // quarter-width bar against a full one.
  const widths = await year
    .locator(".option-bar")
    .evaluateAll((bars) =>
      bars.map((bar) => parseFloat(getComputedStyle(bar).width)),
    );
  expect(widths[1]).toBeGreaterThan(0);
  expect(widths[0] / widths[1]).toBeCloseTo(0.25, 1);

  // Kind is still a set: one checkbox per group, more than one can be on.
  await expect(group("Kind").locator('input[type="checkbox"]')).toHaveCount(1);

  await year.locator(".facet-option").filter({ hasText: "2019" }).click();
  await expect(page).toHaveURL(/from=2019-01-01/);
  await expect(page).toHaveURL(/to=2019-12-31/);
  await expect(radios.nth(1)).toBeChecked();

  // The way back. A single-select facet whose only exit is "clear all" would
  // throw every other facet away with it.
  await year.locator(".facet-option").filter({ hasText: "2019" }).click();
  await expect(page).not.toHaveURL(/from=/);
  await expect(radios.nth(1)).not.toBeChecked();
});
