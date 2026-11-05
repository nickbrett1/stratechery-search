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
  <p class="notice bad">{error.message}</p>
{:else if !data}
  <p class="hint">Loading…</p>
{:else}
  <h1>The corpus</h1>
  <p class="hint sub">
    {data.first.slice(0, 10)} → {data.last.slice(0, 10)} · flattener {data.index.flattener_version} ·
    chunker {data.index.chunker_version} · built {data.index.built_at}
  </p>

  <!-- The house stat card: micro-label, big tabular figure, footnote. Same shape
       as a deepseek-balance .card, so the two read as one product family. -->
  <div class="topcards">
    <div class="card">
      <div class="t micro">articles</div>
      <div class="v num">{data.articles.toLocaleString()}</div>
      <div class="s num">{data.searchable_articles.toLocaleString()} searchable</div>
    </div>
    <div class="card">
      <div class="t micro">passages</div>
      <div class="v num">{data.chunks.toLocaleString()}</div>
      <div class="s">indexed in FTS5</div>
    </div>
    <div class="card">
      <div class="t micro">images</div>
      <div class="v num">{data.images.distinct_assets.toLocaleString()}</div>
      <div class="s num">{data.images.articles_with_images.toLocaleString()} articles</div>
    </div>
    <div class="card">
      <div class="t micro">transactional</div>
      <div class="v num">{data.transactional}</div>
      <div class="s">excluded from search</div>
    </div>
  </div>

  <!-- notes[] is rendered next to the chart on purpose: a bar chart that could
       imply 2017 was a quiet year must be contradicted in words, not in a memo. -->
  {#each data.notes as note (note.code)}
    <p class="notice warn"><b>{note.year}</b> — {note.message}</p>
  {/each}

  <div class="card chart">
    <h3>Articles per year</h3>
    <div class="note hint">One bar per published year; the gap in 2017 is a sampling artefact.</div>
    <div class="years">
      {#each data.by_year as bucket (bucket.year)}
        <div class="row">
          <span class="label num">{bucket.year}</span>
          <span class="track"
            ><span class="bar" style="width: {Math.round((bucket.articles / maxYear) * 100)}%"
            ></span></span
          >
          <span class="count num">{bucket.articles}</span>
        </div>
      {/each}
    </div>
  </div>

  <section class="columns">
    <div class="card">
      <h3>By kind</h3>
      <ul>
        {#each data.by_kind as bucket (bucket.kind)}
          <li><span>{bucket.kind}</span><span class="count num">{bucket.articles}</span></li>
        {/each}
      </ul>
    </div>
    <div class="card">
      <h3>By sender</h3>
      <ul>
        {#each data.by_sender as bucket (bucket.sender)}
          <li>
            <span>{bucket.sender || "(unknown)"}</span
            ><span class="count num">{bucket.articles}</span>
          </li>
        {/each}
      </ul>
    </div>
  </section>

  <p class="hint footer">
    <a href="/">← search</a> · <a href="/healthz">/healthz</a> · <a href="/stats">/stats</a>
  </p>
{/if}

<style>
  h1 {
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0 0 4px;
    color: #fff;
  }
  .sub {
    margin: 0 0 14px;
  }
  .topcards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 8px;
  }
  .topcards .card {
    padding: 8px 10px;
  }
  .topcards .v {
    font-size: 18px;
    font-weight: 700;
    margin-top: 2px;
  }
  .topcards .s {
    font-size: 11px;
    color: var(--ink-muted);
    margin-top: 2px;
  }
  .chart {
    padding: 10px 12px 8px;
    margin: 12px 0;
  }
  .chart h3,
  .columns h3 {
    font-size: 12px;
    font-weight: 600;
    margin: 0 0 2px;
    color: #f1f5f9;
  }
  .chart .note {
    margin: 0 0 8px;
  }
  .years {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-width: 36rem;
  }
  .row {
    display: grid;
    grid-template-columns: 2.6rem 1fr 2.4rem;
    align-items: center;
    gap: 8px;
    font-size: 11.5px;
  }
  .label {
    color: var(--ink-muted);
  }
  .track {
    height: 9px;
    background: var(--bg-sunken);
    border-radius: 2px;
    overflow: hidden;
  }
  .bar {
    display: block;
    height: 100%;
    background: rgba(125, 211, 252, 0.55);
    min-width: 1px;
  }
  .count {
    text-align: right;
    color: var(--ink-dim);
  }
  .columns {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 8px;
    margin-top: 12px;
  }
  .columns .card {
    padding: 10px 12px;
  }
  .columns ul {
    list-style: none;
    padding: 0;
    margin: 6px 0 0;
    font-size: 12px;
  }
  .columns li {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 3px 0;
    border-bottom: 1px solid var(--line);
    color: var(--ink-dim);
  }
  .columns li:last-child {
    border-bottom: 0;
  }
  .columns .count {
    color: var(--ink-muted);
  }
  .notice {
    margin: 10px 0;
  }
  .footer {
    margin-top: 18px;
  }
</style>
