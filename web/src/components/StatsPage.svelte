<script>
  import { stats } from "../lib/api.js";
  import { groupKinds } from "../lib/display.js";
  import StatStrip from "../lib/components/nb/StatStrip.svelte";
  import StatusView from "../lib/components/nb/StatusView.svelte";

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

  // daily + update are one product under two names (see lib/display.js): the
  // corpus is 1,058 daily updates, not 502 of one thing and 556 of another.
  const kinds = $derived(groupKinds(data?.by_kind ?? []));

  // The KPI row is the house block, not a local grid: the shape (micro label,
  // big tabular figure, footnote) is the one the registry publishes, so this
  // page and a portfolio page are the same component with different data.
  const kpis = $derived([
    {
      label: "articles",
      value: data?.articles.toLocaleString() ?? "—",
      sub: `${data?.searchable_articles.toLocaleString() ?? "—"} searchable`,
    },
    {
      label: "passages",
      value: data?.chunks.toLocaleString() ?? "—",
      sub: "indexed in FTS5",
    },
    {
      label: "images",
      value: data?.images.distinct_assets.toLocaleString() ?? "—",
      sub: `${data?.images.articles_with_images.toLocaleString() ?? "—"} articles`,
    },
    {
      label: "transactional",
      value: data?.transactional ?? "—",
      sub: "access codes, receipts, renewals — never searched",
    },
  ]);

  // One lane, one row per kind: the block carries the count in the head and a
  // pill per row, which is the same shape a status board uses.
  const kindCards = $derived([
    {
      title: "by kind",
      count: data?.articles ?? 0,
      tone: "info",
      rows: kinds.map((bucket) => ({
        title: bucket.label,
        pill: { label: bucket.articles.toLocaleString(), tone: "info" },
      })),
    },
  ]);

  const maxYear = $derived(
    Math.max(1, ...(data?.by_year ?? []).map((bucket) => bucket.articles)),
  );

  // The per-year chart has one honest caveat and it belongs on the chart, not in
  // a warning box above it: this archive is a mailbox, so the bars before the
  // daily update was subscribed to are weekly and free articles, not a quieter
  // publishing year. The date comes from /stats rather than being typed here, so
  // it moves when the archive does.
  function monthYear(iso) {
    if (!iso) return "";
    const date = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
    return date.toLocaleDateString("en-GB", {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    });
  }
  const dailyFrom = $derived(monthYear(data?.searchable?.daily_from));
</script>

{#if error}
  <p class="notice bad">{error.message}</p>
{:else if !data}
  <p class="hint">Loading…</p>
{:else}
  <h1>The corpus</h1>
  <p class="hint sub">
    {data.first.slice(0, 10)} → {data.last.slice(0, 10)} · flattener {data.index
      .flattener_version} · chunker {data.index.chunker_version} · built {data
      .index.built_at}
  </p>

  <!-- The house stat card: micro-label, big tabular figure, footnote. Same shape
       as a deepseek-balance .card, so the two read as one product family. -->
  <StatStrip stats={kpis} />

  <!-- A note can still appear — the indexer flags a year that is short *after*
       the daily update began reaching the archive, which the caption below
       cannot explain. Years before that boundary are the caption's job instead,
       so the same fact is not stated twice. -->
  {#each data.notes as note (note.code)}
    <p class="notice warn"><b>{note.year}</b> — {note.message}</p>
  {/each}

  <div class="card chart">
    <h3>Articles per year</h3>
    <div class="note hint">
      {#if dailyFrom}
        Daily updates reach this archive from {dailyFrom}; earlier bars are the
        weekly and free articles, not a quieter year.
      {:else}
        One bar per published year.
      {/if}
    </div>
    <div class="years">
      {#each data.by_year as bucket (bucket.year)}
        <div class="row">
          <span class="label num">{bucket.year}</span>
          <span class="track"
            ><span
              class="bar"
              style="width: {Math.round((bucket.articles / maxYear) * 100)}%"
            ></span></span
          >
          <span class="count num">{bucket.articles}</span>
        </div>
      {/each}
    </div>
  </div>

  <!-- One list, not the old two. The second was "By sender", which is three
       @stratechery.com addresses over 1,691 articles: a breakdown of the
       newsletter plumbing, not of the writing. By kind is the one a reader can
       act on. -->
  <StatusView cards={kindCards} />

  <p class="hint footer">
    <a href="/">← search</a> · <a href="/healthz">/healthz</a> ·
    <a href="/stats">/stats</a>
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
  .chart {
    padding: 10px 12px 8px;
    margin: 12px 0;
  }
  .chart h3 {
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
  .notice {
    margin: 10px 0;
  }
  .footer {
    margin-top: 18px;
  }
</style>
