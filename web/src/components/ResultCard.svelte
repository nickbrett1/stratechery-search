<script>
  import { displaySubject, kindLabel } from "../lib/display.js";

  // The snippet arrives with <mark>…</mark> from SQLite's snippet(). It is article
  // text, so everything else is escaped before the marks go back in: the index
  // is trusted, the prose inside it is not markup.
  // `passages` are the other chunks of the same article that matched: folded
  // under the best-ranked one so a page reads as articles, not as repetitions.
  let { result, passages = [] } = $props();

  let open = $state(false);

  const ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };

  function safeSnippet(snippet) {
    const marked = snippet
      .replaceAll("<mark>", "\u0000")
      .replaceAll("</mark>", "\u0001");
    const escaped = marked.replace(
      /[&<>"']/g,
      (character) => ESCAPES[character],
    );
    return escaped
      .replaceAll("\u0000", "<mark>")
      .replaceAll("\u0001", "</mark>");
  }

  const date = $derived(result.sent_at.slice(0, 10));
  const ordinal = (chunkId) => Number(chunkId.split(":")[1]) + 1;

  // Kind tints come from the house palette only: sky for the long-form
  // interviews, green for the weekly, orange for the short updates. Nothing here
  // introduces a colour the dashboard does not already use.
  const TONE = {
    interview: "info",
    weekly: "ok",
    // daily and update are one product under two names (see lib/display.js), so
    // they cannot carry two colours as if they were two kinds of thing.
    update: "warn",
    daily: "warn",
    other: "",
  };
</script>

<article class="card result">
  <header>
    <span class="pill kind {TONE[result.kind] ?? ''}"
      >{kindLabel(result.kind)}</span
    >
    <span class="date num">{date}</span>
    <a class="subject" href={result.article_href}
      >{displaySubject(result.subject)}</a
    >
  </header>
  <p class="snippet">{@html safeSnippet(result.snippet)}</p>
  <!-- One thing only: which passage of the article this is. The sender is the
       same address on every card, and character offsets and BM25 scores are
       index internals — three lines of developer telemetry under every snippet
       in a product read on a phone. -->
  <footer>
    <a class="num" href={result.article_href}
      >passage {ordinal(result.chunk_id)} →</a
    >
  </footer>

  {#if passages.length}
    <button
      class="more"
      type="button"
      aria-expanded={open}
      onclick={() => (open = !open)}
    >
      {open
        ? "hide"
        : `${passages.length} more ${passages.length === 1 ? "passage" : "passages"}`}
      <span class="chev" aria-hidden="true">{open ? "▴" : "▾"}</span>
    </button>
    {#if open}
      {#each passages as passage (passage.chunk_id)}
        <div class="passage">
          <p class="snippet">{@html safeSnippet(passage.snippet)}</p>
          <footer>
            <a class="num" href={passage.article_href}
              >passage {ordinal(passage.chunk_id)} →</a
            >
          </footer>
        </div>
      {/each}
    {/if}
  {/if}
</article>

<style>
  .result {
    padding: 9px 11px 8px;
    transition:
      background 120ms ease,
      border-color 120ms ease;
  }
  .result:hover {
    background: var(--bg-hover);
    border-color: var(--line-strong);
  }
  header {
    display: flex;
    gap: 8px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .kind {
    text-transform: uppercase;
    letter-spacing: 0.05em;
    align-self: center;
  }
  .date {
    color: var(--ink-faint);
    font-size: 11px;
  }
  .subject {
    font-weight: 600;
    font-size: 13.5px;
    color: var(--ink);
  }
  .subject:hover {
    color: #fff;
    text-decoration: none;
  }
  .snippet {
    margin: 5px 0 6px;
    font-size: 12.5px;
    line-height: 1.55;
    color: var(--ink-dim);
    /* Article prose contains bare URLs and long identifiers; without this a
       single token scrolls the whole phone viewport sideways. */
    overflow-wrap: anywhere;
  }
  footer {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    font-size: 10.5px;
    color: var(--ink-faint);
  }
  footer a {
    color: var(--ink-faint);
  }
  footer a:hover {
    color: var(--accent);
  }
  .more {
    /* A full-width tap target at the card's own inset, not an 11px link: this is
       the control that reveals the rest of an article on a phone. */
    display: flex;
    align-items: center;
    gap: 5px;
    width: 100%;
    margin: 2px 0 0;
    padding: 6px 0 2px;
    font: inherit;
    font-size: 11px;
    color: var(--accent);
    background: none;
    border: 0;
    border-top: 1px solid var(--line);
    cursor: pointer;
    text-align: left;
    -webkit-tap-highlight-color: transparent;
  }
  .more:hover {
    color: #fff;
  }
  .chev {
    font-size: 9px;
    color: var(--ink-faint);
  }
  /* A folded passage keeps the card's left edge so the fold reads as depth
     rather than as another result. */
  .passage {
    margin-top: 8px;
    padding-left: 9px;
    border-left: 2px solid var(--line);
  }
  .passage .snippet {
    margin-top: 0;
  }
</style>
