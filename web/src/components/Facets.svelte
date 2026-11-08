<script>
  // Facet counts come from the same /search response as the results (§3 NEW):
  // a separate /facets call renders counts that lag the result set by one round
  // trip, which is the bug faceted search always has.
  import { groupKinds, kindActive, toggleKindGroup } from "../lib/display.js";

  let { facets = null, query, onchange } = $props();

  const maxYear = $derived(
    Math.max(1, ...(facets?.by_year || []).map((bucket) => bucket.count)),
  );
  const totalKind = $derived(
    (facets?.by_kind || []).reduce((sum, bucket) => sum + bucket.count, 0),
  );
  // daily + update are one product under two names; the panel offers one row for
  // both and filters by both kinds at once (see lib/display.js).
  const kinds = $derived(groupKinds(facets?.by_kind || []));
  // A facet panel with nothing in it is four headings over four empty lists —
  // the same clutter as an instruction line. Draw it only when there is at least
  // one bucket to click, which also covers a search that matched nothing.
  //
  // There is no Sender facet: the corpus has three addresses, all @stratechery.com
  // (blog@, email@, membership@), and the largest bucket is 13 of 1,691 articles.
  // It cannot separate anything a reader wants separated, and it renders as an
  // email address in a panel that is otherwise about the writing. The parameter
  // still works if it is in a URL, and it still shows up as a removable chip —
  // it just is not offered as a choice.
  // The entity dimension arrives only when the service has a gazetteer index, and
  // it is computed sideways — with the entity's own selection removed — so the
  // counts stay useful the moment a chip is clicked. Articles, not passages: a
  // company present across forty passages of one 2018 update is one article here,
  // which is what the result list shows.
  const entities = $derived(facets?.by_entity || []);
  // Two dimensions, not one: `by_entity` carries the whole gazetteer, so a
  // single "Company" heading listed Elon Musk and Mark Zuckerberg under it. The
  // `kind` field was always there; the panel just never read it.
  // Alphabetical, not by count. The API hands these over largest-first, which is
  // the right order for *choosing* a facet dimension but the wrong order for
  // *finding* one: 38 companies sorted by magnitude move whenever the corpus
  // grows, so a name's position is never learnable. The number is still shown,
  // so size is not hidden — it is just no longer the sort key.
  const byName = (rows) =>
    [...rows].sort((left, right) => left.name.localeCompare(right.name));
  const companies = $derived(
    byName(entities.filter((row) => row.kind === "company")),
  );
  const people = $derived(
    byName(entities.filter((row) => row.kind === "person")),
  );
  const hasBuckets = $derived(
    (facets?.by_year?.length ?? 0) > 0 ||
      (facets?.by_kind?.length ?? 0) > 0 ||
      companies.length > 0 ||
      people.length > 0,
  );

  function entityActive(id) {
    return (query.entity || []).includes(id);
  }

  function pickEntity(id) {
    const chosen = query.entity || [];
    onchange({
      ...query,
      offset: 0,
      entity: chosen.includes(id)
        ? chosen.filter((value) => value !== id)
        : [...chosen, id],
    });
  }

  function yearActive(year) {
    return query.from === `${year}-01-01` && query.to === `${year}-12-31`;
  }

  function pickYear(year) {
    const active = yearActive(year);
    onchange({
      ...query,
      offset: 0,
      from: active ? "" : `${year}-01-01`,
      to: active ? "" : `${year}-12-31`,
    });
  }
</script>

