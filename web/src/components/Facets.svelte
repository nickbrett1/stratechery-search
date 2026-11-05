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
    <h2>Year</h2>
    <div class="years">
      {#each facets?.by_year || [] as bucket (bucket.year)}
        <button
          type="button"
          class:active={yearActive(bucket.year)}
          title="{bucket.articles} articles, {bucket.count} passages"
          onclick={() => pickYear(bucket.year)}
        >
          <span class="bar" style="width: {Math.round((bucket.count / maxYear) * 100)}%"
          ></span>
          <span class="label">{bucket.year}</span>
          <span class="count">{bucket.count}</span>
        </button>
      {/each}
    </div>
  </section>

  <section>
    <h2>Kind</h2>
    <ul class="toggles">
      {#each facets?.by_kind || [] as bucket (bucket.kind)}
        <li>
          <button
            type="button"
            class:active={query.kind.includes(bucket.kind)}
            onclick={() => onchange({ ...query, offset: 0, kind: toggle(query.kind, bucket.kind) })}
          >
            {bucket.kind} <span class="count">{bucket.count}</span>
          </button>
        </li>
      {/each}
    </ul>
    <p class="hint">{totalKind} passages across {facets?.by_kind?.length || 0} kinds</p>
  </section>

  <section>
    <h2>Sender</h2>
    <ul class="toggles">
      {#each facets?.by_sender || [] as bucket (bucket.sender)}
        <li>
          <button
            type="button"
            class:active={query.sender.includes(bucket.sender)}
            onclick={() => onchange({ ...query, offset: 0, sender: toggle(query.sender, bucket.sender) })}
          >
            {bucket.sender || "(unknown)"} <span class="count">{bucket.count}</span>
          </button>
        </li>
      {/each}
    </ul>
  </section>

  <section>
    <h2>Images</h2>
    <ul class="toggles">
      {#each [["true", "with images"], ["false", "text only"]] as [value, label] (value)}
        <li>
          <button
            type="button"
            class:active={query.hasImages === value}
            onclick={() =>
              onchange({ ...query, offset: 0, hasImages: query.hasImages === value ? "" : value })}
          >
            {label} <span class="count">{facets?.has_images?.[value] ?? 0}</span>
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
      include transactional mail
    </label>
    <p class="hint">
      Off by default: 44 of 1,691 messages are access codes, receipts and renewals.
    </p>
  </section>
</aside>

<style>
  .facets {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    font-size: 0.85rem;
  }
  h2 {
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #6b6b6b;
    margin: 0 0 0.4rem;
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
    padding: 1px 4px;
    border: 0;
    background: none;
    cursor: pointer;
    text-align: left;
    font: inherit;
  }
  .bar {
    position: absolute;
    inset: 0 auto 0 0;
    background: #e4e9f2;
    z-index: 0;
  }
  .years button.active .bar {
    background: #b9cdf0;
  }
  .label,
  .count {
    position: relative;
    z-index: 1;
  }
  .count {
    color: #6b6b6b;
    font-variant-numeric: tabular-nums;
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
    gap: 0.5rem;
    border: 0;
    background: none;
    padding: 2px 4px;
    font: inherit;
    cursor: pointer;
    text-align: left;
  }
  ul.toggles button.active {
    background: #b9cdf0;
  }
  .hint {
    margin: 0.35rem 0 0;
    color: #6b6b6b;
    font-size: 0.75rem;
  }
  .checkbox {
    display: flex;
    gap: 0.4rem;
    align-items: center;
  }
</style>
