"""The phase-1 HTTP service.

Five read-only JSON endpoints (``/search``, ``/article/{id}``, ``/asset/{file}``,
``/stats``, ``/healthz``) plus ``/livez`` for the container healthcheck, and the
built Svelte bundle from ``web/dist``. No endpoint writes anything: the index is
built by :mod:`stratechery_search.build_index` and mounted read-only in spirit
and in fact (every connection opens with ``mode=ro``).
"""

from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import api

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
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_error_body(self, error: api.ApiError) -> None:
        self._send_json(error.as_body(), error.status)

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
                self._with_index(lambda conn: self._send_json(api.health(conn)))
            elif path == "/stats":
                if self._wants_html():
                    self._serve_static("index.html")
                else:
                    self._with_index(lambda conn: self._send_json(api.stats(conn)))
            elif path == "/search":
                params = api.parse_search_params(query)
                vocab = api.load_vocab(self.config.vocab_path)
                self._with_index(
                    lambda conn: self._send_json(api.search(conn, params, vocab))
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
        self.send_response(200)
        self.send_header("Content-Type", ASSET_CONTENT_TYPES[extension])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

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
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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


def serve(config: Config) -> None:
    server = Server(("0.0.0.0", config.port), Handler)
    server.config = config  # type: ignore[attr-defined]
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
