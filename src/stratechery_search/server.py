"""The phase-1 HTTP service.

Five read-only JSON endpoints (``/search``, ``/article/{id}``, ``/asset/{file}``,
``/stats``, ``/healthz``) plus ``/livez`` for the container healthcheck, and the
built Svelte bundle from ``web/dist``. No endpoint writes anything: the index is
built by :mod:`stratechery_search.build_index` and mounted read-only in spirit
and in fact (every connection opens with ``mode=ro``).
"""

from __future__ import annotations

import gzip
import json
import mimetypes
import os
import sqlite3
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import api, facets, vector_store

DEFAULT_PORT = 8080
DEFAULT_ARCHIVE_ROOT = Path("/archive")
DEFAULT_DATA_ROOT = Path("/data")
DEFAULT_STATIC_DIR = Path("web/dist")
#: The synonym table is a reviewable artifact, so it lives in the repository
#: rather than in the writable data volume: an edit is then a diff, not a
#: change that exists only on the host that made it. Resolved against the
#: working directory for the same reason ``STATIC_DIR`` is — the process
#: starts in the repo root in dev and in ``/app`` in the image.
DEFAULT_VOCAB_PATH = Path("vocab.yaml")

ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif"}
ASSET_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}

#: Compress responses at or above this size. Below it the gzip header is worth
#: more than the saving, and `Content-Length` is already tiny.
MIN_GZIP_BYTES = 1024
GZIP_LEVEL = 6
#: Types worth compressing. Images are already compressed — re-deflating a PNG
#: costs CPU per request and saves nothing — but SVG is text.
COMPRESSIBLE_TYPES = {
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
    "text/javascript",
}


