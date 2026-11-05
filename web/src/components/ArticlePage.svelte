<script>
  import { article } from "../lib/api.js";

  let { messageId } = $props();

  let data = $state(null);
  let error = $state(null);

  $effect(() => {
    let cancelled = false;
    data = null;
    error = null;
    article(messageId)
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

  // A chunk anchor from a search result (#c152-3) scrolls the reader to that
  // passage: the chunk table exists so the article view can honour the link.
  $effect(() => {
    if (!data) return;
    const hash = window.location.hash;
    if (!hash.startsWith("#c")) return;
    const target = document.getElementById(hash.slice(1));
    if (target) target.scrollIntoView({ block: "center" });
  });

  const chunkRanges = $derived(data?.chunks ?? []);
</script>

{#if error}
  <p class="error">{error.message}</p>
{:else if !data}
  <p class="meta">Loading…</p>
{:else}
  <header class="head">
    <p class="crumbs">
      <a href="/">← search</a>
      <span class="kind">{data.kind}</span>
      <span>{data.sent_at.slice(0, 10)}</span>
      {#if data.is_transactional}<span class="badge">transactional</span>{/if}
    </p>
    <h1>{data.subject}</h1>
    <dl class="facts">
      <div><dt>from</dt><dd>{data.from_header}</dd></div>
      <div><dt>words</dt><dd>{data.words.toLocaleString()}</dd></div>
      <div><dt>chars</dt><dd>{data.chars.toLocaleString()}</dd></div>
      <div><dt>chunks</dt><dd>{data.chunks.length}</dd></div>
      <div><dt>est tokens</dt><dd>{data.est_tokens.toLocaleString()} <small>(chars/4)</small></dd></div>
    </dl>
  </header>

  {#if data.images.length}
    <section class="images">
      {#each data.images as image, index (image.url_path + index)}
        {#if image.href}
          <img src={image.href} alt="article image {index + 1}" loading="lazy" />
        {:else}
          <!-- The 2 url-only refs: never fabricate an asset path. -->
          <a class="unmirrored" href={image.url_path} rel="noopener noreferrer" target="_blank">
            image {index + 1} — not mirrored, open at source
          </a>
        {/if}
      {/each}
    </section>
  {/if}

  <article class="text">
    {#each data.text.split("\n\n") as paragraph, index (index)}
      {@const offset = data.text.indexOf(paragraph)}
      {@const chunk = chunkRanges.find(
        (candidate) => offset >= candidate.start_char && offset < candidate.end_char
      )}
      <p id={chunk ? `c${chunk.chunk_id.replace(":", "-")}` : undefined}>
        {paragraph}
      </p>
    {/each}
  </article>
{/if}

<style>
  .head h1 {
    font-size: 1.35rem;
    line-height: 1.25;
    margin: 0.3rem 0 0.6rem;
  }
  .crumbs {
    display: flex;
    gap: 0.7rem;
    align-items: center;
    font-size: 0.8rem;
    color: #6b6b6b;
  }
  .crumbs a {
    color: #1a3d7c;
  }
  .kind {
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.68rem;
    background: #eef1f6;
    padding: 1px 5px;
    border-radius: 3px;
  }
  .badge {
    background: #fbf6dc;
    border: 1px solid #d8c34a;
    border-radius: 3px;
    padding: 0 4px;
  }
  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    font-size: 0.78rem;
    margin: 0 0 1rem;
  }
  .facts div {
    display: flex;
    gap: 0.35rem;
  }
  dt {
    color: #6b6b6b;
  }
  dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
  }
  .images {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 1rem;
  }
  .images img {
    max-height: 90px;
    border: 1px solid #e6e6e6;
    border-radius: 3px;
  }
  .unmirrored {
    font-size: 0.75rem;
    color: #6b6b6b;
    border: 1px dashed #c9cfda;
    padding: 0.3rem 0.5rem;
    border-radius: 3px;
  }
  .text {
    max-width: 44rem;
    line-height: 1.62;
  }
  .text p {
    margin: 0 0 0.9rem;
  }
  .text p:target {
    background: #fff8d0;
  }
  .error {
    background: #fdeaea;
    border-left: 3px solid #c0392b;
    padding: 0.5rem 0.7rem;
  }
  .meta {
    color: #6b6b6b;
  }
</style>
