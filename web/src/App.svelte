<script>
  import { readState, writeState } from "./lib/query.js";
  import ArticlePage from "./components/ArticlePage.svelte";
  import SearchPage from "./components/SearchPage.svelte";
  import StatsPage from "./components/StatsPage.svelte";

  // Flat client-side routing over the querystring (§4). No adapter, no SSR:
  // Python serves dist/ and the URL is the state, which is why a result set is
  // linkable, reloadable and shareable to the phone.
  let path = $state(window.location.pathname);
  let state = $state(readState());

  function onPopState() {
    path = window.location.pathname;
    state = readState();
  }

  window.addEventListener("popstate", onPopState);

  function updateState(next) {
    state = next;
    writeState(next);
  }

  const articleMatch = $derived(path.match(/^\/article\/(\d+)\/?$/));

  function go(nextPath) {
    window.history.pushState({}, "", nextPath);
    path = nextPath;
  }
</script>

<svelte:window onpopstate={onPopState} />

<header class="top">
  <a class="brand" href="/" onclick={(event) => { event.preventDefault(); go("/"); }}>
    Stratechery <span>search</span>
  </a>
  <nav>
    <a
      href="/"
      class:active={path === "/"}
      onclick={(event) => { event.preventDefault(); go("/"); }}>search</a
    >
    <a
      href="/stats"
      class:active={path === "/stats"}
      onclick={(event) => { event.preventDefault(); go("/stats"); }}>corpus</a
    >
  </nav>
</header>

<noscript>
  <p class="noscript">
    This UI needs JavaScript. The data is also plain JSON: <a href="/stats">/stats</a>,
    <a href="/healthz">/healthz</a>, and <code>/search?q=…</code>.
  </p>
</noscript>

<main>
  {#if articleMatch}
    <ArticlePage messageId={articleMatch[1]} />
  {:else if path === "/stats"}
    <StatsPage />
  {:else}
    <SearchPage query={state} onchange={updateState} />
  {/if}
</main>

<style>
  :global(body) {
    margin: 0;
    font-family:
      ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1c1c1c;
    background: #fdfdfc;
  }
  :global(a) {
    color: #1a3d7c;
  }
  .top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.9rem 1.5rem;
    border-bottom: 1px solid #e6e6e6;
  }
  .brand {
    font-weight: 700;
    text-decoration: none;
    color: #1c1c1c;
    letter-spacing: -0.01em;
  }
  .brand span {
    font-weight: 400;
    color: #6b6b6b;
  }
  nav {
    display: flex;
    gap: 1rem;
    font-size: 0.85rem;
  }
  nav a {
    text-decoration: none;
    color: #6b6b6b;
    padding-bottom: 2px;
  }
  nav a.active {
    color: #1c1c1c;
    border-bottom: 2px solid #1a3d7c;
  }
  main {
    padding: 1.2rem 1.5rem 3rem;
    max-width: 68rem;
    margin: 0 auto;
  }
  .noscript {
    margin: 0;
    padding: 0.6rem 1.5rem;
    background: #fdeaea;
    font-size: 0.85rem;
  }
</style>
