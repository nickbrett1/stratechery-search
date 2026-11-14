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
      if (message.type() !== "error") return;
      const text = message.text();
      const url = message.location()?.url ?? "";
      // The one expected exception, and it is contractual rather than tolerated:
      // `/article/{id}/similar` answers 503 when the process has no vector store
      // (phase 4 §6). The app catches it, the article renders, the block is
      // simply absent — which is the specified behaviour. Chromium logs every
      // failed resource load as a console error, so without this the fixture
      // corpus (which has no vectors) would fail every drill-in test.
      if (url.includes("/similar") && text.includes("503")) return;
      failures.push(`console: ${text}`);
    });
    await use(page);
    expect(failures, "the page logged an error while rendering").toEqual([]);
  },
});

// A result is a *row* of the results card now, not a card of its own: the block
// draws one card, ranks the rows and rules between them. The selector is the
// block's, which is the point of consuming it -- the app does not own that name.
const card = ".result-row";
// A browse card has no matched passage to point at, so its footer is the whole
// affordance ("read →"), asserted by name so a regression to "passage …" is a
// failure rather than a diff nobody reads.
const READ_FOOTER = "read →";
const SEARCH_BOX = "Search the corpus";
// Two entity rows on the landing screen, asserted by name in several tests: the
// split between them is the point (a person is not a company).
const BROWSE_COMPANY = "Browse by company";
const BROWSE_PEOPLE = "Browse by people";
// The landing screen's newest-articles block.
const LATEST = "Latest articles";
// How many chips a browse row shows before it folds, and the number the stub
// below is built to exceed (the real gazetteer is 38 companies, 9 people).
const VISIBLE = 6;
// The 2018 article in the fixture corpus, used as a stable "did this one come
// back" probe.
const AMAZON_TITLE = "More on Amazon";
// The 2018 range as a link: the same URL a reader would share, used three ways.
const RANGE_2018 = "/?from=2018-01-01&to=2018-12-31";
// The zero-result state. Asserted absent where a word is unfinished, present
// where the archive really has nothing.
const NO_MATCHES = /No passages match/;

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

  test("puts companies above the calendar and the kind", async ({ page }) => {
    // The owner's call (2026-11-08): browsing to a company is the more common
    // question than browsing to a year, and it is the only row here that names
    // something a reader cannot type from memory. Order is a decision, so it is
    // asserted rather than left to whoever edits the template next.
    //
    // The fixture corpus ships no facets.sqlite, so /stats has no `companies` at
    // all — the same gap that let three facet UI defects ship green. The row is
    // stubbed: what is being tested here is the order of the blocks, not the
    // gazetteer.
    await page.route("**/stats", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      // Deliberately out of order and out of size order: the API hands these
      // over largest-first, and the landing screen is supposed to re-sort them.
      body.companies = [
        { entity_id: "ibm", name: "IBM", articles: 9 },
        { entity_id: "apple", name: "Apple", articles: 1 },
      ];
      await route.fulfill({ response, json: body });
    });
    await page.goto("/");
    const headings = await page
      .locator(".block > .micro, .block > .head .micro")
      .allTextContents();
    const company = headings.indexOf(BROWSE_COMPANY);
    expect(company).toBeGreaterThanOrEqual(0);
    expect(company).toBeLessThan(headings.indexOf("Browse by year"));
    expect(company).toBeLessThan(headings.indexOf("Browse by kind"));
  });

  test("lists companies alphabetically, not by size", async ({ page }) => {
    // A list sorted by magnitude reorders whenever the corpus grows, so the
    // position of a name is never learnable. IBM has nine articles and Apple
    // one; Apple still comes first.
    await page.route("**/stats", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.companies = [
        { entity_id: "ibm", name: "IBM", articles: 9 },
        { entity_id: "apple", name: "Apple", articles: 1 },
      ];
      await route.fulfill({ response, json: body });
    });
    await page.goto("/");

    const names = await page
      .locator(".block", { hasText: BROWSE_COMPANY })
      .locator("button.pick span:first-child")
      .allTextContents();
    expect(names).toEqual(["Apple", "IBM"]);
  });

  test("offers a person as a place to start, apart from the companies", async ({
    page,
  }) => {
    // /stats now carries two entity lists. The person row must render, must sort
    // alphabetically like the company row, and must not be folded into it —
    // "Elon Musk" under "Browse by company" is the defect this split fixes.
    await page.route("**/stats", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.companies = [{ entity_id: "apple", name: "Apple", articles: 9 }];
      body.people = [
        { entity_id: "elon_musk", name: "Elon Musk", articles: 7 },
        { entity_id: "jeff_bezos", name: "Jeff Bezos", articles: 3 },
      ];
      await route.fulfill({ response, json: body });
    });
    await page.goto("/");

    const headings = await page
      .locator(".block > .micro, .block > .head .micro")
      .allTextContents();
    const company = headings.indexOf(BROWSE_COMPANY);
    const people = headings.indexOf(BROWSE_PEOPLE);
    expect(people).toBeGreaterThanOrEqual(0);
    // Adjacent: a reader scanning for a name should not hunt between rows.
    expect(people).toBe(company + 1);

    expect(
      await page
        .locator(".block", { hasText: BROWSE_PEOPLE })
        .locator("button.pick span:first-child")
        .allTextContents(),
    ).toEqual(["Elon Musk", "Jeff Bezos"]);
    // ...and the company row is still exactly the companies.
    expect(
      await page
        .locator(".block", { hasText: BROWSE_COMPANY })
        .locator("button.pick span:first-child")
        .allTextContents(),
    ).toEqual(["Apple"]);
  });

  test("a person chip browses that person's articles", async ({ page }) => {
    // A chip is a complete question: no query, still a browse. The entity
    // parameter is what carries it, and people and companies share it.
    await page.route("**/stats", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.people = [
        { entity_id: "elon_musk", name: "Elon Musk", articles: 7 },
      ];
      await route.fulfill({ response, json: body });
    });
    // The fixture corpus ships no facets.sqlite, so the server answers this
    // person's browse with 400 unknown_entity and the console guard fails the
    // test for a reason that has nothing to do with the chip. What is asserted
    // is that the chip carries the entity, not that the gazetteer knows Musk.
    await page.route("**/search**", async (route) => {
      const url = new URL(route.request().url());
      // The real question asked is "browse Elon Musk"; the stub answers an
      // ordinary query instead, because a browse with no filter at all is itself
      // a 400 in this API ("nothing asked").
      url.searchParams.delete("entity");
      url.searchParams.set("q", "Amazon");
      await route.fulfill({
        response: await route.fetch({ url: url.toString() }),
      });
    });
    await page.goto("/");
    await page
      .locator(".block", { hasText: BROWSE_PEOPLE })
      .getByRole("button", { name: /Elon Musk/ })
      .click();
    await expect(page).toHaveURL(/entity=elon_musk/);
  });

  test("offers the newest articles as a way in", async ({ page }) => {
    // A reader who does not know what to search for can still open what has just
    // been published. It comes from /stats (the document this screen already
    // fetches), so it is one round trip, and it is ordered like browse: newest
    // first.
    await page.goto("/");
    const latest = page.locator(".block", { hasText: LATEST });
    const rows = latest.locator("a.latestrow");
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText("IBM Misses");
    await expect(rows.first()).toContainText("2026-07-15");
    await expect(rows.first()).toHaveAttribute("href", "/article/152");
    await expect(rows.nth(1)).toContainText("More on Amazon");

    // The newest *message* in the fixture is the 2024 access code, which is
    // transactional mail: offering it as "what's new" would be the reading list
    // recommending a receipt.
    await expect(latest).not.toContainText("access code");
  });

  test("a newest article opens from the landing screen", async ({ page }) => {
    // The block is a way *in*, not a dead end: the row is an ordinary link, and
    // the in-document router has to pick it up like any other.
    await page.goto("/");
    await page
      .locator(".block", { hasText: LATEST })
      .locator("a.latestrow")
      .first()
      .click();
    await expect(page).toHaveURL(/\/article\/152/);
  });

  test("folds the company list until the reader asks for all of it", async ({
    page,
  }) => {
    // Twelve companies, of which the row shows six: the gazetteer is 38 and
    // rendered whole it was most of a phone screen between the reader and the
    // year row. Nothing is lost — the expander says how many it is holding.
    await page.route("**/stats", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.companies = Array.from({ length: 12 }, (_, index) => ({
        entity_id: `company_${index}`,
        name: `Company ${String(index).padStart(2, "0")}`,
        articles: 12 - index,
      }));
      await route.fulfill({ response, json: body });
    });
    await page.goto("/");

    const row = page.locator(".block", { hasText: BROWSE_COMPANY });
    const picks = row.locator("button.pick");
    await expect(picks).toHaveCount(VISIBLE);

    const more = row.getByRole("button", { name: /show all 12 companies/ });
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await more.click();

    await expect(picks).toHaveCount(12);
    // The first six keep their places: expanding adds, it does not reorder.
    await expect(picks.first()).toContainText("Company 00");

    const fewer = row.getByRole("button", { name: /show fewer/ });
    await expect(fewer).toHaveAttribute("aria-expanded", "true");
    await fewer.click();
    await expect(picks).toHaveCount(VISIBLE);
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

  test("orders the company facet alphabetically, not by size", async ({
    page,
  }) => {
    // The fixture corpus builds no facets.sqlite, so `by_entity` never arrives
    // on its own — the same gap that let three facet UI defects ship green.
    // Stubbed here to pin the ordering decision: the API hands entities over
    // largest-first, and the panel re-sorts them by name.
    await page.route("**/search*", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.facets = body.facets || {};
      body.facets.by_entity = [
        { entity_id: "ibm", name: "IBM", kind: "company", articles: 9 },
        { entity_id: "apple", name: "Apple", kind: "company", articles: 1 },
        { entity_id: "musk", name: "Elon Musk", kind: "person", articles: 1 },
        { entity_id: "bezos", name: "Jeff Bezos", kind: "person", articles: 4 },
      ];
      await route.fulfill({ response, json: body });
    });
    await page.goto("/?q=installed+base");

    // One group per dimension in the block; the label is a `.facet-label` span,
    // not a heading, so the group is found by its text and read by its options.
    const group = (title) =>
      page.locator(".facet-group").filter({ hasText: title });
    const labels = async (title) =>
      group(title).locator(".option-label").allTextContents();

    expect(await labels("Company")).toEqual(["Apple", "IBM"]);
    expect(await labels("People")).toEqual(["Elon Musk", "Jeff Bezos"]);
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
    await expect(page.getByText(NO_MATCHES)).toBeVisible();
  });
});

