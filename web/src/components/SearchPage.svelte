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
  <button type="submit">Search</button>
</form>

{#if response?.query?.expansions?.length}
  <p class="rewrite">
    Matched via vocabulary:
    {#each response.query.expansions as expansion (expansion.matched)}
      <strong>{expansion.matched}</strong> → {expansion.canonical}
      ({expansion.aliases.join(", ")})
    {/each}
    <span class="hint">Not a related-concept graph — phase 1 has none.</span>
  </p>
{/if}

{#if error}
  <p class="error">
    {error.message}
    {#if error.status === 503}
      The index is not built yet; <code>/healthz</code> reports the reason.
    {/if}
  </p>
{/if}

<div class="layout">
  <section class="results" class:loading>
    {#if response}
      <p class="meta">
        {response.total.toLocaleString()} passages · showing {pageStart}–{pageEnd} ·
        {response.took_ms} ms
      </p>
      {#each response.results as result (result.chunk_id)}
        <ResultCard {result} />
      {/each}
      {#if response.results.length === 0}
        <p class="empty">No passages match. Try fewer words.</p>
      {/if}
      <nav class="pager">
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
      <p class="empty">Type a query. Facets appear once there are results to facet.</p>
    {/if}
  </section>

  <Facets facets={response?.facets ?? null} {query} {onchange} />
</div>

<style>
  .bar {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.8rem;
  }
  .bar input {
    flex: 1;
    padding: 0.55rem 0.7rem;
    border: 1px solid #c9cfda;
    border-radius: 4px;
    font-size: 1rem;
  }
  .bar button {
    padding: 0 1rem;
    border: 0;
    border-radius: 4px;
    background: #1a3d7c;
    color: #fff;
    font-size: 0.95rem;
    cursor: pointer;
  }
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 210px;
    gap: 1.5rem;
    align-items: start;
  }
  @media (max-width: 720px) {
    .layout {
      grid-template-columns: 1fr;
    }
  }
  .results.loading {
    opacity: 0.55;
  }
  .meta,
  .empty,
  .hint {
    color: #6b6b6b;
    font-size: 0.8rem;
  }
  .rewrite {
    background: #fbf6dc;
    border-left: 3px solid #d8c34a;
    padding: 0.4rem 0.6rem;
    font-size: 0.82rem;
  }
  .error {
    background: #fdeaea;
    border-left: 3px solid #c0392b;
    padding: 0.5rem 0.7rem;
    font-size: 0.85rem;
  }
  .pager {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.8rem;
  }
  .pager button {
    border: 1px solid #c9cfda;
    background: #fff;
    border-radius: 4px;
    padding: 0.25rem 0.6rem;
    cursor: pointer;
  }
  .pager button:disabled {
    opacity: 0.4;
    cursor: default;
  }
</style>
