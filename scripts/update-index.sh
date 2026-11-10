#!/bin/sh
# Bring the search index up to date with the archive. Four steps, in order, and
# every one of them is idempotent — so the cost of running this with nothing new
# is a flatten + a rebuild, not a re-embed.
#
#   flatten       archive/index.jsonl -> derived/<id>.txt + .meta.json
#   build_index   derived -> search.sqlite (chunks, FTS, typeahead, stats)
#   embed_corpus  chunks missing from lancedb -> one Cohere call per batch
#   build_facets  derived + entities.yaml -> facets.sqlite
#
# Why a script rather than a daemon: the four steps were already four commands
# and nobody ran them. Measured 2026-11-10 — the index was three days and three
# articles behind the mailbox, and the owner's new article could not be found,
# because `build_index` refused any corpus that was not exactly 1,691 articles
# (fixed in the same change). The corpus gates are now comparisons against the
# archive, which is what makes a scheduled run safe.
#
# Run inside the service container (that is where the embedding key and the
# mounts are):
#
#   docker exec stratechery-search sh /app/scripts/update-index.sh
#
# Exit status is the *last* step's, but earlier failures stop the run: a stale
# index that half-updated is worse than one that did not update, because the
# first is a silent disagreement between search.sqlite and lancedb.
set -eu

ARCHIVE=${ARCHIVE:-/archive}
DATA=${DATA_ROOT:-/data}
DERIVED=${DERIVED:-$DATA/derived}
INDEX=${INDEX_PATH:-$DATA/index/search.sqlite}
TAXONOMY=${TAXONOMY:-/app/entities.yaml}
FACETS=${FACETS_PATH:-$DATA/index/facets.sqlite}

echo "== flatten =="
python -m stratechery_search.flatten --archive "$ARCHIVE" --out "$DERIVED"

echo "== build_index =="
python -m stratechery_search.build_index \
    --derived "$DERIVED" --archive "$ARCHIVE" --out "$INDEX"

echo "== embed_corpus =="
python -m stratechery_search.embed_corpus --index "$INDEX"

echo "== build_facets =="
python -m stratechery_search.build_facets \
    --derived "$DERIVED" --taxonomy "$TAXONOMY" --index "$INDEX" --out "$FACETS"

echo "== done =="
echo "restart the service to reload facets/gazetteer: docker restart stratechery-search"
