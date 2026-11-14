<script>
  import { readState, writeState } from "./lib/query.js";
  import AppShell from "./lib/components/nb/AppShell.svelte";
  import ArticlePage from "./components/ArticlePage.svelte";
  import SearchPage from "./components/SearchPage.svelte";
  import StatsPage from "./components/StatsPage.svelte";

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

  // On a phone a drill-in bar has less horizontal room than the four things it
  // wants to hold: an icon, a back control, the product name and the view
  // switcher. The switcher is the one that can go — the back control is the only
  // way out of an article on a home-screen app, and the name is the product —
  // and /stats is one back away. The old bespoke header hid it at 420px; the
  // width is now a prop of the layout rather than a guess in CSS.
  let narrow = $state(window.matchMedia("(max-width: 520px)").matches);
  function onResize() {
    narrow = window.matchMedia("(max-width: 520px)").matches;
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
  // Named `goBack`, not `back`: the block's slot is `{#snippet back()}`, and a
  // snippet declaration shadows a same-named function in this scope, so
  // `onclick={back}` would call the snippet and render it inside the event
  // handler — which is not a render context.
  function goBack() {
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

<svelte:window onpopstate={syncFromUrl} onresize={onResize} />

<svelte:document onclick={onDocumentClick} />

<!--
  The frame is the house block, not a local header. Three of its slots are used
  and two props are deliberate:

    `back`      is a snippet because this app's back is `history.back()`, an
                action. The block's own back pill is a link, and a link pushes a
                new entry instead of popping one — the bug the `depth` stamping
                above exists to avoid.
    `titleTag`  is `div` because the pages own the `h1`: an article's subject,
                "The corpus". The bar carries the product's name, not the page's.
-->
<AppShell title="Stratechery Search" titleTag="div">
  {#snippet mark()}
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
  {/snippet}

  {#snippet back()}
    {#if isDrillIn}
      <!-- The one control a standalone web app cannot inherit from the browser.
           Sticky with the header, so it is reachable at any scroll position in a
           long article. Kept slim (32/34px) so the header does not grow, with a
           transparent ::after that inflates the *hit* area to ~44x44 — what a
           thumb needs — without changing what is drawn. -->
      <button
        class="back"
        type="button"
        onclick={goBack}
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
  {/snippet}

  {#snippet actions()}
    {#if !(isDrillIn && narrow)}
      <nav class="seg" aria-label="Views">
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
    {/if}
  {/snippet}

  <noscript>
    <p class="notice">
      This UI needs JavaScript. The data is also plain JSON: <a href="/stats"
        >/stats</a
      >, <a href="/healthz">/healthz</a>, and <code>/search?q=…</code>.
    </p>
  </noscript>

  {#if articleMatch}
    <ArticlePage messageId={articleMatch[1]} />
  {:else if path === "/stats"}
    <StatsPage />
  {:else}
    <SearchPage query={state} onchange={updateState} />
  {/if}
</AppShell>

<style>
  /* No footer. The shell always draws one (1.7.0 has no "no footer" prop) and
     this app has nothing to put in it: the navigation is the header's, and a
     second set of links at the bottom only duplicated it — its /stats link
     pointed at the page you were already on and, being plain <a href> rather
     than go(), made the browser reload to get there. Hide the strip rather than
     fill it, and drop this rule when the block grows the prop. */
  :global(.shell > footer.footer) {
    display: none;
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
  .notice {
    margin: 14px 0 0;
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
  }
</style>
