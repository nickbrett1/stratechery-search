<script>
  // Facet counts come from the same /search response as the results (§3 NEW):
  // a separate /facets call renders counts that lag the result set by one round
  // trip, which is the bug faceted search always has.
  import { toggle } from "../lib/query.js";

  let { facets = null, query, onchange } = $props();

  const maxYear = $derived(
    Math.max(1, ...(facets?.by_year || []).map((bucket) => bucket.count))
  );
  const totalKind = $derived(
    (facets?.by_kind || []).reduce((sum, bucket) => sum + bucket.count, 0)
  );

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
          <span class="bar" style="width: {Math.round((bucket.count / maxYear) * 100)}%"></span>
          <span class="label num">{bucket.year}</span>
          <span class="count num">{bucket.count}</span>
        </button>
      {/each}
    </div>
  </section>

  <section>
    <h2 class="section-title">Kind</h2>
    <ul class="toggles">
      {#each facets?.by_kind || [] as bucket (bucket.kind)}
        <li>
          <button
            type="button"
            class:active={query.kind.includes(bucket.kind)}
            onclick={() => onchange({ ...query, offset: 0, kind: toggle(query.kind, bucket.kind) })}
          >
            <span>{bucket.kind}</span>
            <span class="count num">{bucket.count}</span>
          </button>
        </li>
      {/each}
    </ul>
    <p class="hint">{totalKind} passages across {facets?.by_kind?.length || 0} kinds</p>
  </section>

  <section>
    <h2 class="section-title">Sender</h2>
    <ul class="toggles">
      {#each facets?.by_sender || [] as bucket (bucket.sender)}
        <li>
          <button
            type="button"
            class:active={query.sender.includes(bucket.sender)}
            onclick={() => onchange({ ...query, offset: 0, sender: toggle(query.sender, bucket.sender) })}
          >
            <span>{bucket.sender || "(unknown)"}</span>
            <span class="count num">{bucket.count}</span>
          </button>
        </li>
      {/each}
    </ul>
  </section>

  <section>
    <h2 class="section-title">Images</h2>
    <ul class="toggles">
      {#each [["true", "with images"], ["false", "text only"]] as [value, label] (value)}
        <li>
          <button
            type="button"
            class:active={query.hasImages === value}
            onclick={() =>
              onchange({ ...query, offset: 0, hasImages: query.hasImages === value ? "" : value })}
          >
            <span>{label}</span>
            <span class="count num">{facets?.has_images?.[value] ?? 0}</span>
          </button>
        </li>
      {/each}
    </ul>
  </section>

  <section>
    <label class="checkbox">
      <input
        type="checkbox"
        checked={query.includeTransactional}
        onchange={(event) =>
          onchange({ ...query, offset: 0, includeTransactional: event.currentTarget.checked })}
      />
      <span>include transactional mail</span>
    </label>
    <p class="hint">
      Off by default: 44 of 1,691 messages are access codes, receipts and renewals.
    </p>
  </section>
</aside>

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
  .checkbox {
    display: flex;
    gap: 6px;
    align-items: center;
    color: var(--ink-dim);
    cursor: pointer;
  }
  @media (max-width: 760px) {
    .facets {
      position: static;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
  }
</style>
