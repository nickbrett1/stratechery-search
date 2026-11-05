"""Shared fixtures: a three-message corpus, its index, and a live server.

The corpus is small on purpose. The point of these fixtures is not coverage of
1,691 articles but of the *contract*: every field §3 promises, the two nullable
image references, and the failure modes (no index, bad params, read-only).
"""

from __future__ import annotations

import hashlib
import json
import threading
from email.message import EmailMessage
from pathlib import Path

import pytest

from stratechery_search import build_index
from stratechery_search.chunk import chunk_text
from stratechery_search.flatten import main as flatten_main
from stratechery_search.server import Config, Handler, Server

ARTICLE_BODY = (
    "The aggregation theory says integration is a choice about where the "
    "bottleneck sits in a value chain.\n\n"
) * 14
SECOND_BODY = (
    "Apple's services revenue is a function of the installed base, and the "
    "installed base is a function of the iPhone.\n\n"
) * 10
ACCESS_BODY = "Hi Nick Brett,\n\nHere is your code for Stratechery:\n\n749209\n"

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 16


def _write_archive(root: Path) -> None:
    raw = root / "raw"
    raw.mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "assets" / "aaaa0000.png").write_bytes(PNG_BYTES)
    rows = []
    for message_id, subject, sent_at, body, images in (
        (
            152,
            "IBM Misses (Stratechery Update 7-15-2026)",
            "2026-07-15T10:07:57Z",
            ARTICLE_BODY,
            [
                {
                    "url_path": "https://example.test/header.png",
                    "asset": "assets/aaaa0000.png",
                    "sha256": "a" * 64,
                    "bytes": len(PNG_BYTES),
                }
            ],
        ),
        (
            85344,
            "More on Amazon (Stratechery Update 11-14-2018)",
            "2018-11-14T11:00:00Z",
            SECOND_BODY,
            [
                {
                    "url_path": "https://example.test/none.png",
                    "asset": None,
                    "sha256": None,
                    "bytes": None,
                }
            ],
        ),
        (
            30564,
            "749209 is your Stratechery access code",
            "2024-07-08T18:28:28Z",
            ACCESS_BODY,
            [],
        ),
    ):
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = "Stratechery <email@stratechery.com>"
        message["Date"] = sent_at
        message.set_content(body)
        data = message.as_bytes()
        (raw / f"{message_id}.eml").write_bytes(data)
        rows.append(
            {
                "message_id": message_id,
                "sent_at": sent_at,
                "subject": subject,
                "from": "email@stratechery.com",
                "sender": "email@stratechery.com",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "images": images,
            }
        )
    (root / "index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def build_corpus(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """Flatten, index, and return ``(archive, derived, index_path)``."""
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    _write_archive(archive)
    # Corpus-wide gates fail on three messages; the files are still written.
    flatten_main(["--archive", str(archive), "--out", str(derived)])
    for message_id, body in ((152, ARTICLE_BODY), (85344, SECOND_BODY)):
        (derived / f"{message_id}.txt").write_text(body + "\n", encoding="utf-8")
    metas = build_index.load_metas(derived)
    expected_chunks = sum(
        len(chunk_text((derived / f"{m['message_id']}.txt").read_text(encoding="utf-8")))
        for m in metas
    )
    monkeypatch.setattr(
        build_index,
        "EXPECTED",
        {
            **build_index.EXPECTED,
            "articles": len(metas),
            "searchable_articles": len(metas) - 1,
            "chunks_min": expected_chunks,
            "chunks_max": expected_chunks,
            "images_distinct": 1,
            "images_null": 1,
            "null_asset_ids": (85344,),
        },
    )
    index_path = tmp_path / "index" / "search.sqlite"
    report = build_index.build(derived, archive, index_path, verify_idempotent=False)
    assert report["installed"] is True, report["gates"]
    return archive, derived, index_path


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    return build_corpus(tmp_path, monkeypatch)


@pytest.fixture()
def service(corpus):
    archive, _derived, index_path = corpus
    config = Config(
        port=0,
        index_path=index_path,
        archive_root=archive,
        static_dir=archive.parent / "dist",
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
