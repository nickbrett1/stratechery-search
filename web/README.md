# Frontend (Svelte)

This directory holds the project's Svelte app. It is a **sub-build**: the
project's primary language is `python`, and this app is built to **static
assets** that the `python` server serves. There is no adapter and no server
of its own here — a plain Vite build, or SvelteKit's `adapter-static`.

## Where the build output lands

`npm run build` writes the static assets to `web/dist`.

## Who serves it

The **`python` server is the web server**. There is **no Node runtime** in
the image: the frontend is built here and its `web/dist`
output is copied into the image by the Dockerfile.

genproj scaffolds a **minimal serving harness** as the `python`
entry point (`src/<pkg>/__main__.py`): it binds `0.0.0.0` on the container port
(3000 unless `docker-container.exposePort` says otherwise), serves
`web/dist`, answers the declared healthcheck path with
200, and 404s everything else. That is what makes the container come up and pass
its own `HEALTHCHECK` - the harness half of the project.

The **domain half is yours**: the wire protocol, the business endpoints, and
anything you want the server to do beyond serving the UI. Replace the harness
(and its standard-library-only constraint) with your application whenever you
are ready.

## How it is built

- **CI** builds this directory in its own `.buildkite/pipeline.yml` step (on a
  Node image), before the `python` build, so a broken frontend fails CI
  rather than being discovered at image-build time.
- **Docker** builds it in a `frontend` stage in the root `Dockerfile` and copies
  `web/dist` into the `python` build stage and the
  runtime image.

## Working on it locally

```sh
cd web
npm install
npm run dev
```

## The design system is installed, not copied

This app renders on the house design system: the `@nb` theme and the house
blocks, both installed from `ui-registry` with the pinned `shadcn-svelte` CLI.
Nothing in this directory owns a colour, a pill or a card.

```bash
# The base is pinned by version, and the version is a path segment: registry
# items are immutable, so a bump is a deliberate edit with a diff.
COMPONENTS_REGISTRY_URL=http://ryzen:3012/r/v1.3.0 \
  npx shadcn-svelte@1.8.1 add theme --yes --overwrite
COMPONENTS_REGISTRY_URL=http://ryzen:3012/r/v1.3.0 \
  npx shadcn-svelte@1.8.1 add app-shell stat-strip result-list filter-panel \
    empty-state status-view detail-panel burst-timeline request-form \
    --yes --overwrite
```

`add theme` merges the item's `cssVars` and `css` into `src/app.css`, which is
where `--bg`, `.card`, `.pill`, `.micro`, `.seg` and the rest now come from;
`src/lib/theme.css` is gone. The blocks land in `src/lib/components/nb/` — the
install target is relative to `aliases.components`, not to the project root, so
the item says `nb/StatStrip.svelte` and the CLI resolves `$lib` from
`components.json`. `vite.config.js` carries that `$lib` alias and
`@tailwindcss/vite`; `jsconfig.json` carries `paths` so the CLI can resolve it.

`--overwrite` is not tidiness: `add` asks a second question about overwriting
existing CSS variables that `--yes` does not answer, and non-interactively it
prints the question and **exits 0 having written nothing**.

### Which screens are the blocks

| Screen             | Blocks                                                    |
| ------------------ | --------------------------------------------------------- |
| `/stats`           | `StatStrip` (four KPIs), `StatusView` (one lane per kind) |
| `/article/{id}`    | `ResultList` for "Elsewhere in the archive"               |
| results, zero rows | `EmptyState`, cause-aware (filters vs query)              |
| `/` results tally  | `StatStrip`                                               |

Still local, and deliberately so: the top bar (`AppShell` has no slot for the
search/corpus nav), the result cards (`ResultList` rows carry a title, a pill,
meta and one snippet — not highlighted passages with a fold), the facet panel
(`FilterPanel` expects a facet shape this API does not emit), and the per-year
chart (`BurstTimeline` models time buckets as a vertical sparkline and hard-codes
its "recent bursts" caption). Those four are the block changes that would be
needed before the conversion can finish — see the note in `ui-registry`.

### The rendered check

`e2e/blocks.spec.js` stubs the API and asserts what only a browser can show: that
`--bg` computes to `rgb(15, 23, 42)` on `body` (a token that is present but not
applied reads as `""`), that a `.card` gets `8px` from the theme, that `StatStrip`
renders four figures, `StatusView` one lane per kind, `ResultList` the neighbour
rows, and `EmptyState` the right advice for the cause.
