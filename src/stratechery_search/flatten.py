"""Flatten the read-only email archive into a derived text layer.

Usage::

    python -m stratechery_search.flatten \
        --archive /srv/datasets/stratechery-archive \
        --out data/derived

The archive is input only. Everything this module writes goes under ``--out``:
one ``<message_id>.txt`` and ``<message_id>.meta.json`` per message, plus
``versions.json`` and a ``flatten-report.json`` carrying the evidence for the
flatten gates in the phase-1 scope contract.
"""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

from .chunk import CHUNKER_VERSION
from .normalise import (
    FLATTENER_VERSION,
    NORMALIZER_VERSION,
    NormaliseError,
    normalise_body,
)

#: Phase-1 scope contract §1 gate values. ``transactional`` is the note's
#: value; the archive actually contains more (see the report), so a mismatch
#: here is evidence for the owner, not a flattener bug.
EXPECTED = {
    "coverage": 1691,
    "transactional": 4,
    "images_distinct": 2415,
    "images_null": 2,
    "min_chars": 800,
    "total_chars_min": 30_000_000,
    "total_chars_max": 55_000_000,
    "est_tokens_min": 8_000_000,
    "est_tokens_max": 14_000_000,
}

#: Transactional mail is classified on subject, never on length (design §1).
#: The survey's "3 Stripe receipts, 1 access code" undercounts the corpus;
#: these patterns are what the archive actually contains.
TRANSACTIONAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("access_code", r"\b\d{6} is your (?:Stratechery|Dithering) access code\b"),
    ("receipt", r"^Your receipt from \S+"),
    ("renewal", r"^Your Stratechery subscription will renew soon"),
)


def classify_transactional(subject: str) -> str | None:
    import re

    for name, pattern in TRANSACTIONAL_PATTERNS:
        if re.search(pattern, subject, re.IGNORECASE):
            return name
    return None


