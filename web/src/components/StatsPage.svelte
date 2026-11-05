<script>
  import { stats } from "../lib/api.js";

  let data = $state(null);
  let error = $state(null);

  $effect(() => {
    let cancelled = false;
    stats()
      .then((body) => {
        if (!cancelled) data = body;
      })
      .catch((failure) => {
        if (!cancelled) error = failure;
      });
    return () => {
      cancelled = true;
    };
  });

  const maxYear = $derived(
    Math.max(1, ...(data?.by_year ?? []).map((bucket) => bucket.articles))
  );
</script>

{#if error}
  <p class="error">{error.message}</p>
{:else if !data}
  <p class="meta">Loading…</p>
{:else}
  <h1>The corpus</h1>
  <ul class="totals">
    <li><strong>{data.articles.toLocaleString()}</strong> articles</li>
    <li><strong>{data.searchable_articles.toLocaleString()}</strong> searchable</li>
    <li><strong>{data.transactional}</strong> transactional</li>
    <li><strong>{data.chunks.toLocaleString()}</strong> passages</li>
    <li>{data.first.slice(0, 10)} → {data.last.slice(0, 10)}</li>
  </ul>

  <!-- notes[] is rendered next to the chart on purpose: a bar chart that could
       imply 2017 was a quiet year must be contradicted in words, not in a memo. -->
  {#each data.notes as note (note.code)}
    <p class="note"><strong>{note.year}</strong> — {note.message}</p>
  {/each}

  <section>
    <h2>Articles per year</h2>
    <div class="years">
      {#each data.by_year as bucket (bucket.year)}
        <div class="row">
          <span class="label">{bucket.year}</span>
          <span class="bar" style="width: {Math.round((bucket.articles / maxYear) * 100)}%"
          ></span>
          <span class="count">{bucket.articles}</span>
        </div>
      {/each}
    </div>
  </section>

  <section class="columns">
    <div>
      <h2>By kind</h2>
      <ul>
        {#each data.by_kind as bucket (bucket.kind)}
          <li>{bucket.kind} <span class="count">{bucket.articles}</span></li>
        {/each}
      </ul>
    </div>
    <div>
      <h2>By sender</h2>
      <ul>
        {#each data.by_sender as bucket (bucket.sender)}
          <li>{bucket.sender || "(unknown)"} <span class="count">{bucket.articles}</span></li>
        {/each}
      </ul>
    </div>
    <div>
      <h2>Images</h2>
      <ul>
        <li>{data.images.distinct_assets.toLocaleString()} distinct assets</li>
        <li>{data.images.articles_with_images.toLocaleString()} articles with images</li>
      </ul>
      <h2>Index</h2>
      <ul>
        <li>flattener {data.index.flattener_version}</li>
        <li>chunker {data.index.chunker_version}</li>
        <li>built {data.index.built_at}</li>
      </ul>
    </div>
  </section>

  <p class="meta"><a href="/">← search</a> · <a href="/healthz">/healthz</a></p>
{/if}

<style>
  h1 {
    font-size: 1.3rem;
    margin: 0.2rem 0 0.8rem;
  }
  h2 {
    font-size: 0.72rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #6b6b6b;
    margin: 1.2rem 0 0.4rem;
  }
  .totals {
    display: flex;
    flex-wrap: wrap;
    gap: 1.2rem;
    list-style: none;
    padding: 0;
    font-size: 0.85rem;
  }
  .totals strong {
    font-size: 1.05rem;
  }
  .years {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-width: 32rem;
  }
  .row {
    display: grid;
    grid-template-columns: 3rem 1fr 3rem;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
  }
  .bar {
    display: block;
    height: 0.75rem;
    background: #b9cdf0;
    min-width: 1px;
  }
  .count {
    text-align: right;
    font-variant-numeric: tabular-nums;
    color: #6b6b6b;
    font-size: 0.78rem;
  }
  .columns {
    display: flex;
    flex-wrap: wrap;
    gap: 2.5rem;
  }
  .columns ul {
    list-style: none;
    padding: 0;
    margin: 0;
    font-size: 0.82rem;
  }
  .columns li {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    max-width: 16rem;
  }
  .note {
    background: #fbf6dc;
    border-left: 3px solid #d8c34a;
    padding: 0.4rem 0.6rem;
    font-size: 0.82rem;
    max-width: 34rem;
  }
  .error {
    background: #fdeaea;
    border-left: 3px solid #c0392b;
    padding: 0.5rem 0.7rem;
  }
  .meta {
    color: #6b6b6b;
    font-size: 0.8rem;
    margin-top: 1.5rem;
  }
  a {
    color: #1a3d7c;
  }
</style>
