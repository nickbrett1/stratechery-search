"""Tests for the index build: schema, the FTS rebuild, and the §2.3 gates.

The synthetic corpus is two messages — one article and one transactional mail —
so the two failures this build exists to prevent (an empty external-content
index, and ``snippet()`` dying because ``subject`` is not in ``chunks``) are
asserted directly rather than inferred from corpus-wide counts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from email.message import EmailMessage
from pathlib import Path

import pytest

from stratechery_search import build_index
from stratechery_search.build_index import build
from stratechery_search.chunk import chunk_text
from stratechery_search.flatten import main as flatten_main

SENT_AT = "2026-07-15T10:07:57Z"
BODY = (
    "The aggregation theory says integration is a choice about where the "
    "bottleneck sits.\n\n" * 12
)
ACCESS_BODY = "Hi Nick Brett,\n\nHere is your code for Stratechery:\n\n749209\n"


def _write_archive(root: Path) -> list[dict]:
    raw = root / "raw"
    raw.mkdir(parents=True)
    rows = []
    for message_id, subject, body, images in (
        (
            152,
            "IBM Misses (Stratechery Update 7-15-2026)",
            BODY,
            [
                {
                    "url_path": "https://example.test/header.png",
                    "asset": "assets/aaaa.png",
                    "sha256": "a" * 64,
                    "bytes": 100,
                }
            ],
        ),
        (
            30564,
            "749209 is your Stratechery access code",
            ACCESS_BODY,
            [
                {
                    "url_path": "https://example.test/none.png",
                    "asset": None,
                    "sha256": None,
                    "bytes": None,
                }
            ],
        ),
    ):
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = "Stratechery <email@stratechery.com>"
        message["Date"] = SENT_AT
        message.set_content(body)
        data = message.as_bytes()
        (raw / f"{message_id}.eml").write_bytes(data)
        rows.append(
            {
                "message_id": message_id,
                "sent_at": SENT_AT,
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
    return rows


@pytest.fixture()
def built(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    _write_archive(archive)
    # The flattener's corpus-wide gates fail on a 2-message corpus by design;
    # the files are still written, which is all this build consumes.
    assert flatten_main(["--archive", str(archive), "--out", str(derived)]) == 1
    for path in derived.glob("*.meta.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        # Hold the normalised body steady so the chunk arithmetic is predictable.
        if meta["message_id"] == 152:
            (derived / "152.txt").write_text(BODY + "\n", encoding="utf-8")
    metas = build_index.load_metas(derived)
    expected_chunks = sum(
        len(
            chunk_text((derived / f"{m['message_id']}.txt").read_text(encoding="utf-8"))
        )
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
            "null_asset_ids": (30564,),
        },
    )
    out = tmp_path / "index" / "search.sqlite"
    report = build(derived, archive, out)
    return out, report


def test_schema_is_populated(built):
    out, report = built
    assert report["installed"] is True
    conn = sqlite3.connect(out)
    try:
        assert conn.execute("SELECT count(*) FROM articles").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM corpus_stats").fetchone()[0] == 1
        assert (
            conn.execute("SELECT searchable_articles FROM corpus_stats").fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT transactional FROM corpus_stats").fetchone()[0] == 1
    finally:
        conn.close()


def test_fts_is_rebuilt_and_resolves_content(built):
    out, _ = built
    conn = sqlite3.connect(out)
    try:
        chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        assert conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == chunks
        # The control query: an empty index passes parity (0 == 0) and fails this.
        hits = conn.execute(
            "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'aggregation'"
        ).fetchone()[0]
        assert hits >= 1
        # The defect this gate exists for: snippet() needs `subject` in `chunks`.
        snippet = conn.execute(
            "SELECT snippet(chunks_fts, 0, '<mark>', '</mark>', '…', 8)"
            " FROM chunks_fts WHERE chunks_fts MATCH 'aggregation' LIMIT 1"
        ).fetchone()[0]
        assert "<mark>aggregation</mark>" in snippet
        # bm25 with a subject column weight also resolves the content table.
        ranked = conn.execute(
            "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'ibm'"
        ).fetchone()[0]
        assert ranked >= 1
    finally:
        conn.close()


def test_url_only_image_is_null_not_missing(built):
    out, _ = built
    conn = sqlite3.connect(out)
    try:
        rows = conn.execute(
            "SELECT message_id, asset, sha256 FROM article_images ORDER BY message_id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[1] == (30564, None, None)
    finally:
        conn.close()


def test_every_gate_passes_on_the_synthetic_corpus(built):
    _, report = built
    failed = [gate for gate in report["gates"] if not gate["pass"]]
    assert failed == [], failed
    codes = {gate["gate"] for gate in report["gates"]}
    assert {
        "fts_parity_and_control",
        "fts_content_resolution",
        "subject_denormalised",
        "provenance",
        "build_idempotent",
    } <= codes


def test_chunk_text_reassembles_into_the_derived_body(built):
    out, _ = built
    conn = sqlite3.connect(out)
    try:
        rows = conn.execute(
            "SELECT text FROM chunks WHERE message_id = 152 ORDER BY ordinal"
        ).fetchall()
        joined = "".join(row[0] for row in rows)
        assert joined.startswith("The aggregation theory")
        assert joined.rstrip() == BODY.rstrip()
    finally:
        conn.close()


def test_failed_gate_does_not_install_the_index(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    _write_archive(archive)
    flatten_main(["--archive", str(archive), "--out", str(derived)])
    out = tmp_path / "index" / "search.sqlite"
    report = build(derived, archive, out, verify_idempotent=False)
    assert report["installed"] is False
    assert not out.exists()


BY_YEAR = {
    2016: 1,
    2017: 51,
    2018: 156,
    2019: 184,
    2020: 193,
    2021: 201,
    2022: 201,
    2023: 180,
    2024: 186,
    2025: 181,
    2026: 157,
}


def test_under_represented_year_note_ignores_partial_boundary_years():
    from stratechery_search.notes import stats_notes

    # daily_from_year=2016: this is the baseline arithmetic on its own, where
    # every interior year is fair game. 2016 and 2026 are excluded as partial by
    # construction, so a one-message 2016 cannot mask the gap in 2017.
    notes = stats_notes(BY_YEAR, daily_from_year=2016)
    assert [note["year"] for note in notes] == [2017]
    assert notes[0]["articles"] == 51
    assert notes[0]["neighbouring_mean"] == 185


def test_a_short_year_before_the_daily_update_arrived_is_left_to_the_caption():
    from stratechery_search.notes import stats_notes

    # 2017 is short because the daily update was not subscribed to yet, and the
    # corpus page's chart caption says so once. A note repeating it was both
    # redundant and wrong about the cause ("a sampling artefact").
    assert stats_notes(BY_YEAR, daily_from_year=2018) == []


def test_a_short_year_after_the_daily_update_arrived_is_still_noted():
    from stratechery_search.notes import stats_notes

    thin = {**BY_YEAR, 2020: 80}
    notes = stats_notes(thin, daily_from_year=2018)
    assert [note["year"] for note in notes] == [2020]
    assert notes[0]["code"] == "under_represented_year_2020"
    # The note knows a count, not a cause, and does not invent one.
    assert "archive" in notes[0]["message"]
    assert "sampling" not in notes[0]["message"]
