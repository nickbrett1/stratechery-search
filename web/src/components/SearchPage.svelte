<script>
  import { search } from "../lib/api.js";
  import { remember } from "../lib/recent.js";
  import Facets from "./Facets.svelte";
  import ResultCard from "./ResultCard.svelte";
  import StartingPoint from "./StartingPoint.svelte";

  let { query, onchange } = $props();

  let response = $state(null);
  let error = $state(null);
  let loading = $state(false);
  let typed = $state(query.q);

  // The URL is the query (§4): a parameter change is a fetch, and the input box
  // follows the URL rather than owning the truth.
  $effect(() => {
    // The URL owns q; the box only mirrors it.
    typed = query.q;
  });

  // A filter with no query is a question too ("browse 2019"), so the landing
  // screen is only for when nothing at all has been asked. The API draws the same
  // line: an empty q is 400 unless a filter narrows the corpus.
  const intent = $derived(
    Boolean(
      query.q ||
      query.from ||
      query.to ||
      (query.kind || []).length ||
      (query.sender || []).length ||
      query.hasImages,
    ),
  );

  $effect(() => {
    if (!intent) {
      response = null;
      return;
    }
    let cancelled = false;
    loading = true;
    error = null;
    search(query)
      .then((body) => {
        if (!cancelled) response = body;
      })
      .catch((failure) => {
        if (!cancelled) {
          error = failure;
          response = null;
        }
      })
      .finally(() => {
        if (!cancelled) loading = false;
      });
    return () => {
      cancelled = true;
    };
  });

  function submit(event) {
    event.preventDefault();
    const term = typed.trim();
    // Only what the reader typed is remembered: a query that arrived in a shared
    // URL is not their search.
    if (term) remember(term);
    onchange({ ...query, q: term, offset: 0 });
  }

  // Active filters, as removable chips. The facet panel is a mile down a phone
  // screen (it sat 4,016px below the top of the results — 4.8 screens), so on
  // mobile the filters come to the reader as a chip row instead, and the full
  // panel opens as a sheet.
  const activeFilters = $derived.by(() => {
    const chips = [];
    if (query.from || query.to) {
      const from = query.from ? query.from.slice(0, 4) : "…";
      const to = query.to ? query.to.slice(0, 4) : "…";
      chips.push({
        key: "date",
        label: from === to ? from : `${from}–${to}`,
        patch: { from: "", to: "" },
      });
    }
    // daily + update are one product under two names, so one chip stands for
    // both and removing it drops both — otherwise the reader has to know which
    // era they are in to undo a filter (see lib/display.js).
    const selectedKinds = query.kind || [];
    if (selectedKinds.some((kind) => kind === "daily" || kind === "update")) {
      chips.push({
        key: "kind-daily-update",
        label: "daily update",
        patch: {
          kind: selectedKinds.filter(
            (kind) => kind !== "daily" && kind !== "update",
          ),
        },
      });
    }
    for (const kind of selectedKinds) {
      if (kind === "daily" || kind === "update") continue;
      chips.push({
        key: `kind-${kind}`,
        label: kind,
        patch: { kind: selectedKinds.filter((value) => value !== kind) },
      });
    }
    for (const sender of query.sender || []) {
      chips.push({
        key: `sender-${sender}`,
        label: sender,
        patch: {
          sender: (query.sender || []).filter((value) => value !== sender),
        },
      });
    }
    if (query.hasImages) {
      chips.push({
        key: "images",
        label: query.hasImages === "true" ? "with images" : "no images",
        patch: { hasImages: "" },
      });
    }
    return chips;
  });

  let sheetOpen = $state(false);

  function removeFilter(patch) {
    onchange({ ...query, offset: 0, ...patch });
  }

  function clearFilters() {
    sheetOpen = false;
    onchange({
      ...query,
      offset: 0,
      from: "",
      to: "",
      kind: [],
      sender: [],
      hasImages: "",
    });
  }

  // Choosing a facet is the only reason the sheet exists, so it closes on the
  // choice: the point is to see the results change.
  function facetChange(next) {
    sheetOpen = false;
    onchange(next);
  }

  // The sheet owns the screen while it is open; letting the page scroll behind it
  // makes a phone feel broken rather than busy.
  $effect(() => {
    if (!sheetOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  });

  // The API pages by article (`group=article`): one row per article, its other
  // matching passages folded under the best-ranked one. So the pager and the
  // "showing" range count articles, while the total stays a count of passages —
  // which is the number that tells the reader how much text matched.
  const pageStart = $derived((response?.offset ?? 0) + 1);
  const pageEnd = $derived(
    (response?.offset ?? 0) + (response?.results.length ?? 0),
  );
  const hasPrev = $derived((response?.offset ?? 0) > 0);
  const hasNext = $derived(
    response
      ? response.offset + response.results.length <
          (response.article_total ?? 0)
      : false,
  );
</script>

<!-- Placeholder copy is short on purpose: measured against the input on a 320px
     phone, where the old text ("…articles — aggregation theory, smiling curve…")
     was cut off mid-word. No count in it: the landing screen prints the real one
     from /stats, and a hardcoded number here only creates a second place to
     forget to update. -->
<form class="bar" onsubmit={submit}>
  <input
    type="search"
    bind:value={typed}
    placeholder="Search the archive"
    aria-label="Search the corpus"
    autocapitalize="none"
    autocomplete="off"
    autocorrect="off"
    enterkeyhint="search"
    spellcheck="false"
  />
  <button class="btn primary" type="submit">Search</button>
</form>

{#if response?.query?.expansions?.length}
  <p class="notice info">
    <span class="micro">Matched via vocabulary</span>
    {#each response.query.expansions as expansion (expansion.matched)}
      <span class="expansion">
        <b>{expansion.matched}</b> → {expansion.canonical}
        <span class="hint">({expansion.aliases.join(", ")})</span>
      </span>
    {/each}
    <span class="hint">Not a related-concept graph — phase 1 has none.</span>
  </p>
{/if}

{#if error}
  <p class="notice bad">
    {error.message}
    {#if error.status === 503}
      The index is not built yet; <code>/healthz</code> reports the reason.
    {/if}
  </p>
{/if}

{#if intent}
  <div class="layout" class:sheet-open={sheetOpen}>
    <!-- Phone only: filters as chips, directly above the results, where the reader
       is. On a wide screen this row is hidden and the panel sits beside them. -->
    {#if response}
      <div class="chipsrow">
        <button
          class="chip filters"
          type="button"
          onclick={() => (sheetOpen = true)}
        >
          filters{activeFilters.length ? ` (${activeFilters.length})` : ""}
          <span class="caret" aria-hidden="true">▾</span>
        </button>
        {#each activeFilters as chip (chip.key)}
          <button
            class="chip on"
            type="button"
            onclick={() => removeFilter(chip.patch)}
          >
            {chip.label}
            <span class="x" aria-hidden="true">×</span>
            <span class="sr">— remove filter</span>
          </button>
        {/each}
        {#if activeFilters.length}
          <button class="chip clear" type="button" onclick={clearFilters}
            >clear all</button
          >
        {/if}
      </div>
    {/if}

    <section class="results" class:loading>
      {#if response}
        <div class="tally">
          {#if response.browse}
            <!-- Browsing has no match, so "passages" would count every chunk of
               every article in the range and mean nothing to the reader. -->
            <span
              ><b class="num">{response.article_total.toLocaleString()}</b>
              articles</span
            >
            <span class="hint num">newest first</span>
          {:else}
            <span
              ><b class="num">{response.total.toLocaleString()}</b> passages</span
            >
            <span class="hint num"
              >in {response.article_total ?? response.results.length} articles</span
            >
          {/if}
          <span class="hint num">showing {pageStart}–{pageEnd}</span>
          <span class="pill info num">{response.took_ms} ms</span>
        </div>
        <div class="cards">
          {#each response.results as result (result.chunk_id)}
            <ResultCard
              {result}
              passages={result.more_passages ?? []}
              browse={response.browse ?? false}
            />
          {/each}
        </div>
        {#if response.results.length === 0}
          <!-- Advice has to match the cause: a filter that excludes everything is
             not fixed by typing fewer words. -->
          {#if response.browse}
            <p class="notice">
              Nothing in this range.
              <button class="inline" type="button" onclick={clearFilters}
                >clear filters</button
              >
            </p>
          {:else if activeFilters.length}
            <p class="notice">
              No passages match with these filters.
              <button class="inline" type="button" onclick={clearFilters}
                >clear filters</button
              >
            </p>
          {:else}
            <p class="notice">No passages match. Try fewer words.</p>
          {/if}
        {/if}
        <nav class="seg pager">
          <button
            type="button"
            disabled={!hasPrev}
            onclick={() =>
              onchange({
                ...query,
                offset: Math.max(0, response.offset - response.limit),
              })}
          >
            ← previous
          </button>
          <button
            type="button"
            disabled={!hasNext}
            onclick={() =>
              onchange({ ...query, offset: response.offset + response.limit })}
          >
            next →
          </button>
        </nav>
      {/if}
    </section>

    <div class="facetwrap" class:open={sheetOpen}>
      <div class="sheethead">
        <span class="micro">filters</span>
        <button class="chip clear" type="button" onclick={clearFilters}
          >clear all</button
        >
        <button
          class="chip done"
          type="button"
          onclick={() => (sheetOpen = false)}>done</button
        >
      </div>
      <Facets
        facets={response?.facets ?? null}
        {query}
        onchange={facetChange}
      />
    </div>
  </div>
{:else}
  <!-- Nothing asked yet: the one screen that had no way in. -->
  <StartingPoint {query} {onchange} />
{/if}

{#if sheetOpen}
  <!-- Tapping the dimmed results behind the sheet is the other way out. -->
  <button
    class="scrim"
    type="button"
    aria-label="Close filters"
    onclick={() => (sheetOpen = false)}
  ></button>
{/if}

<style>
  .bar {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
  }
  .bar input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 8px 11px;
    /* No font-size here on purpose: theme.css owns it (16px on touch, because
       Safari zooms the page for anything smaller). A value here would outrank
       that rule and bring the zoom back. */
  }
  /* Never let the button shrink or wrap out of reach: on a phone the input is
     the greedy element and the button is the thing being aimed at. */
  .bar .btn {
    flex: 0 0 auto;
    padding: 0 16px;
    font-size: 12.5px;
  }
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 224px;
    gap: 22px;
    align-items: start;
  }
  /* Filter chips: the phone's substitute for the sideways panel. Hidden on a wide
     screen, where the panel itself is visible beside the results. */
  .chipsrow {
    display: none;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    flex: 0 0 auto;
    height: 32px;
    padding: 0 10px;
    font: inherit;
    font-size: 11.5px;
    color: var(--ink-dim);
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    white-space: nowrap;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .chip.on {
    color: var(--accent);
    border-color: rgba(125, 211, 252, 0.45);
    background: rgba(125, 211, 252, 0.1);
  }
  .chip .x {
    color: var(--ink-faint);
    font-size: 13px;
    line-height: 1;
  }
  .chip.clear {
    border-style: dashed;
    color: var(--ink-muted);
  }
  .chip.done {
    margin-left: auto;
    color: #fff;
    border-color: var(--line-strong);
  }
  .chip .caret {
    font-size: 9px;
    color: var(--ink-faint);
  }
  /* A button that reads as a link, for advice inside a notice. */
  .inline {
    padding: 0;
    font: inherit;
    color: var(--accent);
    background: none;
    border: 0;
    cursor: pointer;
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .facetwrap {
    display: block;
    min-width: 0;
  }
  .sheethead {
    display: none;
  }
  .scrim {
    display: none;
  }
  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .results.loading {
    opacity: 0.55;
  }
  .tally {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--line);
    font-size: 12px;
    color: var(--ink-muted);
  }
  .tally b {
    color: var(--ink);
    font-weight: 600;
  }
  .cards {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 10px;
  }
  .expansion {
    margin: 0 2px;
  }
  .expansion b {
    color: var(--ink);
  }
  .pager {
    margin-top: 14px;
  }
  .notice {
    margin: 0 0 12px;
  }
  @media (max-width: 760px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
      gap: 18px;
    }
  }
  @media (max-width: 600px) {
    /* Same height on both controls so the row reads as one bar, and 40px is the
       smallest comfortable thumb target. The input's 16px is set globally (see
       theme.css) because iOS zooms the page for anything smaller. */
    .bar {
      gap: 6px;
    }
    .bar input,
    .bar .btn {
      min-height: 40px;
    }
    .bar .btn {
      padding: 0 14px;
      font-size: 13px;
    }
  }
  /* ---- phone layout -------------------------------------------------------
     Below 600px the facet panel leaves the flow entirely: it becomes a sheet
     raised from the bottom, and a sticky chip row above the results carries the
     filters the reader has actually applied. This is the difference between
     "filters exist" and "filters are reachable" — as a stacked column it began
     4,016px down the page, which is 4.8 phone screens past the first result. */
  @media (max-width: 600px) {
    .chipsrow {
      position: sticky;
      top: 50px; /* height of the sticky header above it */
      z-index: 5;
      display: flex;
      gap: 6px;
      margin: 0 -12px;
      padding: 7px 12px;
      overflow-x: auto;
      background: rgba(15, 23, 42, 0.93);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--line);
      scrollbar-width: none;
    }
    .chipsrow::-webkit-scrollbar {
      display: none;
    }
    .facetwrap {
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 20;
      max-height: 72vh;
      padding: 10px 12px calc(14px + env(safe-area-inset-bottom, 0px));
      overflow-y: auto;
      background: var(--bg);
      border-top: 1px solid var(--line-strong);
      border-radius: 12px 12px 0 0;
      transform: translateY(101%);
      transition: transform 180ms ease;
    }
    .facetwrap.open {
      transform: none;
    }
    .facetwrap .facets {
      position: static;
      top: auto;
    }
    .sheethead {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }
    /* The sheet's own escape hatches get a 44px thumb target. The painted pill
       stays 34px so the sheet head does not grow; the row below it scrolls, so
       this is the one place where the target can grow without being clipped. */
    .sheethead .chip {
      position: relative;
      height: 34px;
    }
    .sheethead .chip::after {
      content: "";
      position: absolute;
      inset: -5px -4px;
      border-radius: inherit;
    }
    .scrim {
      display: block;
      position: fixed;
      inset: 0;
      z-index: 19;
      padding: 0;
      background: rgba(15, 23, 42, 0.6);
      border: 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .facetwrap {
      transition: none;
    }
  }
</style>