def classify_kind(subject: str) -> str:
    """Map the subject's trailing ``(Stratechery …)`` tag to the §2.1 enum."""
    import re

    match = re.search(r"\(([^()]*)\)\s*$", subject)
    if not match:
        return "other"
    tag = match.group(1)
    if re.search(r"\bInterview\b", tag, re.IGNORECASE):
        return "interview"
    if re.search(r"\b(?:Weekly|Article|Articles|This Week)\b", tag, re.IGNORECASE):
        return "weekly"
    if re.search(r"\bDaily\s+Update\b", tag, re.IGNORECASE):
        return "daily"
    if re.search(r"\bUpdate\b", tag, re.IGNORECASE):
        return "update"
    return "other"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_index(archive: Path) -> list[dict]:
    rows = []
    with (archive / "index.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def plain_part(message: Message) -> tuple[str, str | None, bool]:
    """Return (text, charset, decode_ok) for the longest text/plain part.

    Choosing the longest guards against a short ``text/plain`` attachment while
    still preferring ``text/plain`` over HTML, per design §1.
    """
    best: tuple[bytes, str | None] | None = None
    for part in message.walk():
        if part.get_content_type() != "text/plain" or part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        if best is None or len(payload) > len(best[0]):
            best = (payload, part.get_content_charset())

    if best is None:
        return "", None, False

    payload, charset = best
    try:
        text = payload.decode(charset or "utf-8")
        return text, charset, "\ufffd" not in text
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace"), charset, False


def has_html_tables(message: Message) -> bool:
    for part in message.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            if b"<table" in payload.lower():
                return True
    return False


def flatten_one(row: dict, archive: Path) -> tuple[str, dict]:
    message_id = int(row["message_id"])
    eml_path = archive / "raw" / f"{message_id}.eml"
    raw_bytes = eml_path.read_bytes()
    message = email.message_from_bytes(raw_bytes)

    text, charset, decode_ok = plain_part(message)
    if not text:
        raise NormaliseError("no text/plain part")

    body = normalise_body(text)
    body_bytes = body.encode("utf-8")

    category = classify_transactional(row["subject"])
    images = row.get("images") or []

    meta = {
        "message_id": message_id,
        "sent_at": row["sent_at"],
        "subject": row["subject"],
        "from": row["from"],
        "sender": row["from"],
        "kind": classify_kind(row["subject"]),
        "chars": len(body),
        "words": len(body.split()),
        "est_tokens": len(body) // 4,
        "est_tokens_method": "chars/4",
        "n_images": len(images),
        "has_tables": has_html_tables(message),
        "is_transactional": category is not None,
        "transactional_category": category,
        "archive_sha256": row["sha256"],
        "archive_bytes": row["bytes"],
        "text_sha256": sha256_bytes(body_bytes),
        "content_type": message.get_content_type(),
        "charset": charset,
        "decode_ok": decode_ok,
        "flattener_version": FLATTENER_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
    }
    return body, meta


def _write_atomic(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _gate(code: str, expected, observed, ok: bool, note: str = "") -> dict:
    return {
        "gate": code,
        "expected": expected,
        "observed": observed,
        "pass": bool(ok),
        "note": note,
    }


def build_report(
    rows: list[dict],
    metas: list[dict],
    failures: list[dict],
    archive: Path,
    index_rows: set[int],
    derived_ids: set[int],
) -> dict:
    by_year: dict[str, list[dict]] = defaultdict(list)
    for meta in metas:
        by_year[meta["sent_at"][:4]].append(meta)

    per_year = {}
    for year, items in sorted(by_year.items()):
        chars = [item["chars"] for item in items]
        per_year[year] = {
            "articles": len(items),
            "min_chars": min(chars),
            "median_chars": int(statistics.median(chars)),
            "max_chars": max(chars),
            "chars": sum(chars),
            "short_bodies": sum(1 for value in chars if value < EXPECTED["min_chars"]),
        }

    all_chars = [meta["chars"] for meta in metas]
    total_chars = sum(all_chars)
    total_tokens = sum(meta["est_tokens"] for meta in metas)
    transactional = [m for m in metas if m["is_transactional"]]
    categories = Counter(m["transactional_category"] for m in transactional)
    searchable = [m for m in metas if not m["is_transactional"]]
    short_searchable = [m for m in searchable if m["chars"] < EXPECTED["min_chars"]]

    images = [img for row in rows for img in (row.get("images") or [])]
    distinct_assets = {img["asset"] for img in images if img.get("asset")}
    null_assets = [img for img in images if not img.get("asset")]

    era_short: dict[str, dict[str, int]] = defaultdict(lambda: {"articles": 0, "short": 0})
    for meta in metas:
        if meta["is_transactional"]:
            continue
        era = meta["content_type"]
        era_short[era]["articles"] += 1
        if meta["chars"] < EXPECTED["min_chars"]:
            era_short[era]["short"] += 1
    era_balanced = all(
        counts["articles"] == 0 or counts["short"] / counts["articles"] <= 0.5
        for counts in era_short.values()
    )

    short_bodies = [
        {
            "message_id": m["message_id"],
            "chars": m["chars"],
            "sent_at": m["sent_at"],
            "subject": m["subject"],
        }
        for m in sorted(short_searchable, key=lambda m: m["chars"])
    ]

    gates = [
        _gate(
            "coverage",
            EXPECTED["coverage"],
            len(derived_ids),
            len(derived_ids) == EXPECTED["coverage"] and derived_ids == index_rows,
            "every index.jsonl message_id has a derived .txt",
        ),
        _gate(
            "url_residue",
            0,
            sum("http://" in m["_text"] or "https://" in m["_text"] for m in metas),
            all(
                "http://" not in m["_text"] and "https://" not in m["_text"]
                for m in metas
            ),
        ),
        _gate(
            "bold_residue",
            0,
            sum("**" in m["_text"] for m in metas),
            all("**" not in m["_text"] for m in metas),
        ),
        _gate(
            "divider_residue",
            0,
            sum(
                1
                for m in metas
                for line in m["_text"].splitlines()
                if line.strip() and set(line.strip()) <= set("-*_=") and len(line.strip()) >= 3
            ),
            not any(
                line.strip() and set(line.strip()) <= set("-*_=") and len(line.strip()) >= 3
                for m in metas
                for line in m["_text"].splitlines()
            ),
        ),
        _gate(
            "jwt_residue",
            0,
            sum("access_token=" in m["_text"] for m in metas),
            all("access_token=" not in m["_text"] for m in metas),
            "signed tokens are a leak, not a cosmetic residue",
        ),
        _gate(
            "length_floor",
            f">= {EXPECTED['min_chars']} chars for non-transactional articles",
            sorted(m["message_id"] for m in short_searchable),
            not short_searchable,
            "exceptions are listed as observed",
        ),
        _gate(
            "length_sanity",
            f"{EXPECTED['total_chars_min']}–{EXPECTED['total_chars_max']} chars, "
            f"{EXPECTED['est_tokens_min']}–{EXPECTED['est_tokens_max']} est_tokens",
            {"chars": total_chars, "est_tokens": total_tokens},
            EXPECTED["total_chars_min"] <= total_chars <= EXPECTED["total_chars_max"]
            and EXPECTED["est_tokens_min"] <= total_tokens <= EXPECTED["est_tokens_max"],
        ),
        _gate(
            "era_balance",
            "no era has >50% short bodies",
            {era: dict(counts) for era, counts in sorted(era_short.items())},
            era_balanced,
        ),
        _gate(
            "transactional",
            EXPECTED["transactional"],
            {"count": len(transactional), "by_category": dict(sorted(categories.items()))},
            len(transactional) == EXPECTED["transactional"],
            "the archive contains more transactional mail than design §1 recorded",
        ),
        _gate(
            "images",
            {
                "distinct_assets": EXPECTED["images_distinct"],
                "null_assets": EXPECTED["images_null"],
                "rows": sum(meta["n_images"] for meta in metas),
            },
            {
                "distinct_assets": len(distinct_assets),
                "null_assets": len(null_assets),
                "rows": len(images),
            },
            len(distinct_assets) == EXPECTED["images_distinct"]
            and len(null_assets) == EXPECTED["images_null"],
        ),
        _gate(
            "charset",
            0,
            sum(not meta["decode_ok"] for meta in metas),
            all(meta["decode_ok"] for meta in metas),
        ),
    ]

    return {
        "archive": str(archive),
        "flattener_version": FLATTENER_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {
            "index_rows": len(rows),
            "derived_txt": len(derived_ids),
            "failures": len(failures),
            "transactional": len(transactional),
            "searchable": len(searchable),
        },
        "chars": {
            "min": min(all_chars) if all_chars else 0,
            "median": int(statistics.median(all_chars)) if all_chars else 0,
            "max": max(all_chars) if all_chars else 0,
            "total": total_chars,
        },
        "est_tokens_total": total_tokens,
        "per_year": per_year,
        "short_bodies": short_bodies,
        "kinds": dict(sorted(Counter(m["kind"] for m in metas).items())),
        "senders": dict(sorted(Counter(m["sender"] for m in metas).items())),
        "images": {
            "rows": len(images),
            "distinct_assets": len(distinct_assets),
            "null_assets": len(null_assets),
        },
        "failures": failures,
        "gates": gates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--message-id", type=int, action="append", default=None)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="report path (default: <out>/flatten-report.json)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="normalise and report without writing the derived layer",
    )
    args = parser.parse_args(argv)

    all_rows = read_index(args.archive)
    all_ids = {int(row["message_id"]) for row in all_rows}
    rows = all_rows
    if args.message_id:
        wanted = set(args.message_id)
        rows = [row for row in rows if int(row["message_id"]) in wanted]
    if args.limit:
        rows = rows[: args.limit]

    args.out.mkdir(parents=True, exist_ok=True)
    metas: list[dict] = []
    failures: list[dict] = []
    derived_ids: set[int] = set()
    texts: dict[int, str] = {}

    for row in sorted(rows, key=lambda r: int(r["message_id"])):
        message_id = int(row["message_id"])
        try:
            body, meta = flatten_one(row, args.archive)
        except (OSError, NormaliseError, KeyError) as exc:
            failures.append({"message_id": message_id, "reason": str(exc)})
            continue

        texts[message_id] = body
        metas.append(meta)
        derived_ids.add(message_id)
        if not args.no_write:
            _write_atomic(args.out / f"{message_id}.txt", body)
            _write_atomic(
                args.out / f"{message_id}.meta.json",
                json.dumps(meta, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
            )

    # Reports read the written text; attaching it here keeps build_report pure.
    for meta in metas:
        meta["_text"] = texts[meta["message_id"]]

    report = build_report(
        rows,
        metas,
        failures,
        args.archive,
        all_ids,
        derived_ids,
    )

    if not args.no_write:
        _write_atomic(
            args.out / "versions.json",
            json.dumps(
                {
                    "flattener_version": FLATTENER_VERSION,
                    "normalizer_version": NORMALIZER_VERSION,
                    "chunker_version": CHUNKER_VERSION,
                },
                sort_keys=True,
                indent=1,
            )
            + "\n",
        )

    report_path = args.report or args.out / "flatten-report.json"
    report_json = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if not args.no_write:
        _write_atomic(report_path, report_json)
    else:
        sys.stdout.write(report_json)

    failed = [gate for gate in report["gates"] if not gate["pass"]]
    for gate in failed:
        print(f"GATE FAILED {gate['gate']}: {gate['observed']}", file=sys.stderr)
    print(
        f"{len(derived_ids)}/{len(rows)} flattened, "
        f"{len(failures)} failures, {len(failed)} gate(s) failed",
        file=sys.stderr,
    )
    return 1 if failed or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