test.describe("type-ahead (typing is the whole interaction)", () => {
  // `aggregation` is in the fixture's article body and, being stemmed to
  // `aggreg` at index time, is exactly the word a prefix cannot reach without the
  // resolver. Eight of its eleven characters, so it is unambiguously mid-word.
  const HALF_TYPED = "aggregat";

  test("results arrive before the word is finished", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("searchbox", { name: SEARCH_BOX }).fill(HALF_TYPED);

    // No Enter, no click: filling the box is the entire interaction.
    await expect(page.locator(card)).toHaveCount(1);
    await expect(page).toHaveURL(/q=aggregat/);
    // The rewrite is visible, so a half-word that resolved to something
    // unexpected is debuggable rather than mysterious.
    await expect(page.getByText(/aggregation/).first()).toBeVisible();
    // Claim 3: what is on screen corresponds to the text in the box. The URL
    // mirror must not overwrite the box mid-word.
    await expect(page.getByRole("searchbox", { name: SEARCH_BOX })).toHaveValue(
      HALF_TYPED,
    );
  });

  test("a half-typed word never claims the archive has nothing", async ({
    page,
  }) => {
    await page.goto("/");
    const box = page.getByRole("searchbox", { name: SEARCH_BOX });
    // One character at a time, so the page passes through every intermediate
    // state a reader would see — including the ones below the two-character floor.
    for (const prefix of ["a", "ag", "agg", "aggr", "aggreg", HALF_TYPED]) {
      await box.fill(prefix);
      await expect(
        page.getByText(NO_MATCHES),
        `"${prefix}" is mid-word, so claiming no matches would be false`,
      ).toHaveCount(0);
    }
    await expect(page.locator(card)).toHaveCount(1);
  });

  test("the first letter of a second word does not empty the page", async ({
    page,
  }) => {
    // Measured on a phone: typing "smiling curve" as a slow reader rendered
    // "No passages match" for one keystroke at "smiling c", because a
    // one-character word was searched as the letter it is. A started word that
    // is not finished must not constrain the query yet.
    await page.goto("/");
    const box = page.getByRole("searchbox", { name: SEARCH_BOX });
    await box.fill("aggregation");
    await expect(page.locator(card)).toHaveCount(1);

    await box.fill("aggregation t");
    await expect(page.getByText(NO_MATCHES)).toHaveCount(0);
    // The same result set as the completed word alone: nothing new appeared, and
    // nothing disappeared.
    await expect(page.locator(card)).toHaveCount(1);
  });

  test("one character has not started yet", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("searchbox", { name: SEARCH_BOX }).fill("a");
    // The landing screen, not a result set and not a zero: nothing has been asked.
    await expect(page.getByText(/2 searchable articles/)).toBeVisible();
    await expect(page.getByText(/2 searchable articles/)).toBeVisible();
    await expect(page.locator(card)).toHaveCount(0);
    await expect(page).toHaveURL(/\/$/);
  });

  test("the floor is three characters, not two", async ({ page }) => {
    // A 2-character prefix cost 211-218 ms of server time and 20 KB gzipped on a
    // phone (measured 2026-11-07) against 12-75 ms for everything else, and gate
    // clause 4 says the answer is to raise the floor rather than to hide it.
    await page.goto("/");
    const box = page.getByRole("searchbox", { name: SEARCH_BOX });
    await box.fill("ag");
    await expect(page.getByText(/2 searchable articles/)).toBeVisible();
    await expect(page.locator(card)).toHaveCount(0);

    await box.fill("agg");
    await expect(page.locator(card)).toHaveCount(1);
  });

  test("clearing the box falls back to the landing screen", async ({
    page,
  }) => {
    await page.goto("/");
    const box = page.getByRole("searchbox", { name: SEARCH_BOX });
    await box.fill(HALF_TYPED);
    await expect(page.locator(card)).toHaveCount(1);

    await box.fill("");
    await expect(page.getByText(/2 searchable articles/)).toBeVisible();
    await expect(page.locator(card)).toHaveCount(0);
    // Nothing was asked, so the URL is empty again rather than carrying `q=`.
    await expect(page).toHaveURL(/\/$/);
  });

  test("punctuation mid-word is not an error state", async ({ page }) => {
    await page.goto("/");
    const box = page.getByRole("searchbox", { name: SEARCH_BOX });
    for (const typed of ['agg"', "aggregat*", "agg-r", "don't", "OR", "(agg"]) {
      await box.fill(typed);
      await expect(
        page.getByText(/Something went wrong|request failed/),
        `"${typed}" rendered an error state`,
      ).toHaveCount(0);
    }
  });
});

