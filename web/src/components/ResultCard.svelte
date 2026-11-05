<script>
  // The snippet arrives with <mark>…</mark> from SQLite's snippet(). It is article
  // text, so everything else is escaped before the marks go back in: the index
  // is trusted, the prose inside it is not markup.
  let { result } = $props();

  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  function safeSnippet(snippet) {
    const marked = snippet.replaceAll("<mark>", "\u0000").replaceAll("</mark>", "\u0001");
    const escaped = marked.replace(/[&<>"']/g, (character) => ESCAPES[character]);
    return escaped.replaceAll("\u0000", "<mark>").replaceAll("\u0001", "</mark>");
  }

  const date = $derived(result.sent_at.slice(0, 10));

  // Kind tints come from the house palette only: sky for the long-form
  // interviews, green for the weekly, orange for the short updates. Nothing here
  // introduces a colour the dashboard does not already use.
  const TONE = {
    interview: "info",
    weekly: "ok",
    update: "warn",
    daily: "",
    other: "",
  };
</script>

<article class="card result">
  <header>
    <span class="pill kind {TONE[result.kind] ?? ''}">{result.kind}</span>
    <span class="date num">{date}</span>
    <a class="subject" href={result.article_href}>{result.subject}</a>
  </header>
  <p class="snippet">{@html safeSnippet(result.snippet)}</p>
  <footer>
    <span>{result.sender}</span>
    <span class="num">chars {result.start_char}–{result.end_char}</span>
    <span class="num">score {result.score.toFixed(2)}</span>
    <a class="num" href={result.article_href}>#{result.chunk_id}</a>
  </footer>
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
</style>
