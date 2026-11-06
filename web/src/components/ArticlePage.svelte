<script>
  import { article } from "../lib/api.js";
  import { displaySubject, kindLabel } from "../lib/display.js";

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
  <p class="notice bad">{error.message}</p>
{:else if !data}
  <p class="hint">Loading…</p>
{:else}
  <header class="card head">
    <div class="crumbs">
      <span class="pill info kind">{kindLabel(data.kind)}</span>
      <span class="num date">{data.sent_at.slice(0, 10)}</span>
      {#if data.is_transactional}<span class="pill warn">transactional</span
        >{/if}
    </div>
    <h1>{displaySubject(data.subject)}</h1>
    <dl class="facts">
      <div>
        <dt class="micro">from</dt>
        <dd>{data.from_header}</dd>
      </div>
      <div>
        <dt class="micro">words</dt>
        <dd class="num">{data.words.toLocaleString()}</dd>
      </div>
      <div>
        <dt class="micro">chars</dt>
        <dd class="num">{data.chars.toLocaleString()}</dd>
      </div>
      <div>
        <dt class="micro">passages</dt>
        <dd class="num">{data.chunks.length}</dd>
      </div>
      <div>
        <dt class="micro">est tokens</dt>
        <dd class="num">
          {data.est_tokens.toLocaleString()} <span class="hint">chars/4</span>
        </dd>
      </div>
    </dl>
  </header>

  {#if data.images.length}
    <section class="images">
      {#each data.images as image, index (image.url_path + index)}
        {#if image.href}
          <img
            src={image.href}
            alt="article image {index + 1}"
            loading="lazy"
          />
        {:else}
          <!-- The 2 url-only refs: never fabricate an asset path. -->
          <a
            class="unmirrored num"
            href={image.url_path}
            rel="noopener noreferrer"
            target="_blank"
          >
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
        (candidate) =>
          offset >= candidate.start_char && offset < candidate.end_char,
      )}
      <p id={chunk ? `c${chunk.chunk_id.replace(":", "-")}` : undefined}>
        {paragraph}
      </p>
    {/each}
  </article>
{/if}

<style>
  .head {
    padding: 12px 14px;
    margin-bottom: 14px;
  }
  .crumbs {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 6px;
  }
  .kind {
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .date {
    font-size: 11px;
    color: var(--ink-faint);
  }
  .head h1 {
    font-size: 20px;
    line-height: 1.25;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0 0 10px;
    color: #fff;
    max-width: 44rem;
  }
  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 18px;
    margin: 0;
    font-size: 11.5px;
  }
  .facts div {
    display: flex;
    gap: 6px;
    align-items: baseline;
  }
  .facts dt {
    margin: 0;
  }
  .facts dd {
    margin: 0;
    color: var(--ink-dim);
  }
  .images {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 14px;
  }
  .images img {
    /* An image is the only thing on this page that arrives with an intrinsic
       width, and the mirror holds banner images far wider than a phone. Capping
       max-height alone was not enough: a 689px image inside a 390px viewport
       scrolls the whole document sideways and drags the sticky header — brand
       included — out past the right edge. */
    max-width: 100%;
    height: auto;
    max-height: 96px;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--bg-raised);
  }
  .unmirrored {
    font-size: 11px;
    color: var(--ink-muted);
    border: 1px dashed var(--line-strong);
    padding: 6px 9px;
    border-radius: var(--radius-sm);
  }
  .unmirrored:hover {
    color: var(--accent);
    border-color: rgba(125, 211, 252, 0.4);
    text-decoration: none;
  }
  /* Reading column: 15px is a size up from the 13px UI text on purpose — this
     page is read, not scanned, and the whole point of the reader is the prose. */
  .text {
    max-width: 46rem;
    font-size: 14.5px;
    line-height: 1.68;
    color: var(--ink-dim);
  }
  .text p {
    margin: 0 0 0.85rem;
    overflow-wrap: break-word;
  }
  .text p:target {
    background: var(--mark-bg);
    color: var(--ink);
    box-shadow:
      -6px 0 0 var(--mark-bg),
      6px 0 0 var(--mark-bg);
    border-radius: 2px;
  }
  @media (max-width: 600px) {
    .head h1 {
      font-size: 17px;
    }
    .text {
      font-size: 14px;
    }
  }
</style>
