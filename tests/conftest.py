"""Shared fixtures: the three-message corpus, its index, and a live server.

The corpus itself is built by :mod:`corpus` rather than here, because the
browser suite (``web/e2e``) needs the same archive on disk and two definitions of
"a corpus" would drift apart.
"""

from __future__ import annotations

import threading

import pytest

from corpus import build as build_corpus
from stratechery_search.server import Config, Handler, Server


@pytest.fixture()
def corpus(tmp_path):
    return build_corpus(tmp_path)


@pytest.fixture()
def service(corpus):
    archive, _derived, index_path = corpus
    dist = archive.parent / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Stratechery Search</title></head>"
        '<body><div id="app"></div></body></html>',
        encoding="utf-8",
    )
    # The real bundle ships an icon set from web/public/; the fixture keeps the
    # two text-based ones so the static-type contract can be asserted without
    # committing PNGs to the test tree.
    (dist / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"></svg>',
        encoding="utf-8",
    )
    (dist / "site.webmanifest").write_text(
        '{"name": "Stratechery Search", "start_url": "/"}', encoding="utf-8"
    )
    config = Config(
        port=0,
        index_path=index_path,
        archive_root=archive,
        static_dir=dist,
        vocab_path=None,
    )
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
