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
</script>

<article class="result">
  <header>
    <span class="kind kind-{result.kind}">{result.kind}</span>
    <span class="date">{date}</span>
    <a class="subject" href={result.article_href}>{result.subject}</a>
  </header>
  <p class="snippet">{@html safeSnippet(result.snippet)}</p>
  <footer>
    <span>{result.sender}</span>
    <span>chars {result.start_char}–{result.end_char}</span>
    <span>score {result.score.toFixed(2)}</span>
    <a href={result.article_href}>#{result.chunk_id}</a>
  </footer>
</article>

<style>
  .result {
    border-bottom: 1px solid #e6e6e6;
    padding: 0.7rem 0;
  }
  header {
    display: flex;
    gap: 0.6rem;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .subject {
    font-weight: 600;
    color: #1a3d7c;
    text-decoration: none;
  }
  .subject:hover {
    text-decoration: underline;
  }
  .date,
  footer {
    color: #6b6b6b;
    font-size: 0.78rem;
    font-variant-numeric: tabular-nums;
  }
  footer {
    display: flex;
    gap: 0.9rem;
    margin-top: 0.3rem;
  }
  .snippet {
    margin: 0.35rem 0 0;
    line-height: 1.5;
  }
  .snippet :global(mark) {
    background: #fff2a8;
    padding: 0 1px;
  }
  .kind {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 1px 5px;
    border-radius: 3px;
    background: #eef1f6;
    color: #40506b;
  }
  .kind-interview {
    background: #ede7f7;
    color: #4b2f7a;
  }
  .kind-weekly {
    background: #e7f2ea;
    color: #2c5c3a;
  }
  .kind-daily {
    background: #eef1f6;
  }
</style>
