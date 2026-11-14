<script>
  // The facet panel is the block's now (ui-registry 1.7.0). What is left here is
  // the two translations, in lib/facets.js: the API's payload → the panel's one
  // shape, and the panel's selection → the querystring this app routes on.
  //
  // Facet counts come from the same /search response as the results (§3 NEW):
  // a separate /facets call renders counts that lag the result set by one round
  // trip, which is the bug faceted search always has.
  import FilterPanel from "../lib/components/nb/FilterPanel.svelte";
  import {
    facetGroups,
    selectionFromQuery,
    queryFromSelection,
  } from "../lib/facets.js";

  let { facets = null, query, onchange, onclear } = $props();

  // A facet panel with nothing in it is headings over empty lists — the same
  // clutter as an instruction line. Draw it only when there is at least one
  // bucket to click, which also covers a search that matched nothing.
  //
  // There is no Sender facet: the corpus has three addresses, all
  // @stratechery.com (blog@, email@, membership@), and the largest bucket is 13
  // of 1,691 articles. It cannot separate anything a reader wants separated, and
  // it renders as an email address in a panel that is otherwise about the
  // writing. The parameter still works if it is in a URL, and it still shows up
  // as a removable chip — it just is not offered as a choice.
  const groups = $derived(facetGroups(facets));
  const selected = $derived(selectionFromQuery(query));

  function change(next) {
    onchange(queryFromSelection(query, next));
  }
</script>

{#if groups.length}
  <FilterPanel
    facets={groups}
    {selected}
    onchange={change}
    onclear={() => (onclear ? onclear() : change({}))}
  />
{/if}
