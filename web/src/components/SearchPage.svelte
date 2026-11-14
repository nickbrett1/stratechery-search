<script>
  import { search } from "../lib/api.js";
  import { remember } from "../lib/recent.js";
  import Facets from "./Facets.svelte";
  import ResultCard from "./ResultCard.svelte";
  import ResultList from "../lib/components/nb/ResultList.svelte";
  import EmptyState from "../lib/components/nb/EmptyState.svelte";
  import StatStrip from "../lib/components/nb/StatStrip.svelte";
  import StartingPoint from "./StartingPoint.svelte";

  let { query, onchange } = $props();

  let response = $state(null);
  let error = $state(null);
  let loading = $state(false);
  let typed = $state(query.q);

  // The last value this box pushed to the URL. The mirror below must not fight
  // the reader: when a keystroke updates `query`, `typed` already holds that text,
  // and resetting it from the URL would move the caret to the end mid-word.
  let sent = $state(query.q);

  // A reader typing is not asking a question yet. Below three characters the
  // vocabulary is too broad to be a result set *and* too expensive to answer —
  // a 2-character prefix measured at 211–218 ms of server time and 20 KB gzipped
  // on a phone, against 12–75 ms for everything else — and a mid-word
  // zero-result state is a *false claim* ("this corpus has nothing for you")
  // rather than an honest "I don't know yet" (design note §2.3, §3.3, and gate
  // clause 4). The floor is the UI's: the API's own 400 for an empty q is
  // correct and is never sent.
  const MIN_PREFIX_CHARS = 3;
  // Not a delay — a rate limit. The first version waited 120 ms for silence
  // before asking, which charged every reader 120 ms for a decision they had
  // already made: a pause *is* the question. Measured on a phone (2026-11-07),
  // that wait was 121 ms of a 205 ms median — more than half the perceived
  // latency was this timer, on a link where the whole query cost 12-38 ms.
  //
  // So a keystroke now goes out immediately when the last request is older than
  // the cooldown, and only a burst *inside* it is collapsed into one trailing
  // request. Ninety milliseconds is below the gap between two keys at phone
  // typing speed (~150 ms), so ordinary typing asks once per keystroke exactly
  // as before, minus the wait; a paste, an autorepeat or a very fast thumb is
  // what the cooldown actually protects against.
  const TYPEAHEAD_COOLDOWN_MS = 90;
  // The same word-runs the server extracts, so the UI never sends text the API
  // would refuse as "no searchable words" (`:` , an emoji, a lone `"`).
  const WORD = /[^\W_]+(?:[-'][^\W_]+)*/u;

  let pending = 0;
  // When the last type-ahead request went out. The cooldown is measured from
  // here, not from the keystroke, so a burst cannot exceed ~4 requests/second.
  let lastSentAt = 0;

  // The URL is the query (§4): a parameter change is a fetch, and the input box
  // follows the URL rather than owning the truth.
  $effect(() => {
    // …except for the change this box just made. Only an *external* change (back
    // button, a link, a chip) rewrites the text under the caret.
    if (query.q !== sent) {
      typed = query.q;
      sent = query.q;
    }
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
      (query.entity || []).length ||
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
    // A keystroke still in the debounce must not fire after Enter: the reader has
    // said this is the question, so the pending refinement is dropped.
    clearTimeout(pending);
    const term = typed.trim();
    // Only what the reader typed is remembered: a query that arrived in a shared
    // URL is not their search. And only on Enter — a debounced keystroke is not a
    // search anyone meant to keep.
    if (term) remember(term);
    sent = term;
    // A submitted query is a *complete* question, so it is asked the way it was
    // asked before type-ahead existed: no prefix, no resolver.
    onchange({ ...query, q: term, offset: 0, partial: false });
  }

  function typeahead(event) {
    typed = event.currentTarget.value;
    clearTimeout(pending);
    const term = typed.trim();

    if (!term) {
      // Clearing the box is not a query: send nothing, and fall back to the
      // pre-typing screen rather than letting the API refuse an empty q.
      if (!sent) return;
      sent = "";
      onchange(
        { ...query, q: "", offset: 0, partial: false },
        { replace: true },
      );
      return;
    }
    // Punctuation the server would refuse, and a first character either way:
    // nothing is sent, and the previous results stay on screen labelled as what
    // they are. This is the state that must never read as "no results".
    if (!WORD.test(term) || term.length < MIN_PREFIX_CHARS) return;
    if (term === sent) return;

    const now = performance.now();
    const wait = Math.max(0, lastSentAt + TYPEAHEAD_COOLDOWN_MS - now);
    if (wait === 0) ask(term);
    else pending = setTimeout(() => ask(typed.trim()), wait);
  }

  function ask(term) {
    if (!term || term === sent) return;
    lastSentAt = performance.now();
    sent = term;
    // `replace` keeps the back stack for places the reader chose to go.
    onchange(
      { ...query, q: term, offset: 0, partial: true },
      { replace: true },
    );
  }

  // Active filters, as removable chips. The facet panel is a mile down a phone
  // screen (it sat 4,016px below the top of the results — 4.8 screens), so on
  // mobile the filters come to the reader as a chip row instead, and the full
  // panel opens as a sheet.
  // Entity ids are what the URL carries; the reader needs the company's name on
  // the chip, and the facet payload is the only place that mapping exists. A
  // selected id missing from the payload falls back to the id rather than an
  // empty chip.
  const entityNames = $derived(
    new Map(
      (response?.facets?.by_entity || []).map((row) => [
        row.entity_id,
        row.name,
      ]),
    ),
  );

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
    for (const entity of query.entity || []) {
      chips.push({
        key: `entity-${entity}`,
        label: entityNames.get(entity) || entity,
        patch: {
          entity: (query.entity || []).filter((value) => value !== entity),
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

  function removeFilter(patch) {
    onchange({ ...query, offset: 0, ...patch });
  }

  function clearFilters() {
    onchange({
      ...query,
      offset: 0,
      from: "",
      to: "",
      kind: [],
      sender: [],
      entity: [],
      hasImages: "",
    });
  }

  // The panel closes its own sheet on a phone (that is the block's job now);
  // choosing a facet only has to mean "re-run the search".
  function facetChange(next) {
    onchange(next);
  }

  // The API pages by article (`group=article`): one row per article, its other
  // matching passages folded under the best-ranked one. So the pager and the
  // "showing" range count articles, while the total stays a count of passages —
  // which is the number that tells the reader how much text matched.
  // The tally as the house KPI row. The counting rules are unchanged and
  // deliberately not flattened into one number: browsing has no match, so
  // "passages" would count every chunk in the range and mean nothing, and a
  // hybrid query ranks a fused window out of everything BM25 matched - either
  // number alone lies.
  const tally = $derived.by(() => {
    if (!response) return [];
    const rows = [];
    if (response.browse) {
      rows.push({
        label: "articles",
        value: response.article_total.toLocaleString(),
        sub: "newest first",
      });
    } else if (response.hybrid?.enabled) {
      rows.push({
        label: "ranked",
        value: response.hybrid.fused.toLocaleString(),
        sub: `of ${response.total.toLocaleString()} passages`,
      });
    } else {
      rows.push({
        label: "passages",
        value: response.total.toLocaleString(),
        sub: `in ${(response.article_total ?? response.results.length).toLocaleString()} articles`,
      });
    }
    rows.push({
      label: "showing",
      value: `${pageStart}–${pageEnd}`,
      sub: "this page",
    });
    rows.push({
      label: "took",
      value: `${response.took_ms} ms`,
      sub: "server time",
    });
    return rows;
  });

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
    oninput={typeahead}
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
  <div class="layout">
    <!-- The applied filters, as chips you can take off one at a time. The way
         *in* to the panel is the block's own chip row now, which is sticky on a
         phone; this row is what you have chosen. Phone only: on a wide screen the
         panel is beside the results and each facet shows its own selection. -->
    {#if response && activeFilters.length}
      <div class="chipsrow">
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
        <button class="chip clear" type="button" onclick={clearFilters}
          >clear all</button
        >
      </div>
    {/if}

    <section class="results" class:loading>
      {#if response}
        <StatStrip stats={tally} />
        <!-- One card, ranked rows, hairlines between them — the block's shape,
             with this app's row in the `row` slot. `initialCount` is the whole
             page because the pager below is the fold: a second "show N more"
             inside a page that already ends in "0–10 of 1,691" would be two
             different answers to the same question. -->
        {#if response.results.length}
          <ResultList
            items={response.results}
            initialCount={response.results.length}
          >
            {#snippet row(result)}
              <ResultCard
                {result}
                passages={result.more_passages ?? []}
                browse={response.browse ?? false}
                query={query.q}
              />
            {/snippet}
          </ResultList>
        {:else}
          <!-- Advice has to match the cause: a filter that excludes everything is
             not fixed by typing fewer words, and a range with nothing in it is
             not a query at all. The block is the same one the registry
             publishes; the wording still comes from here. -->
          {#if response.browse}
            <EmptyState
              cause="filters"
              customMessage="Nothing in this range."
              onclear={clearFilters}
            />
          {:else if activeFilters.length}
            <!-- The block supplies the shape and the clear action; the sentence
               stays this project's, because it is contract copy: a filter that
               excludes everything is not fixed by typing fewer words. -->
            <EmptyState
              cause="filters"
              customMessage="No passages match with these filters."
              onclear={clearFilters}
            />
          {:else}
            <EmptyState
              cause="query"
              customMessage="No passages match. Try fewer words."
            />
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

    <aside class="facetwrap">
      <Facets
        facets={response?.facets ?? null}
        {query}
        onchange={facetChange}
        onclear={clearFilters}
      />
    </aside>
  </div>
{:else}
  <!-- Nothing asked yet: the one screen that had no way in. -->
  <StartingPoint {query} {onchange} />
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
  .facetwrap {
    display: block;
    min-width: 0;
    position: sticky;
    top: 56px;
    align-self: start;
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
    /* One column: the panel goes *above* the results rather than below them.
       Below, it was 4,016px down the page — 4.8 phone screens past the first
       result — which is the difference between "filters exist" and "filters are
       reachable". Under 600px the panel turns into a chip row and a sheet, and
       this is what puts that row at the top. */
    .facetwrap {
      order: -1;
      position: static;
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
     Below 600px FilterPanel stops being a panel and becomes a sticky chip row
     that opens a bottom sheet. That is the block's own mobile behaviour, so this
     app no longer carries a second one. The aside still has to *come first*:
     stacked under the results, its chip row began 4,016px down the page, which
     is 4.8 phone screens past the first result. `order` moves the row, not the
     panel — the panel is fixed once it is open. */
  @media (max-width: 600px) {
    /* The chip row lives inside the block, and the block's styles are its own,
       so reach it from the column that holds it. Sticky at the header's height
       is what keeps the filters one tap away while the results scroll. */
    .facetwrap :global(.mobile-chip-row) {
      position: sticky;
      top: 50px; /* height of the sticky header above it */
      z-index: 5;
      margin: 0 -12px;
      padding: 7px 12px;
      background: rgba(15, 23, 42, 0.93);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--line);
    }
    /* The applied filters, under the way in. Not sticky: one sticky row is what
       keeps the bar thin, and the row can be several chips deep. */
    .chipsrow {
      margin: 0 -12px;
      padding: 7px 12px;
      overflow-x: auto;
      scrollbar-width: none;
      border-bottom: 1px solid var(--line);
    }
    .chipsrow::-webkit-scrollbar {
      display: none;
    }
  }
</style>
