import { test as base, expect } from "@playwright/test";

/**
 * Every test in this file runs under a console guard.
 *
 * This is the whole reason the suite exists. A Svelte template that referenced
 * an undeclared `browse` shipped green through eslint, `vite build` and 70
 * Python contract tests, and rendered an empty results pane for every search:
 * the failure was a runtime ReferenceError inside the component, which only a
 * browser can see. So a console error or an uncaught page error fails the test,
 * whether or not the assertions in it happened to pass.
 */
const test = base.extend({
  page: async ({ page }, use) => {
    const failures = [];
    page.on("pageerror", (error) =>
      failures.push(`pageerror: ${error.message}`),
    );
    page.on("console", (message) => {
      // `warning` is not a failure; an `error` is, and chromium reports failed
      // resource loads here too, which is how a missing asset gets caught.
      if (message.type() === "error") {
        failures.push(`console: ${message.text()}`);
      }
    });
    await use(page);
    expect(failures, "the page logged an error while rendering").toEqual([]);
  },
});

const card = ".card.result";
// A browse card has no matched passage to point at, so its footer is the whole
// affordance ("read →"), asserted by name so a regression to "passage …" is a
// failure rather than a diff nobody reads.
const READ_FOOTER = "read →";
const SEARCH_BOX = "Search the corpus";
// The 2018 article in the fixture corpus, used as a stable "did this one come
// back" probe.
const AMAZON_TITLE = "More on Amazon";
// The 2018 range as a link: the same URL a reader would share, used three ways.
const RANGE_2018 = "/?from=2018-01-01&to=2018-12-31";

test.describe("landing screen (nothing asked yet)", () => {
  test("offers the years and kinds the archive actually has", async ({
    page,
  }) => {
    await page.goto("/");

    await expect(page.getByText("Browse by year")).toBeVisible();
    await expect(page.getByRole("button", { name: /^2026\b/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /^2018\b/ })).toBeVisible();

    // daily + update are one chip, because they are one thing to a reader.
    await expect(
      page.getByRole("button", { name: /^daily update\b/ }),
    ).toBeVisible();

    // The count comes from /stats, so this also proves the landing screen
    // fetched the archive rather than rendering a hardcoded number.
    await expect(
      page.getByText(/2 searchable articles, 2018 → 2026/),
    ).toBeVisible();

    // 2024 exists in the archive, but its only message is an access code that
    // search and browse exclude. A chip for it would be an empty promise, so
    // the landing screen counts the searchable slice.
    await expect(page.getByRole("button", { name: /^2024\b/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^other\b/ })).toHaveCount(0);

    // A year chip is a question on its own: nothing has been searched yet.
    await expect(page.locator(card)).toHaveCount(0);
  });

  test("the search box says what it searches", async ({ page }) => {
    await page.goto("/");
    const box = page.getByRole("searchbox", { name: SEARCH_BOX });
    await expect(box).toHaveAttribute("placeholder", "Search the archive");
  });
});

test.describe("browsing without typing", () => {
  test("a year chip lists that year's articles, newest first", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /^2026\b/ }).click();

    await expect(page).toHaveURL(/from=2026-01-01/);
    await expect(page).toHaveURL(/to=2026-12-31/);

    await expect(page.locator(card)).toHaveCount(1);
    await expect(page.locator(card).first()).toContainText("IBM Misses");
    // Browse means there was no query to match, so the footer cannot point at
    // a passage: it says what the card is for instead.
    await expect(page.locator(card).first().locator("footer")).toHaveText(
      READ_FOOTER,
    );
    await expect(page.getByText("newest first")).toBeVisible();
  });

  test("a browsed range is linkable", async ({ page }) => {
    await page.goto(RANGE_2018);

    await expect(page.locator(card)).toHaveCount(1);
    await expect(page.locator(card).first()).toContainText(AMAZON_TITLE);
    await expect(page.locator(card).first().locator("footer")).toHaveText(
      READ_FOOTER,
    );
  });

  test("an empty range says so instead of looking broken", async ({ page }) => {
    await page.goto("/?from=2001-01-01&to=2001-12-31");
    await expect(page.getByText(/Nothing in this range/)).toBeVisible();
  });
});

test.describe("searching", () => {
  test("a typed query returns the passage it matched", async ({ page }) => {
    await page.goto("/");
    await page
      .getByRole("searchbox", { name: SEARCH_BOX })
      .fill("installed base");
    await page.locator('form.bar button[type="submit"]').click();

    await expect(page).toHaveURL(/q=installed/);
    await expect(page.locator(card)).toHaveCount(1);
    await expect(page.locator(card).first()).toContainText(AMAZON_TITLE);
    // A search, unlike a browse, is about which passage: the footer says so and
    // the snippet highlights the hit.
    await expect(page.locator(card).first().locator("footer")).toHaveText(
      /passage \d+ →/,
    );
    await expect(
      page.locator(card).first().locator("mark").first(),
    ).toBeVisible();
  });

  test("the term is remembered, and replays from the landing screen", async ({
    page,
  }) => {
    await page.goto("/");
    await page
      .getByRole("searchbox", { name: SEARCH_BOX })
      .fill("aggregation theory");
    await page.locator('form.bar button[type="submit"]').click();
    await expect(page.locator(card)).toHaveCount(1);

    await page.goto("/");
    await expect(page.getByText("Recent searches")).toBeVisible();
    await page
      .getByRole("button", { name: "aggregation theory", exact: true })
      .click();
    await expect(page).toHaveURL(/q=aggregation/);
    await expect(page.locator(card)).toHaveCount(1);
  });

  test("an unmatched query keeps the archive calm", async ({ page }) => {
    await page.goto("/?q=zzzznotinthearchive");
    await expect(page.getByText(/No passages match/)).toBeVisible();
  });
});

test.describe("drill-in", () => {
  test("an article opens and shows a way back", async ({ page }) => {
    await page.goto(RANGE_2018);
    await page.locator(card).first().locator("a.subject").click();

    await expect(page).toHaveURL(/\/article\/85344/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      AMAZON_TITLE,
    );
    await page.getByRole("button", { name: /back/i }).click();
    await expect(page.locator(card)).toHaveCount(1);
  });
});

test.describe("corpus page", () => {
  test("the per-year chart states when the daily update started", async ({
    page,
  }) => {
    await page.goto("/stats");

    // The bars before that date are weekly and free articles, not a quiet year;
    // the caption carries the explanation so the indexer's notes[] does not have
    // to repeat it (and get the cause wrong).
    await expect(
      page.getByText(/Daily updates reach this archive from November 2018/),
    ).toBeVisible();
  });
});

test.describe("phone", () => {
  test("neither the landing screen nor results scroll sideways", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const url of ["/", RANGE_2018, "/article/85344"]) {
      await page.goto(url);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow, `${url} overflows horizontally`).toBeLessThanOrEqual(0);
    }
  });
});
