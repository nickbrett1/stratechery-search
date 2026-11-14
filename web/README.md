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
COMPONENTS_REGISTRY_URL=http://ryzen:3012/r/v1.7.0 \
  npx shadcn-svelte@1.8.1 add theme --yes --overwrite
COMPONENTS_REGISTRY_URL=http://ryzen:3012/r/v1.7.0 \
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

| Screen             | Blocks                                                                                                                                          |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| the whole frame    | `AppShell` — `mark` (the favicon), `actions` (`search \| corpus`), `back` (this app's own button, `history.back()`), `footer`, `titleTag="div"` |
| `/stats`           | `StatStrip` (four KPIs), `StatusView` (one lane per kind), `BurstTimeline` for the year chart (`showValues` + a prose `caption`)                |
| `/article/{id}`    | `ResultList` for "Elsewhere in the archive", its own title as an `h2`                                                                           |
| results            | `ResultList` — one card, ranked rows, hairline between, `ResultCard` in the `row` slot                                                          |
| results, zero rows | `EmptyState`, cause-aware (filters vs query)                                                                                                    |
| `/` results tally  | `StatStrip`                                                                                                                                     |
| filters            | `FilterPanel` — the year facet as `mode: "single"` + `layout: "bars"`, kind/company/people as checkbox sets, images as one choice from two      |

Nothing in this directory is a locally built layout any more. The last holdout
was the facet panel, and the reason it held out was an interaction-model gap, not
a shape one: a year here is a from/to **range**, and a checkbox list can express
2019 _and_ 2021, which the API cannot query. Registry 1.7.0 closed it inside the
block (`mode: "single"` renders radios, selecting replaces, clicking the active
one clears the range) and added `layout: "bars"` for the histogram, so the panel
moved in without this app teaching the block about years.

What stays local is the _translation_, in `src/lib/facets.js`: the API's
`by_year` / `by_kind` / `by_entity` / `has_images` payload becomes the panel's
one shape, and the panel's selection becomes the `from`/`to`/`kind`/`entity`/
`has_images` query this app routes on. Two shapes, one place that knows both.

Two rules about `src/lib/components/nb/`: it is generated, so it is in
`.prettierignore` (prettier rewriting it would put a diff on every install, and
pinning a version only means something if the file is what was published), and
a change to one of those files belongs upstream in `ui-registry` as a new
version — never here.

### The rendered check

`e2e/blocks.spec.js` stubs the API and asserts what only a browser can show: that
`--bg` computes to `rgb(15, 23, 42)` on `body` (a token that is present but not
applied reads as `""`), that a `.card` gets `8px` from the theme, that `StatStrip`
renders four figures, `StatusView` one lane per kind, `ResultList` the neighbour
rows, and `EmptyState` the right advice for the cause.
