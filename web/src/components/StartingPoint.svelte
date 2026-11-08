<script>
  import { stats } from "../lib/api.js";
  import { groupKinds, kindActive, toggleKindGroup } from "../lib/display.js";
  import { readRecent, remember, forget, forgetAll } from "../lib/recent.js";

  // What the reader sees before they have typed anything. On its own the app used
  // to leave this state blank below the search box — the one screen with no way
  // in, on a phone with no browser history to fall back on.
  //
  // Nothing here searches on its own: the API answers a filter with no query
  // (`/search?from=2019-01-01&to=2019-12-31` is "browse 2019"), so a year or a
  // kind is a complete question, and recent searches are re-run as they were.
  let { query, onchange } = $props();

  let data = $state(null);
  let recent = $state(readRecent());

  $effect(() => {
    let cancelled = false;
    stats()
      .then((body) => {
        if (!cancelled) data = body;
      })
      .catch(() => {
        // The counts are a convenience; the search box above still works.
      });
    return () => {
      cancelled = true;
    };
  });

  // The searchable slices, not the whole archive: a chip has to be a question
  // the API can answer, and 44 of the 1,691 articles are transaction mail that
  // search and browse cannot reach. `by_year` above them counts those too, so a
  // chip built from it would promise a year that returns nothing.
  const slices = $derived(data?.searchable ?? {});

  // Newest first: a reader browsing by year is far more often looking for
  // something recent than for 2016.
  const years = $derived([...(slices.by_year ?? [])].reverse());
  const kinds = $derived(groupKinds(slices.by_kind ?? []));

  function runTerm(term) {
    // Tapping a past search is a search: it moves back to the front of the list.
    recent = remember(term);
    onchange({ ...query, q: term, offset: 0 });
  }

  function pickYear(year) {
    onchange({
      ...query,
      offset: 0,
      from: `${year}-01-01`,
      to: `${year}-12-31`,
    });
  }

  // A tollgle, not a launch: the panel's kind chips add to the selection, so the
  // landing screen's must too — otherwise "daily update + weekly" is three
  // separate visits and the second one silently drops the first.
  function pickKind(group) {
    onchange({
      ...query,
      offset: 0,
      kind: toggleKindGroup(query.kind, group.kinds),
    });
  }

  // A company chip is a complete question too (`/?entity=apple` browses Apple's
  // articles), which is the job the facet exists for: a place to start, before
  // anything is typed. It is also the only place the gazetteer is visible
  // without a search, since the panel lives beside the results.
  const companies = $derived(data?.companies ?? []);

  function pickCompany(id) {
    onchange({ ...query, offset: 0, entity: [id] });
  }
</script>

<section class="start">
  {#if recent.length}
    <div class="block">
      <div class="head">
        <span class="micro">Recent searches</span>
        <button
          class="quiet"
          type="button"
          onclick={() => (recent = forgetAll())}>clear</button
        >
      </div>
      <ul class="recents">
        {#each recent as term (term)}
          <li>
            <button class="term" type="button" onclick={() => runTerm(term)}>
              <span class="arrow" aria-hidden="true">↺</span>
              <span class="text">{term}</span>
            </button>
            <button
              class="drop"
              type="button"
              aria-label="Forget {term}"
              onclick={() => (recent = forget(term))}
            >
              ×
            </button>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if years.length}
    <div class="block">
      <span class="micro">Browse by year</span>
      <div class="picks">
        {#each years as bucket (bucket.year)}
          <button
            class="pick"
            type="button"
            title="{bucket.articles} articles in {bucket.year}"
            onclick={() => pickYear(bucket.year)}
          >
            <span class="num">{bucket.year}</span>
            <span class="n num">{bucket.articles}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}

  {#if kinds.length}
    <div class="block">
      <span class="micro">Browse by kind</span>
      <div class="picks">
        {#each kinds as group (group.id)}
          <button
            class="pick wide"
            class:on={kindActive(query.kind, group.kinds)}
            type="button"
            onclick={() => pickKind(group)}
          >
            <span>{group.label}</span>
            <span class="n num">{group.articles}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}

  {#if companies.length}
    <div class="block">
      <span class="micro">Browse by company</span>
      <div class="picks">
        {#each companies as company (company.entity_id)}
          <button
            class="pick wide"
            type="button"
            title="{company.articles} articles mention {company.name}"
            onclick={() => pickCompany(company.entity_id)}
          >
            <span>{company.name}</span>
            <span class="n num">{company.articles}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}

  {#if data}
    <p class="hint foot">
      {data.searchable_articles.toLocaleString()} searchable articles, {data.first.slice(
        0,
        4,
      )} → {data.last.slice(0, 4)}. A year or a kind is enough on its own.
    </p>
  {/if}
</section>

<style>
  .start {
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding: 4px 0 8px;
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 6px;
  }
  .block > .micro {
    display: block;
    margin-bottom: 6px;
  }
  .quiet {
    border: 0;
    background: none;
    padding: 0;
    font: inherit;
    font-size: 11px;
    color: var(--ink-faint);
    text-decoration: underline;
    cursor: pointer;
  }
  .quiet:hover {
    color: var(--accent);
  }
  ul.recents {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  ul.recents li {
    display: flex;
    align-items: center;
    gap: 2px;
  }
  /* The row is the target, the × is a second, smaller one: forgetting a search
     must not be a mis-tap away from running it. */
  .term {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 36px;
    padding: 4px 8px;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    background: none;
    font: inherit;
    font-size: 13.5px;
    color: var(--ink-dim);
    text-align: left;
    cursor: pointer;
  }
  .term:hover,
  .term:focus-visible {
    border-color: var(--line);
    background: var(--bg-hover);
  }
  .term .text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .arrow {
    color: var(--ink-faint);
    font-size: 12px;
  }
  .drop {
    flex: 0 0 auto;
    width: 36px;
    height: 36px;
    border: 0;
    border-radius: var(--radius-sm);
    background: none;
    font: inherit;
    font-size: 16px;
    line-height: 1;
    color: var(--ink-faint);
    cursor: pointer;
  }
  .drop:hover,
  .drop:focus-visible {
    background: var(--bg-hover);
    color: var(--ink);
  }
  .picks {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .pick {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 11px;
    font: inherit;
    font-size: 12.5px;
    color: var(--ink-dim);
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    white-space: nowrap;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .pick:hover,
  .pick:focus-visible {
    border-color: rgba(125, 211, 252, 0.4);
    color: #fff;
  }
  .pick.on {
    border-color: rgba(125, 211, 252, 0.55);
    background: var(--accent-soft);
    color: #fff;
  }
  .pick.on .n {
    color: var(--accent);
  }
  .pick .n {
    font-size: 11px;
    color: var(--ink-faint);
  }
  .hint.foot {
    margin: 0;
    max-width: 46ch;
  }
</style>
