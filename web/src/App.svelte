<script>
  import { readState, writeState } from "./lib/query.js";
  import ArticlePage from "./components/ArticlePage.svelte";
  // TEMPORARY: latency instrument for the type-ahead gate (?perf=1).
  import PerfHud from "./components/PerfHud.svelte";
  import SearchPage from "./components/SearchPage.svelte";
  import StatsPage from "./components/StatsPage.svelte";
  import { perf } from "./lib/perf.svelte.js";

  // Flat client-side routing over the querystring (§4). No adapter, no SSR:
  // Python serves dist/ and the URL is the state, which is why a result set is
  // linkable, reloadable and shareable to the phone.
  let path = $state(window.location.pathname);
  let state = $state(readState());

  // How many in-app entries sit behind this one. history.length cannot answer
  // that — it counts the whole tab session, so a cold launch from a link would
  // look like it had somewhere to go back to and would leave the app instead.
  // Every entry the app creates is stamped with its own depth; anything it
  // cannot see (a fresh document, a link from elsewhere) reads as 0.
  function currentDepth() {
    const marker = window.history.state?.depth;
    return typeof marker === "number" ? marker : 0;
  }
  let depth = $state(currentDepth());

  // Stamp the entry the app was opened on, so it is known and counted as zero.
  if (typeof window.history.state?.depth !== "number") {
    window.history.replaceState(
      { ...(window.history.state || {}), depth: 0 },
      "",
      window.location.href,
    );
  }

  function syncFromUrl() {
    path = window.location.pathname;
    state = readState();
    depth = currentDepth();
  }

  function updateState(next, { replace = false } = {}) {
    state = next;
    // Typing must not fill the back stack: a debounced keystroke is a refinement
    // of the current page, so it replaces the entry instead of adding one. Enter
    // and every click still push (design note §3).
    if (!replace) depth += 1;
    writeState(next, { replace, historyState: { depth } });
  }

  const articleMatch = $derived(path.match(/^\/article\/(\d+)\/?$/));
  // A drill-in is anything that is not the search root: an article, the corpus
  // page. Those are where a phone needs a way back.
  const isDrillIn = $derived(path !== "/");

  function go(nextPath, { replace = false } = {}) {
    const nextDepth = replace ? depth : depth + 1;
    if (replace)
      window.history.replaceState(
        { ...(window.history.state || {}), depth: nextDepth },
        "",
        nextPath,
      );
    else window.history.pushState({ depth: nextDepth }, "", nextPath);
    syncFromUrl();
  }

  // Running as a web app on a phone means no browser chrome, so no browser back
  // control — every drill-in has to carry its own. history.back() is the right
  // primitive rather than a link to "/": it returns to the *results page you
  // left*, querystring and all, instead of a brand new empty search. A deep
  // link opened cold (a shared article, a home-screen relaunch) has nothing of
  // ours behind it, so that case replaces itself with the search root instead
  // of walking out of the app.
  function back() {
    if (depth > 0) window.history.back();
    else go("/", { replace: true });
  }

  // Internal links are followed in-document. A plain anchor would trigger a full
  // page load on every result click — a flash, a re-fetch of the bundle, and the
  // querystring state rebuilt from scratch — which is exactly the thing that
  // makes a web app feel like a web page.
  function onDocumentClick(event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return; // let the browser open a new tab
    const anchor =
      event.target instanceof Element ? event.target.closest("a") : null;
    if (
      !anchor ||
      anchor.target === "_blank" ||
      anchor.hasAttribute("download")
    )
      return;
    const href = anchor.getAttribute("href");
    if (!href) return;
    const url = new URL(href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (
      url.pathname === window.location.pathname &&
      url.search === window.location.search
    )
      return;
    event.preventDefault();
    go(`${url.pathname}${url.search}${url.hash}`);
  }
</script>

<svelte:window onpopstate={syncFromUrl} />

<svelte:document onclick={onDocumentClick} />

<header class="top" class:drill={isDrillIn}>
  {#if isDrillIn}
    <button
      class="back"
      type="button"
      onclick={back}
      aria-label="Back to results"
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 16 16"
        aria-hidden="true"
        focusable="false"
      >
        <path
          d="M10 3 5 8l5 5"
          fill="none"
          stroke="currentColor"
          stroke-width="1.9"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
      <span class="lbl">back</span>
    </button>
  {/if}
  <a
    class="brand"
    href="/"
    onclick={(event) => {
      event.preventDefault();
      go("/");
    }}
  >
    <!-- The same mark the tab and the home screen use, at 22px. It is the only
         colour in a header that is otherwise ink on the ground colour — the
         accent lens and the cream match lines are the whole point of it. -->
    <img
      class="mark"
      src="/favicon.svg"
      alt=""
      width="22"
      height="22"
      aria-hidden="true"
    />
    <span class="name">Stratechery <b>Search</b></span>
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
  <p class="notice">
    This UI needs JavaScript. The data is also plain JSON: <a href="/stats"
      >/stats</a
    >, <a href="/healthz">/healthz</a>, and <code>/search?q=…</code>.
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

<!-- TEMPORARY: the type-ahead latency readout, mounted only when the app was
     loaded with `?perf=1`. Delete with lib/perf.svelte.js. -->
{#if perf.enabled}
  <PerfHud />
{/if}

<style>
  .top {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 18px;
    background: rgba(15, 23, 42, 0.82);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line);
  }
  /* The one control a standalone web app cannot inherit from the browser. Sticky
     with the header, so it is reachable at any scroll position in a long article.
     The pill is kept slim (32/34px) so the header does not grow, and the ::after
     below quietly inflates the *hit* area to ~44x44 — what a thumb needs — without
     changing what is drawn. */
  .back {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex: 0 0 auto;
    height: 32px;
    padding: 0 10px 0 7px;
    color: var(--ink-dim);
    background: transparent;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-pill);
    font: inherit;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.02em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .back:hover,
  .back:focus-visible {
    color: #fff;
    border-color: rgba(125, 211, 252, 0.45);
    background: var(--bg-hover);
    outline: none;
  }
  .back svg {
    display: block;
  }
  /* Tap target, not paint: iOS and Android both honour a transparent box that
     extends past the visible pill. */
  .back::after {
    content: "";
    position: absolute;
    inset: -6px -5px;
    border-radius: var(--radius-pill);
  }
  .seg {
    margin-left: auto;
  }
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    color: var(--ink-dim);
    font-size: 13px;
    font-weight: 500;
    letter-spacing: -0.01em;
    text-decoration: none;
    min-width: 0;
  }
  /* The tile in favicon.svg is the ground colour, which is also the header, so
     without a hairline the mark floats as a bare magnifier. One ring makes it
     read as the app icon it is. */
  .mark {
    flex: 0 0 auto;
    width: 22px;
    height: 22px;
    border-radius: 6px;
    box-shadow: 0 0 0 1px var(--line-strong);
  }
  .name {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
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
      gap: 8px;
    }
    main {
      padding: 12px 12px 48px;
    }
    .back {
      height: 34px;
    }
  }
  /* On a drill-in at phone width, the nav steps aside: the back button already
     occupies that corner, and keeping both squeezed the wordmark to
     "Stratechery Searc…". The brand and the icon stay — they are the point of the
     top left — and /stats is one tap away through back. */
  @media (max-width: 420px) {
    .top.drill .seg {
      display: none;
    }
  }
  /* Narrowest phones: the word "back" goes, the arrow and the 44px target stay.
     The accessible name does not, because it is on aria-label. */
  @media (max-width: 379px) {
    .back .lbl {
      display: none;
    }
    .back {
      padding: 0 9px;
    }
    .brand {
      font-size: 12.5px;
    }
  }
</style>
