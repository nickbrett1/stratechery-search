<!--
  TEMPORARY. The readout for the type-ahead latency gate (design note §7).
  Only mounted when the app was loaded with `?perf=1`; delete this file with the
  flag. It is deliberately plain: a phone, a thumb, and a number that must be
  readable in one glance.
-->
<script>
  import { clearSamples, percentile, perf } from "../lib/perf.svelte.js";

  const typing = $derived(perf.samples.filter((sample) => sample.partial));
  const typingLatency = $derived(typing.map((sample) => sample.keyToPaint));
  const last = $derived(perf.samples.length ? perf.samples.at(-1) : null);
  const rows = $derived(
    perf.samples
      .slice(-6)
      .reverse()
      .map((sample) => ({
        ...sample,
        label: `${sample.q}${sample.partial ? "" : " ⏎"}`,
      })),
  );
  const gate = $derived(percentile(typingLatency, 0.95));

  let copied = $state(false);

  async function copy() {
    const payload = JSON.stringify(
      {
        conn: perf.conn,
        zero: perf.zero,
        samples: perf.samples,
      },
      null,
      2,
    );
    try {
      await navigator.clipboard.writeText(payload);
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch {
      // Clipboard is permission-gated on some phones; fall back to the console,
      // which is at least reachable over remote debugging.
      console.log(payload);
    }
  }

  function hide() {
    perf.enabled = false;
  }

  function tone(value) {
    if (value === null) return "";
    if (value <= 150) return "good";
    if (value <= 300) return "warn";
    return "bad";
  }
</script>

<aside class="hud" aria-label="type-ahead latency">
  <div class="top">
    <span class="title">type-ahead</span>
    <span class="big {tone(gate)}">
      {gate ?? "–"}<small>ms p95</small>
    </span>
    <span class="sub">p50 {percentile(typingLatency, 0.5) ?? "–"}</span>
    <span class="sub">n={typing.length}</span>
    <span class="zero {perf.zero ? 'bad' : 'good'}">
      zero-flicker {perf.zero}
    </span>
  </div>

  {#if last}
    <div class="last">
      <span class="q">{last.q}</span>
      <span class="num {tone(last.keyToPaint)}">{last.keyToPaint}ms</span>
      <span class="parts">
        {last.debounce ?? "?"} debounce + {last.network ?? "?"} net
        {#if last.server !== null}(server {last.server}){/if}
      </span>
      <span class="parts">{last.total} results</span>
    </div>
  {/if}

  {#if rows.length > 1}
    <ol class="rows">
      {#each rows as row (row.at + row.q)}
        <li>
          <span class="q">{row.label}</span>
          <span class="num {tone(row.keyToPaint)}">{row.keyToPaint}</span>
          <span class="parts">{row.total}</span>
          <span class="parts">{row.network ?? "–"} net</span>
        </li>
      {/each}
    </ol>
  {/if}

  <div class="foot">
    <span class="conn">conn {perf.conn}</span>
    <button type="button" onclick={clearSamples}>clear</button>
    <button type="button" onclick={copy}
      >{copied ? "copied" : "copy json"}</button
    >
    <button type="button" onclick={hide}>hide</button>
  </div>
</aside>

<style>
  .hud {
    position: fixed;
    left: 8px;
    right: 8px;
    bottom: 8px;
    z-index: 50;
    padding: 8px 10px;
    border-radius: 10px;
    background: rgba(18, 18, 20, 0.92);
    color: #f2f0e9;
    font:
      12px/1.45 ui-monospace,
      SFMono-Regular,
      Menlo,
      monospace;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
    max-height: 46vh;
    overflow: auto;
  }
  .top {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
  }
  .title {
    opacity: 0.65;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 10px;
  }
  .big {
    font-size: 20px;
    font-weight: 700;
  }
  .big small {
    font-size: 10px;
    font-weight: 400;
    opacity: 0.6;
    margin-left: 2px;
  }
  .sub {
    opacity: 0.7;
  }
  .zero {
    margin-left: auto;
    font-weight: 700;
  }
  .last,
  .rows li {
    display: flex;
    gap: 8px;
    align-items: baseline;
    padding-top: 4px;
  }
  .q {
    flex: 0 0 96px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .num {
    flex: 0 0 auto;
    font-weight: 700;
  }
  .parts {
    opacity: 0.6;
    white-space: nowrap;
  }
  .rows {
    margin: 6px 0 0;
    padding: 6px 0 0;
    border-top: 1px solid rgba(255, 255, 255, 0.15);
    list-style: none;
  }
  .good {
    color: #7ddc9b;
  }
  .warn {
    color: #f2c14e;
  }
  .bad {
    color: #ff8a80;
  }
  .foot {
    display: flex;
    gap: 6px;
    align-items: center;
    padding-top: 8px;
  }
  .conn {
    margin-right: auto;
    opacity: 0.55;
  }
  button {
    /* A thumb, not a mouse: 44px is the smallest honest control. */
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid rgba(255, 255, 255, 0.3);
    border-radius: 8px;
    background: transparent;
    color: inherit;
    font: inherit;
  }
</style>
