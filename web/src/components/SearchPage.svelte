<script>
  import { search } from "../lib/api.js";
  import Facets from "./Facets.svelte";
  import ResultCard from "./ResultCard.svelte";

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

  $effect(() => {
    if (!query.q) {
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
    onchange({ ...query, q: typed.trim(), offset: 0 });
  }

  const pageStart = $derived((response?.offset ?? 0) + 1);
  const pageEnd = $derived((response?.offset ?? 0) + (response?.results.length ?? 0));
  const hasPrev = $derived((response?.offset ?? 0) > 0);
  const hasNext = $derived(
    response ? response.offset + response.results.length < response.total : false
  );
</script>

<form class="bar" onsubmit={submit}>
  <input
    type="search"
    bind:value={typed}
    placeholder="Search 1,691 Stratechery articles — aggregation theory, smiling curve…"
    aria-label="Search the corpus"
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

<div class="layout">
  <section class="results" class:loading>
    {#if response}
      <div class="tally">
        <span><b class="num">{response.total.toLocaleString()}</b> passages</span>
        <span class="hint num">showing {pageStart}–{pageEnd}</span>
        <span class="pill info num">{response.took_ms} ms</span>
      </div>
      <div class="cards">
        {#each response.results as result (result.chunk_id)}
          <ResultCard {result} />
        {/each}
      </div>
      {#if response.results.length === 0}
        <p class="notice">No passages match. Try fewer words.</p>
      {/if}
      <nav class="seg pager">
        <button
          type="button"
          disabled={!hasPrev}
          onclick={() => onchange({ ...query, offset: Math.max(0, response.offset - response.limit) })}
        >
          ← previous
        </button>
        <button
          type="button"
          disabled={!hasNext}
          onclick={() => onchange({ ...query, offset: response.offset + response.limit })}
        >
          next →
        </button>
      </nav>
    {:else if !query.q}
      <p class="notice">Type a query. Facets appear once there are results to facet.</p>
    {/if}
  </section>

  <Facets facets={response?.facets ?? null} {query} {onchange} />
</div>

<style>
  .bar {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
  }
  .bar input {
    flex: 1;
    min-width: 0;
    font-size: 13.5px;
    padding: 8px 11px;
  }
  .bar .btn {
    padding: 0 16px;
    font-size: 12.5px;
  }
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 224px;
    gap: 22px;
    align-items: start;
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
</style>