class Config:
    """Runtime configuration, read from the environment once at start-up."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        index_path: Path | None = None,
        archive_root: Path = DEFAULT_ARCHIVE_ROOT,
        static_dir: Path = DEFAULT_STATIC_DIR,
        vocab_path: Path | None = None,
    ) -> None:
        data_root = Path(os.environ.get("DATA_ROOT", DEFAULT_DATA_ROOT))
        self.port = port
        self.index_path = index_path or Path(
            os.environ.get("INDEX_PATH", data_root / "index" / "search.sqlite")
        )
        self.archive_root = archive_root
        self.static_dir = static_dir
        self.vocab_path = vocab_path or Path(
            os.environ.get("VOCAB_PATH") or DEFAULT_VOCAB_PATH
        )
        # Phase 2. The directory is declared here so a missing store is a clear
        # 500 on `hybrid=1` rather than a path that depends on the working
        # directory. Nothing opens it until a hybrid request arrives.
        self.store_path = Path(
            os.environ.get("LANCEDB_PATH", data_root / "index" / "lancedb")
        )
        # 3-pre. Built per corpus by `build_facets`, so it is not in the image and
        # its absence is normal: no file means no entity facet, not a broken
        # service. Loaded once at start-up (it is ~10k pairs) rather than parsed
        # per request like the vocabulary table.
        self.facets_path = Path(
            os.environ.get("FACETS_PATH", data_root / "index" / "facets.sqlite")
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "stratechery-search/0.1"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        if os.environ.get("HTTP_LOG", "").lower() in {"1", "true"}:
            super().log_message(format, *args)

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def _connect(self) -> sqlite3.Connection | None:
        path = self.config.index_path
        if not path.exists():
            return None
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = None
        return connection

    def _send_json(self, body: dict, status: int = 200) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            payload,
            "application/json; charset=utf-8",
            status=status,
            cache_control="no-store",
        )

    def _send_error_body(self, error: api.ApiError) -> None:
        self._send_json(error.as_body(), error.status)

    def _accepts_gzip(self) -> bool:
        """Does the client offer gzip, and not refuse it with ``q=0``?"""
        for token in self.headers.get("Accept-Encoding", "").split(","):
            name, _, params = token.strip().partition(";")
            if name.strip().lower() != "gzip":
                continue
            quality = params.strip().lower().removeprefix("q=")
            return quality not in {"0", "0.0", "0.00", "0.000"}
        return False

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        *,
        status: int = 200,
        cache_control: str | None = None,
    ) -> None:
        """Write a response body, compressing it when that is worth doing.

        This exists because a keystroke was shipping 73 KB of JSON uncompressed
        over the tailnet to a phone — more than the query itself cost by a factor
        of thirty (measured 2026-11-07, design note §7). The same body is 11 KB
        gzipped, and the JS bundle is 75 KB against 27 KB.
        """
        compressed = None
        if (
            len(data) >= MIN_GZIP_BYTES
            and content_type.split(";")[0] in COMPRESSIBLE_TYPES
            and self._accepts_gzip()
        ):
            compressed = gzip.compress(data, GZIP_LEVEL)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        # Sent whether or not this response was compressed: a cache that stored
        # the identity form must not answer a client that asked for gzip.
        self.send_header("Vary", "Accept-Encoding")
        if compressed is not None:
            self.send_header("Content-Encoding", "gzip")
            data = compressed
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        # HEAD: the headers are the answer, and sending a body would exceed the
        # Content-Length we just declared. `curl -I` used to be a 501.
        if not getattr(self, "_head_only", False):
            self.wfile.write(data)

    # -- routing ----------------------------------------------------------

    def _wants_html(self) -> bool:
        accept = self.headers.get("Accept", "")
        if "text/html" in accept:
            json_pos = accept.find("application/json")
            html_pos = accept.find("text/html")
            if json_pos == -1 or html_pos < json_pos:
                return True
        return False

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query, keep_blank_values=True)

        if path == "/livez":
            # The container healthcheck. Deliberately independent of the index:
            # a missing index is a degraded product, not a dead process.
            self._send_json({"status": "ok"})
            return

        try:
            if path == "/healthz":
                # Loading the table here as well as on /search is what makes
                # "is the file firing?" answerable: the response reports the
                # path it looked at and how many terms it found.
                vocab = api.load_vocab(self.config.vocab_path)
                self._with_index(
                    lambda conn: self._send_json(
                        api.health(
                            conn,
                            self.config.vocab_path,
                            vocab,
                            self.config.facets_path,
                            getattr(self.server, "facets", None),
                        )
                    )
                )
            elif path == "/stats":
                if self._wants_html():
                    self._serve_static("index.html")
                else:
                    self._with_index(lambda conn: self._send_json(api.stats(conn)))
            elif path == "/search":
                params = api.parse_search_params(query)
                vocab = api.load_vocab(self.config.vocab_path)
                self._with_index(
                    lambda conn: self._send_json(
                        api.search(
                            conn,
                            params,
                            vocab,
                            hybrid=getattr(self.server, "hybrid", None),
                            facet_index=getattr(self.server, "facets", None),
                        )
                    )
                )
            elif path.startswith("/article/"):
                if self._wants_html():
                    self._serve_static("index.html")
                else:
                    message_id = self._parse_id(path, "/article/")
                    self._with_index(
                        lambda conn: self._send_json(api.article(conn, message_id))
                    )
            elif path.startswith("/asset/"):
                self._serve_asset(path, "/asset/")
            elif path in {"/", "/stats/", "/search/"}:
                self._serve_static("index.html")
            else:
                self._serve_static_or_404(path)
        except api.ApiError as error:
            self._send_error_body(error)
        except BrokenPipeError:  # client hung up; nothing to report
            return

    def _with_index(self, fn) -> None:
        connection = self._connect()
        if connection is None:
            raise api.ApiError(
                503,
                "index_unbuilt",
                "the index has not been built",
                {"index_path": str(self.config.index_path)},
            )
        try:
            fn(connection)
        except sqlite3.Error as exc:
            raise api.ApiError(
                503,
                "index_degraded",
                "the index could not be queried",
                {"detail": str(exc)},
            ) from exc
        finally:
            connection.close()

    def _parse_id(self, path: str, prefix: str) -> int:
        raw = path[len(prefix) :].strip("/")
        if not raw.isdigit():
            raise api.ApiError(
                400, "bad_request", "message_id must be an integer", {"value": raw}
            )
        return int(raw)

    def _serve_asset(self, path: str, prefix: str) -> None:
        name = path[len(prefix) :].strip("/")
        # Path-escape guard: a single path segment, no traversal, whitelisted
        # extension. The archive's assets/ is the only place images live.
        if not name or name != Path(name).name or "/" in name or "\\" in name:
            raise api.ApiError(400, "bad_request", "bad asset name", {"value": name})
        extension = Path(name).suffix.lower()
        if extension not in ASSET_EXTENSIONS:
            raise api.ApiError(
                404, "not_found", "unsupported asset type", {"value": name}
            )
        asset_path = (self.config.archive_root / "assets" / name).resolve()
        assets_root = (self.config.archive_root / "assets").resolve()
        if assets_root not in asset_path.parents or not asset_path.is_file():
            raise api.ApiError(404, "not_found", "no such asset", {"value": name})

        data = asset_path.read_bytes()
        self._send_bytes(
            data,
            ASSET_CONTENT_TYPES[extension],
            cache_control="public, max-age=31536000, immutable",
        )

    def _serve_static_or_404(self, path: str) -> None:
        candidate = self.config.static_dir / path.lstrip("/")
        if (
            candidate.is_file()
            and self.config.static_dir.resolve() in candidate.resolve().parents
        ):
            self._serve_static(path.lstrip("/"))
            return
        # Unknown non-API path: the app owns its routes through the querystring,
        # so hand back the shell and let the client route.
        self._serve_static("index.html")

    def _serve_static(self, relative: str) -> None:
        target = (self.config.static_dir / relative).resolve()
        root = self.config.static_dir.resolve()
        if (root not in target.parents and target != root) or not target.is_file():
            raise api.ApiError(
                503,
                "ui_unbuilt",
                "the frontend bundle is not present; run `npm run build` in web/",
                {"static_dir": str(self.config.static_dir)},
            )
        data = target.read_bytes()
        content_type = (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        self._send_bytes(data, content_type)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        """The same routing as GET, with the body withheld.

        Monitors, link checkers and `curl -I` all use it; without it the service
        answered 501 to every one of them, which reads as "broken", not as
        "unsupported method".
        """
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    # Everything else is a method nobody may use: this service is read-only.
    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._send_error_body(
            api.ApiError(
                HTTPStatus.METHOD_NOT_ALLOWED.value,
                "method_not_allowed",
                "this service is read-only",
            )
        )

    do_PUT = do_DELETE = do_PATCH = do_POST


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _warm_hybrid(backend, index_path: Path) -> None:
    """Open the store and check parity before the first reader asks for one.

    ~870 ms of a cold process's first hybrid search is LanceDB connect plus the
    29,918-id parity scan (measured 2026-11-07: 1,078 ms cold, 205 ms warm).
    It is paid on a daemon thread so the listener comes up immediately and what
    a reader feels is the warm number. The verdict is logged either way: a store
    that cannot be warmed will fail the same way on its first request, and
    saying so at startup is cheaper than discovering it in a 500.
    """
    print(json.dumps({"hybrid_warmup": backend.warm(index_path)}), flush=True)


def serve(config: Config) -> None:
    server = Server(("0.0.0.0", config.port), Handler)
    server.config = config  # type: ignore[attr-defined]
    # Constructing this imports nothing native and opens nothing; the connect
    # happens on the warm-up thread below, not at import (design §4.6).
    server.hybrid = vector_store.Backend(config.store_path)  # type: ignore[attr-defined]
    # None when the file is absent — see Config.facets_path. Loading it here (not
    # per request) keeps a 10k-pair read off the reader's clock.
    server.facets = facets.open_index(config.facets_path)  # type: ignore[attr-defined]
    threading.Thread(
        target=_warm_hybrid,
        args=(server.hybrid, config.index_path),
        name="hybrid-warmup",
        daemon=True,
    ).start()
    print(
        f"stratechery-search listening on :{config.port} "
        f"(index={config.index_path}, static={config.static_dir})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