test.describe("drill-in", () => {
  test("the excerpt is clickable, not only the title", async ({ page }) => {
    await page.goto("/?q=installed+base");
    await expect(page.locator(card)).toHaveCount(1);
    // A thumb lands on the excerpt, not on the 13px title above it.
    await page.locator(card).first().locator("a.snippet").click();
    await expect(page).toHaveURL(/\/article\/\d+\?q=installed/);
    // …and the passage fragment survives a query string, or the article opens
    // at the top and the reader hunts for the paragraph they clicked.
    await expect(page).toHaveURL(/#c\d+-\d+$/);
  });

  test("a drill-in keeps and highlights the search it came from", async ({
    page,
  }) => {
    await page.goto("/?q=installed+base");
    await page.locator(card).first().locator("a.snippet").click();

    await expect(page.locator("article.text mark").first()).toBeVisible();
    await expect(page.getByText(/matches? for/)).toBeVisible();
    // The words highlighted are the words searched: a highlight the reader
    // cannot connect to the box they typed in is decoration.
    await expect(page.locator("article.text mark").first()).toHaveText(
      /installed|base/i,
    );
  });

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

  test("the article offers elsewhere in the archive, when there is a store", async ({
    page,
  }) => {
    // The fixture corpus ships no vectors, so `/similar` is a contractual 503
    // there and the block is absent by design. The *template* is what a Python
    // test cannot see — an undeclared variable in it ships green through every
    // other gate, which is why this suite exists — so the answer is stubbed.
    await page.route("**/article/*/similar*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message_id: 85344,
          grain: "article",
          basis: { passages: 2, embed_model: "cohere/embed-v5.0" },
          results: [
            {
              message_id: 152,
              sent_at: "2026-07-15T10:07:57Z",
              subject: "IBM Misses (Stratechery Update 7-15-2026)",
              kind: "update",
              distance: 0.412,
              matched_passages: 2,
              chunk_id: "152:0",
              excerpt: "The aggregation theory says integration is a choice.",
              article_href: "/article/152#c152-0",
              more_passages: 1,
            },
          ],
        }),
      }),
    );
    await page.goto("/article/85344");

    // The heading is fixed by copy discipline (phase 4 §7): a dated ranked list,
    // never "related topics" or "themes".
    await expect(
      page.getByRole("heading", { name: "Elsewhere in the archive" }),
    ).toBeVisible();
    await expect(page.getByText(/distance 0\.412/)).toBeVisible();
    await page.getByRole("link", { name: /IBM Misses/ }).click();
    await expect(page).toHaveURL(/\/article\/152#c152-0$/);
  });
});

test.describe("corpus page", () => {
  test("the per-year chart explains itself, or stays quiet", async ({
    page,
  }) => {
    await page.goto("/stats");

    // The caption is where the archive explains its own shape: before sustained
    // daily coverage a short bar means "not subscribed yet", not "published
    // less". This fixture is two updates eleven years apart, so it cannot claim
    // that boundary honestly and falls back to the plain line — the "March 2018"
    // answer is pinned in tests/test_build_index.py, which has the real shape.
    await expect(page.locator(".timeline-wrap .chart-note")).toHaveText(
      "One bar per published year.",
    );
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
