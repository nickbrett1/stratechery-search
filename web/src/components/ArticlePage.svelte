<script>
  import { article, similar } from "../lib/api.js";
  import ResultList from "../lib/components/nb/ResultList.svelte";
  import {
    displaySubject,
    highlightParts,
    kindLabel,
    queryTerms,
  } from "../lib/display.js";

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

  // "Elsewhere in the archive" — phase 4. A second request, started only once
  // the article itself has rendered: the page must never wait on 245 MB of
  // vectors, and a 503 here (`similar_unavailable`) leaves the block absent
  // rather than failing the article, which is a correctness property and not
  // only a latency one. Nothing is said if the block cannot be fetched.
  let neighbours = $state(null);
  let neighbourError = $state(null);
  $effect(() => {
    if (!data) return;
    let cancelled = false;
    neighbours = null;
    neighbourError = null;
    similar(messageId)
      .then((body) => {
        if (!cancelled) neighbours = body;
      })
      .catch((failure) => {
        if (!cancelled) neighbourError = failure;
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

  // "Elsewhere" as the house ranked list: the distance and the kind pill stay,
  // because they are what makes the list checkable in two seconds, but the row
  // shape is the published one rather than a local <ol>.
  const nearItems = $derived(
    (neighbours?.results ?? []).map((near) => {
      const detail = [
        near.sent_at.slice(0, 10),
        `distance ${near.distance.toFixed(3)}`,
      ];
      if (near.more_passages > 0) {
        detail.push(
          `${near.matched_passages} of this article's passages matched`,
        );
      }
      return {
        href: near.article_href,
        title: displaySubject(near.subject),
        pill: { label: kindLabel(near.kind), tone: "info" },
        meta: detail.join(" · "),
        snippet: `${near.excerpt}…`,
      };
    }),
  );

  const chunkRanges = $derived(data?.chunks ?? []);

  // The question that led here. It is carried on the drill-in link
  // (lib/display.js `articleHref`), not in the article API: what an article
  // contains is a fact about the archive, what the reader typed is a fact about
  // their session, and only the second one belongs in a link.
  const searchedFor =
    new URLSearchParams(window.location.search).get("q") ?? "";
  const terms = $derived(queryTerms(searchedFor));
  const hits = $derived(
    terms.length
      ? highlightParts(data?.text ?? "", terms).filter((part) => part.hit)
          .length
      : 0,
  );
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

  {#if hits}
    <p class="hint found">
      {hits}
      {hits === 1 ? "match" : "matches"} for <b>{searchedFor}</b> — from the search
      you came from.
    </p>
  {/if}

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
        <!-- Highlighted as text nodes, never as HTML: the body is mail, and the
             query is whatever the reader typed. -->
        {#each highlightParts(paragraph, terms) as part, partIndex (partIndex)}
          {#if part.hit}<mark>{part.text}</mark>{:else}{part.text}{/if}
        {/each}
      </p>
    {/each}
  </article>

  <!-- Copy discipline (phase 4 §7): this is a ranked, dated list a reader can
       verify in two seconds. It is never "related topics" or "themes" — that
       would be a claim about the corpus, which the project excludes. -->
  {#if neighbours?.results?.length}
    <section class="elsewhere">
      <p class="hint">
        Nearest passages by embedding, best first. Nothing here is a judgement
        about what the article is <em>about</em>.
      </p>
      <!-- `titleTag` puts the block's own title in the outline as an h2, which
           is why the page does not render the heading itself: a section title
           outside the block and the block's untitled list were two halves of one
           thing, and the block already prints the count beside it. -->
      <ResultList
        title="Elsewhere in the archive"
        titleTag="h2"
        items={nearItems}
        initialCount={5}
      />
    </section>
  {/if}
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
  .elsewhere {
    max-width: 46rem;
    margin-top: 28px;
    border-top: 1px solid var(--line);
    padding-top: 14px;
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
