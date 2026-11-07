"""The three-message corpus, built on disk for whoever needs it.

No pytest in here on purpose. The contract tests and the browser tests have to
describe the *same* archive — two definitions of "a corpus" would drift until
Playwright was exercising something the contract suite no longer recognised — so
the builder lives in one importable module and both callers use it:

- ``tests/conftest.py`` builds it into a tmp_path per test;
- ``scripts/e2e-corpus.py`` builds it into a directory the dev server can serve.

The corpus is small on purpose. The point is not coverage of 1,691 articles but
of the *contract*: every field §3 promises, both nullable image references, and
the failure modes (no index, bad params, read-only). Three messages, of which one
is transaction mail — so the "searchable" count is 2 and the browse tests have an
article that must never appear.
"""

from __future__ import annotations

import hashlib
import json
from email.message import EmailMessage
from pathlib import Path

from stratechery_search import build_index
from stratechery_search.chunk import chunk_text
from stratechery_search.flatten import main as flatten_main

# Long enough to be chunked into three (TARGET_CHARS is 1,500), which is what
# makes the grouped-search tests meaningful: the same article matches in more
# than one passage.
ARTICLE_BODY = (
    "The aggregation theory says integration is a choice about where the "
    "bottleneck sits in a value chain.\n\n"
) * 30
SECOND_BODY = (
    "Apple's services revenue is a function of the installed base, and the "
    "installed base is a function of the iPhone.\n\n"
) * 10
ACCESS_BODY = "Hi Nick Brett,\n\nHere is your code for Stratechery:\n\n749209\n"

PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 16


def write_archive(root: Path) -> None:
    """Write the three ``.eml`` messages and their ``index.jsonl``."""
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


def build(
    root: Path, *, bodies: dict[int, str] | None = None
) -> tuple[Path, Path, Path]:
    """Flatten, index, and return ``(archive, derived, index_path)``.

    The indexer's corpus-wide gates are written for 1,691 articles, so they are
    re-pointed at this corpus for the duration of the build and restored
    afterwards — the tests assert the audit, not the numbers.

    ``bodies`` overrides the text of one or more articles *after* flattening and
    before indexing, which is how a test can build a corpus whose bodies and
    subjects disagree. It changes the corpus, so a test that passes it must not
    also assert the default article/passage counts.
    """
    archive = root / "archive"
    derived = root / "derived"
    write_archive(archive)
    # Corpus-wide gates fail on three messages; the files are still written.
    flatten_main(["--archive", str(archive), "--out", str(derived)])
    texts = {152: ARTICLE_BODY, 85344: SECOND_BODY, **(bodies or {})}
    for message_id, body in texts.items():
        path = derived / f"{message_id}.txt"
        if bodies and message_id in bodies:
            # An override must not move the article's length: `articles.chars`
            # comes from the flatten manifest and the coverage gate compares the
            # last chunk's end offset against it. Trimming and padding here keeps
            # that gate meaningful rather than switching it off for the test's
            # benefit — a body that is free to grow would make the gate a lie.
            target = len(path.read_text(encoding="utf-8").rstrip("\n"))
            body = body[:target].ljust(target)
        path.write_text(body + "\n", encoding="utf-8")
    metas = build_index.load_metas(derived)
    expected_chunks = sum(
        len(
            chunk_text((derived / f"{m['message_id']}.txt").read_text(encoding="utf-8"))
        )
        for m in metas
    )
    index_path = root / "index" / "search.sqlite"
    saved = build_index.EXPECTED
    build_index.EXPECTED = {
        **saved,
        "articles": len(metas),
        "searchable_articles": len(metas) - 1,
        "chunks_min": expected_chunks,
        "chunks_max": expected_chunks,
        "images_distinct": 1,
        "images_null": 1,
        "null_asset_ids": (85344,),
    }
    try:
        report = build_index.build(
            derived, archive, index_path, verify_idempotent=False
        )
    finally:
        build_index.EXPECTED = saved
    if report["installed"] is not True:
        raise RuntimeError(f"corpus gates failed: {report['gates']}")
    return archive, derived, index_path
