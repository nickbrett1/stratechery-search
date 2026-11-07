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
===================================  ==========================================
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
