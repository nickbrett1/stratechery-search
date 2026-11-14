<script>
  /**
   * AppShell — the house application frame.
   *
   * Replaces the bespoke top bars in stratechery-search, deepseek-balance and
   * data-download. Sticky header with a backdrop blur, wordmark + micro subtitle,
   * back pill for non-root views, 68rem main, and a footer strip.
   */
  let {
    title = "Application",
    subtitle = "",
    backHref = "",
    backLabel = "← back",
    status = "",
    statusTone = "ok",
    children,
    footer,
  } = $props();
</script>

<div class="shell">
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        {#if backHref}
          <a href={backHref} class="pill back">{backLabel}</a>
        {/if}
        <div class="titles">
          {#if subtitle}
            <span class="micro">{subtitle}</span>
          {/if}
          <h1 class="wordmark">{title}</h1>
        </div>
      </div>
      {#if status}
        <span class="pill {statusTone}">{status}</span>
      {/if}
    </div>
  </header>

  <main class="content">
    {@render children?.()}
  </main>

  <footer class="footer">
    <div class="footer-inner">
      {#if footer}
        {@render footer()}
      {:else}
        <span class="micro">house design system · @nb</span>
      {/if}
    </div>
  </footer>
</div>

<style>
  .shell {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--bg);
    color: var(--ink);
  }

  .topbar {
    position: sticky;
    top: 0;
    z-index: 20;
    /* Derived from the ground token, not a literal: the bar is the page
       colour at 88%, which is what makes the blur read as glass. */
    background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line);
  }

  .topbar-inner {
    max-width: 68rem;
    margin: 0 auto;
    padding: 12px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .titles {
    display: flex;
    flex-direction: column;
  }

  .wordmark {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
  }

  .back {
    text-decoration: none;
  }

  .content {
    flex: 1;
    width: 100%;
    max-width: 68rem;
    margin: 0 auto;
    padding: 24px 16px;
  }

  .footer {
    border-top: 1px solid var(--line);
    padding: 16px;
    background: var(--bg);
  }

  .footer-inner {
    max-width: 68rem;
    margin: 0 auto;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
</style>
