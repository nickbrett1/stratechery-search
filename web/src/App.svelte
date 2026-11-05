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
    Stratechery <b>Search</b>
  </a>
  <nav class="seg">
    <button
      class:on={path === "/"}
      type="button"
      onclick={() => go("/")}
      aria-current={path === "/" ? "page" : undefined}>search</button
    >
    <button
      class:on={path === "/stats"}
      type="button"
      onclick={() => go("/stats")}
      aria-current={path === "/stats" ? "page" : undefined}>corpus</button
    >
  </nav>
</header>

<noscript>
  <p class="notice">This UI needs JavaScript. The data is also plain JSON: <a href="/stats"
      >/stats</a
    >, <a href="/healthz">/healthz</a>, and <code>/search?q=…</code>.</p>
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
  .top {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 10px 18px;
    background: rgba(15, 23, 42, 0.82);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line);
  }
  .brand {
    color: var(--ink-dim);
    font-size: 13px;
    font-weight: 500;
    letter-spacing: -0.01em;
    text-decoration: none;
  }
  .brand b {
    color: #fff;
    font-weight: 600;
  }
  .brand:hover {
    text-decoration: none;
  }
  main {
    max-width: 68rem;
    margin: 0 auto;
    padding: 18px 18px 64px;
  }
  .notice {
    margin: 14px 18px 0;
  }
  @media (max-width: 600px) {
    .top {
      padding: 8px 12px;
    }
    main {
      padding: 12px 12px 48px;
    }
  }
</style>
