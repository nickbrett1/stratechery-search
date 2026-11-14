<script>
  import { stats } from "../lib/api.js";
  import { groupKinds } from "../lib/display.js";
  import BurstTimeline from "../lib/components/nb/BurstTimeline.svelte";
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

  // The year chart is the house block, with two props added for exactly this
  // page: `showValues`, because a bar table prints its figures rather than
  // hiding them in a tooltip, and `caption`, because the caveat below is a
  // sentence and the block's `chartLabel` is a 10px uppercase micro label.
  const years = $derived(
    (data?.by_year ?? []).map((bucket) => ({
      label: String(bucket.year),
      count: bucket.articles,
    })),
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

  <BurstTimeline
    title="Articles per year"
    chartLabel=""
    orientation="horizontal"
    showValues
    intervals={years}
  >
    {#snippet caption()}
      {#if dailyFrom}
        Daily updates reach this archive from {dailyFrom}; earlier bars are the
        weekly and free articles, not a quieter year.
      {:else}
        One bar per published year.
      {/if}
    {/snippet}
  </BurstTimeline>

  <!-- One list, not the old two. The second was "By sender", which is three
       @stratechery.com addresses over 1,691 articles: a breakdown of the
       newsletter plumbing, not of the writing. By kind is the one a reader can
       act on. -->
  <StatusView cards={kindCards} />
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
  .notice {
    margin: 10px 0;
  }
</style>
