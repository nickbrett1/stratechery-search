"""Entrypoint: ``python -m stratechery_search``.

Configuration is environment-only, because the thing that starts this process is
a container definition, not a human typing flags:

===================================  ==========================================
``PORT``                             listen port (default 8080)
``INDEX_PATH``                       ``search.sqlite`` (default ``$DATA_ROOT/index/search.sqlite``)
``ARCHIVE_ROOT``                     read-only archive mount (default ``/archive``)
``DATA_ROOT``                        writable data root (default ``/data``)
``VOCAB_PATH``                       query-time synonyms (default ``vocab.yaml`` in the repo root, like ``STATIC_DIR``)
``STATIC_DIR``                       built frontend (default ``web/dist``)
``EMBED_BASE_URL``                   LiteLLM gateway for the vector arm (falls back to ``LITELLM_BASE_URL``; unset is fine while ``hybrid=0``)
``EMBED_API_KEY``                    gateway key (falls back to ``LITELLM_API_KEY``)
``EMBED_MODEL``                      embedding alias (default ``cohere/embed-v5.0``, 2,048-dim)
``EMBED_TIMEOUT_MS``                 query-embed timeout, clamped to [800, 3000] (default 800, measured)
``EMBED_CORPUS_TIMEOUT_MS``          corpus-embed timeout, clamped to [1000, 30000] (default 5000, measured; the corpus pass only)
===================================  ==========================================

The last four are phase 2 and are read by :mod:`stratechery_search.embed`, not by
the server: nothing on the default (``hybrid=0``) path touches the gateway, so an
unset gateway costs the flag rather than the service.
"""

from __future__ import annotations

import os
from pathlib import Path

from .server import Config, serve


def main() -> int:
    config = Config(
        port=int(os.environ.get("PORT", "8080")),
        archive_root=Path(os.environ.get("ARCHIVE_ROOT", "/archive")),
        static_dir=Path(os.environ.get("STATIC_DIR", "web/dist")),
    )
    serve(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