{#if hasBuckets}
  <aside class="facets">
    <section>
      <h2 class="section-title">Year</h2>
      <div class="years">
        {#each facets?.by_year || [] as bucket (bucket.year)}
          <button
            type="button"
            class:active={yearActive(bucket.year)}
            title="{bucket.articles} articles, {bucket.count} passages"
            onclick={() => pickYear(bucket.year)}
          >
            <span
              class="bar"
              style="width: {Math.round((bucket.count / maxYear) * 100)}%"
            ></span>
            <span class="label num">{bucket.year}</span>
            <span class="count num">{bucket.count}</span>
          </button>
        {/each}
      </div>
    </section>

    <section>
      <h2 class="section-title">Kind</h2>
      <ul class="toggles">
        {#each kinds as group (group.id)}
          <li>
            <button
              type="button"
              class:active={kindActive(query.kind, group.kinds)}
              onclick={() =>
                onchange({
                  ...query,
                  offset: 0,
                  kind: toggleKindGroup(query.kind, group.kinds),
                })}
            >
              <span>{group.label}</span>
              <span class="count num">{group.count}</span>
            </button>
          </li>
        {/each}
      </ul>
      <p class="hint">{totalKind} passages across {kinds.length} kinds</p>
    </section>

    {#each [{ title: "Company", rows: companies }, { title: "People", rows: people }] as dimension (dimension.title)}
      {#if dimension.rows.length}
        <section>
          <h2 class="section-title">{dimension.title}</h2>
          <ul class="toggles">
            {#each dimension.rows as row (row.entity_id)}
              <li>
                <button
                  type="button"
                  class:active={entityActive(row.entity_id)}
                  title="{row.articles} articles mention {row.name}"
                  onclick={() => pickEntity(row.entity_id)}
                >
                  <span>{row.name}</span>
                  <span class="count num">{row.articles}</span>
                </button>
              </li>
            {/each}
          </ul>
          <p class="hint">articles mentioning each, not passages</p>
        </section>
      {/if}
    {/each}

    <section>
      <h2 class="section-title">Images</h2>
      <ul class="toggles">
        {#each [["true", "with images"], ["false", "text only"]] as [value, label] (value)}
          <li>
            <button
              type="button"
              class:active={query.hasImages === value}
              onclick={() =>
                onchange({
                  ...query,
                  offset: 0,
                  hasImages: query.hasImages === value ? "" : value,
                })}
            >
              <span>{label}</span>
              <span class="count num">{facets?.has_images?.[value] ?? 0}</span>
            </button>
          </li>
        {/each}
      </ul>
    </section>
  </aside>
{/if}

<style>
  .facets {
    position: sticky;
    top: 56px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
  }
  section {
    margin-bottom: 10px;
  }
  h2.section-title {
    margin: 0 0 5px;
  }
  .years {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .years button {
    position: relative;
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 6px;
    padding: 2px 6px;
    border: 1px solid transparent;
    border-radius: 4px;
    background: none;
    font: inherit;
    color: var(--ink-dim);
    cursor: pointer;
    text-align: left;
  }
  .years button:hover {
    border-color: var(--line);
    background: var(--bg-hover);
  }
  .bar {
    position: absolute;
    inset: 2px auto 2px 2px;
    border-radius: 3px;
    background: rgba(148, 163, 184, 0.22);
    z-index: 0;
  }
  .years button.active .bar {
    background: rgba(125, 211, 252, 0.4);
  }
  .years button.active {
    color: #fff;
  }
  .label,
  .count {
    position: relative;
    z-index: 1;
  }
  .count {
    color: var(--ink-muted);
    font-size: 11px;
  }
  .years button.active .count {
    color: var(--ink);
  }
  ul.toggles {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  ul.toggles button {
    width: 100%;
    display: flex;
    justify-content: space-between;
    gap: 8px;
    border: 1px solid transparent;
    border-radius: 4px;
    background: none;
    padding: 2px 6px;
    font: inherit;
    color: var(--ink-dim);
    cursor: pointer;
    text-align: left;
  }
  ul.toggles button:hover {
    border-color: var(--line);
    background: var(--bg-hover);
  }
  ul.toggles button.active {
    border-color: rgba(125, 211, 252, 0.4);
    background: var(--accent-soft);
    color: #fff;
  }
  .count {
    color: var(--ink-faint);
  }
  ul.toggles button.active .count {
    color: var(--accent);
  }
  .hint {
    margin: 4px 0 0;
  }
  @media (max-width: 760px) {
    .facets {
      position: static;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
  }
</style>
