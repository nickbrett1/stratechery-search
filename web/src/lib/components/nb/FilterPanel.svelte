<script>
  /**
   * FilterPanel — facet filter bar.
   *
   * Desktop: card panel with grouped checkboxes and counts.
   * Mobile (<600px): chip row that opens a slide-over / bottom sheet so
   * filters remain usable on small screens without taking over the document.
   */
  let { facets = [], selected = {}, onchange, onclear } = $props();

  let mobileOpen = $state(false);

  function isChecked(groupKey, value) {
    return (selected[groupKey] ?? []).includes(value);
  }

  function toggle(groupKey, value) {
    const current = selected[groupKey] ?? [];
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value];
    const updated = { ...selected, [groupKey]: next };
    if (next.length === 0) delete updated[groupKey];
    onchange?.(updated);
  }

  const activeCount = $derived(
    Object.values(selected).reduce((acc, arr) => acc + (arr?.length ?? 0), 0),
  );
</script>

<!-- Mobile chip row (<600px) -->
<div class="mobile-chip-row">
  <button
    type="button"
    class="btn mobile-filter-btn"
    onclick={() => (mobileOpen = true)}
  >
    <span>Filters</span>
    {#if activeCount > 0}
      <span class="pill info">{activeCount}</span>
    {/if}
  </button>
  {#if activeCount > 0}
    <button type="button" class="btn clear-btn" onclick={() => onclear?.()}>
      Clear all
    </button>
  {/if}
</div>

<!-- Desktop sidebar panel / mobile bottom sheet -->
<div class="filter-panel" class:mobile-open={mobileOpen}>
  <div class="card panel-card">
    <div class="panel-header">
      <div class="panel-title-wrap">
        <span class="micro">Filters</span>
        {#if activeCount > 0}
          <span class="pill info">{activeCount}</span>
        {/if}
      </div>
      <div class="panel-actions">
        {#if activeCount > 0}
          <button
            type="button"
            class="btn text-btn"
            onclick={() => onclear?.()}
          >
            Clear
          </button>
        {/if}
        <button
          type="button"
          class="btn close-btn"
          onclick={() => (mobileOpen = false)}
          aria-label="Close filters"
        >
          ✕
        </button>
      </div>
    </div>

    {#each facets as facet}
      <div class="facet-group">
        <div class="facet-label micro">{facet.label}</div>
        <div class="facet-options">
          {#each facet.options as opt}
            <label class="facet-option">
              <input
                type="checkbox"
                checked={isChecked(facet.key, opt.value)}
                onchange={() => toggle(facet.key, opt.value)}
              />
              <span class="option-label">{opt.label}</span>
              {#if opt.count !== undefined}
                <span class="option-count num hint">{opt.count}</span>
              {/if}
            </label>
          {/each}
        </div>
      </div>
    {/each}
  </div>
</div>

{#if mobileOpen}
  <div
    class="backdrop"
    onclick={() => (mobileOpen = false)}
    role="presentation"
  ></div>
{/if}

<style>
  .mobile-chip-row {
    display: none;
    gap: 8px;
    align-items: center;
    margin-bottom: 12px;
  }

  .panel-card {
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .panel-title-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .panel-actions {
    display: flex;
    gap: 6px;
    align-items: center;
  }

  .close-btn {
    display: none;
    padding: 2px 8px;
  }

  .text-btn {
    padding: 2px 8px;
    font-size: 11px;
  }

  .facet-group {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .facet-label {
    margin: 0;
  }

  .facet-options {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .facet-option {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
    cursor: pointer;
    color: var(--ink-dim);
    padding: 2px 0;
  }

  .facet-option:hover {
    color: var(--ink);
  }

  .option-label {
    flex: 1;
  }

  .option-count {
    font-size: 11px;
  }

  .backdrop {
    display: none;
  }

  @media (max-width: 600px) {
    .mobile-chip-row {
      display: flex;
    }

    .filter-panel {
      display: none;
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 50;
      max-height: 80vh;
      overflow-y: auto;
    }

    .filter-panel.mobile-open {
      display: block;
    }

    .panel-card {
      border-radius: 12px 12px 0 0;
      border-bottom: 0;
      background: var(--bg);
    }

    .close-btn {
      display: inline-block;
    }

    .backdrop {
      display: block;
      position: fixed;
      inset: 0;
      background: color-mix(in srgb, var(--bg) 62%, transparent);
      z-index: 40;
    }
  }
</style>
