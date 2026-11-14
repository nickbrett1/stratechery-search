<script>
  import { stats } from "../lib/api.js";
  import {
    displaySubject,
    groupKinds,
    kindActive,
    kindLabel,
    kindTone,
    toggleKindGroup,
  } from "../lib/display.js";
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

  // An entity chip is a complete question too (`/?entity=apple` browses Apple's
  // articles), which is the job the facet exists for: a place to start, before
  // anything is typed. It is also the only place the gazetteer is visible
  // without a search, since the panel lives beside the results.
  // Alphabetical, matching the facet panel: a chip list that reorders whenever
  // an entity is mentioned more often is a list you cannot learn the shape of.
  // The count stays visible, so the head of the corpus is still obvious.
  //
  // Companies and people are two lists, not one, because they answer different
  // questions ("which company" vs "which person") and because the whole reason
  // the panel was split was that a single list had Elon Musk under "Company".
  // The API keeps them apart for the same reason; this is one rendering each.
  const byName = (rows) =>
    [...rows].sort((left, right) => left.name.localeCompare(right.name));
  const companies = $derived(byName(data?.companies ?? []));
  const people = $derived(byName(data?.people ?? []));

  function pickEntity(id) {
    onchange({ ...query, offset: 0, entity: [id] });
  }

  // What has just been published, newest first. "What's new" is a question a
  // reader asks *before* they know what to search for, and the landing screen
  // had no answer to it: recent searches are the reader's own, the year chips
  // are a calendar, and the newest article was otherwise only reachable by
  // opening the current year and scanning. It rides on /stats — the document this
  // screen already fetches — so it costs no extra round trip on a phone.
  const latest = $derived(data?.recent ?? []);

  // How many chips a browse row shows before it folds. Six is one row on a
  // laptop and two on a phone: enough to show the shape of a list and to hit a
  // common name without opening anything. The gazetteer is 38 companies and 9
  // people, and rendered whole those two rows were most of a phone screen of
  // pills between the reader and the year row — and navigation you scroll past
  // is navigation you do not use. Nothing is dropped: the expander says how much
  // it is holding, and the facet panel still lists every entity.
  const VISIBLE_PICKS = 6;
  let companiesOpen = $state(false);
  let peopleOpen = $state(false);
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

  <!-- What has just been published. Above the browse rows because it is the one
       block that needs no knowledge of the archive to use: a reader can open the
       newest piece without knowing a year, a kind or a company name. Title only,
       no excerpt — the excerpt is the result card's job, and five paragraphs here
       would rebuild the wall of text this screen is being cleaned up. -->
  {#if latest.length}
    <div class="block">
      <span class="micro">Latest articles</span>
      <ul class="latest">
        {#each latest as item (item.message_id)}
          <li>
            <a class="latestrow" href={item.article_href}>
              <span class="pill kind {kindTone(item.kind)}"
                >{kindLabel(item.kind)}</span
              >
              <span class="text">{displaySubject(item.subject)}</span>
              <span class="date num">{item.sent_at.slice(0, 10)}</span>
            </a>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  <!-- Companies first: browsing to a company is the more common question than
       browsing to a year, and it is the only row here that names something a
       reader cannot type from memory. Year and kind are calendar and shape;
       company is a subject. People sit directly beneath it — same question
       ("who"), the same kind of answer, and adjacent so a reader scanning for a
       name finds both rows without hunting. -->
  {#if companies.length}
    <div class="block">
      <span class="micro">Browse by company</span>
      <div class="picks" id="browse-company">
        {#each companiesOpen ? companies : companies.slice(0, VISIBLE_PICKS) as company (company.entity_id)}
          <button
            class="pick wide"
            type="button"
            title="{company.articles} articles mention {company.name}"
            onclick={() => pickEntity(company.entity_id)}
          >
            <span>{company.name}</span>
            <span class="n num">{company.articles}</span>
          </button>
        {/each}
      </div>
      {#if companies.length > VISIBLE_PICKS}
        <button
          class="more"
          type="button"
          aria-expanded={companiesOpen}
          aria-controls="browse-company"
          onclick={() => (companiesOpen = !companiesOpen)}
        >
          {companiesOpen
            ? "show fewer"
            : `show all ${companies.length} companies`}
          <span class="chev" aria-hidden="true"
            >{companiesOpen ? "▴" : "▾"}</span
          >
        </button>
      {/if}
    </div>
  {/if}

  {#if people.length}
    <div class="block">
      <span class="micro">Browse by people</span>
      <div class="picks" id="browse-people">
        {#each peopleOpen ? people : people.slice(0, VISIBLE_PICKS) as person (person.entity_id)}
          <button
            class="pick wide"
            type="button"
            title="{person.articles} articles mention {person.name}"
            onclick={() => pickEntity(person.entity_id)}
          >
            <span>{person.name}</span>
            <span class="n num">{person.articles}</span>
          </button>
        {/each}
      </div>
      {#if people.length > VISIBLE_PICKS}
        <button
          class="more"
          type="button"
          aria-expanded={peopleOpen}
          aria-controls="browse-people"
          onclick={() => (peopleOpen = !peopleOpen)}
        >
          {peopleOpen ? "show fewer" : `show all ${people.length} people`}
          <span class="chev" aria-hidden="true">{peopleOpen ? "▴" : "▾"}</span>
        </button>
      {/if}
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

  {#if data}
    <p class="hint foot">
      {data.searchable_articles.toLocaleString()} searchable articles, {data.first.slice(
        0,
        4,
      )} → {data.last.slice(0, 4)}. Pick a year or a kind to browse — no query
      needed.
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
  /* The row's expander reads as a small text control under the chips, on the
     same left edge as the micro-label, so four browse rows still look like four
     labelled rows and not like four rows plus four buttons. */
  .more {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin-top: 6px;
    padding: 4px 0;
    font: inherit;
    font-size: 11px;
    color: var(--accent);
    background: none;
    border: 0;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .more:hover,
  .more:focus-visible {
    color: #fff;
  }
  .chev {
    font-size: 9px;
    color: var(--ink-faint);
  }
  /* The newest articles: one row per article, the shape of the recent-searches
     list below it — this screen now has two lists of links and they should read
     as the same kind of thing. The title truncates rather than wraps, because a
     five-row block whose rows are two lines tall is the wall of text it was. */
  ul.latest {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .latestrow {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-height: 34px;
    padding: 4px 8px;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    font-size: 13px;
    color: var(--ink-dim);
  }
  .latestrow:hover,
  .latestrow:focus-visible {
    color: var(--ink);
    background: var(--bg-hover);
    border-color: var(--line);
    text-decoration: none;
  }
  .latestrow .kind {
    flex: 0 0 auto;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    align-self: center;
  }
  .latestrow .text {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .latestrow .date {
    flex: 0 0 auto;
    font-size: 10.5px;
    color: var(--ink-faint);
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
