"""Flattener integration over a synthetic archive."""

import hashlib
import json
from email.message import EmailMessage

from stratechery_search.flatten import (
    classify_kind,
    classify_transactional,
    main,
)

BODY = """The Daily Stratechery email, including exclusive content for members of Stratechery.com
View in browser (https://example.com/a)

** Synthetic Update Title
------------------------------------------------------------
Wednesday, November 14, 2018

Good morning,

This is **synthetic** prose about aggregation (https://example.com/source) .

Thanks for being a supporter, and have a great day!

Copyright © 2018 Example LLC (#) , All rights reserved.
You are receiving this email because you subscribed to Example.com
Our mailing address is:
Example LLC
"""


def make_archive(tmp_path):
    archive = tmp_path / "archive"
    (archive / "raw").mkdir(parents=True)

    message = EmailMessage()
    message["From"] = "membership@example.com"
    message["Subject"] = "Synthetic Update Title (Stratechery Daily Update 11-14-2018)"
    message["Date"] = "Wed, 14 Nov 2018 11:53:35 +0000"
    message.set_content(BODY)

    raw = archive / "raw" / "107.eml"
    raw.write_bytes(message.as_bytes())

    row = {
        "message_id": 107,
        "rfc822_message_id": "<x@example.com>",
        "sent_at": "2018-11-14T11:53:35Z",
        "from": "membership@example.com",
        "subject": "Synthetic Update Title (Stratechery Daily Update 11-14-2018)",
        "bytes": len(message.as_bytes()),
        "sha256": hashlib.sha256(message.as_bytes()).hexdigest(),
        "images": [
            {
                "url_path": "https://example.com/a.png",
                "asset": "assets/aa.png",
                "sha256": "aa",
                "bytes": 10,
            },
            {"url_path": "https://example.com/b.jpg"},
        ],
    }
    (archive / "index.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return archive


def test_flatten_writes_text_meta_and_report(tmp_path):
    archive = make_archive(tmp_path)
    out = tmp_path / "derived"

    assert (
        main(["--archive", str(archive), "--out", str(out)]) == 1
    )  # gates fail on 1 row

    text = (out / "107.txt").read_text(encoding="utf-8")
    assert "synthetic" in text
    assert "https://" not in text
    assert "Copyright" not in text

    meta = json.loads((out / "107.meta.json").read_text(encoding="utf-8"))
    assert meta["message_id"] == 107
    assert meta["sender"] == "membership@example.com"
    assert meta["kind"] == "daily"
    assert meta["chars"] == len(text)
    assert meta["est_tokens"] == len(text) // 4
    assert meta["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert meta["is_transactional"] is False
    assert meta["n_images"] == 2

    versions = json.loads((out / "versions.json").read_text(encoding="utf-8"))
    assert set(versions) == {
        "flattener_version",
        "normalizer_version",
        "chunker_version",
    }

    report = json.loads((out / "flatten-report.json").read_text(encoding="utf-8"))
    assert report["counts"]["derived_txt"] == 1
    assert report["images"] == {"rows": 2, "distinct_assets": 1, "null_assets": 1}
    gates = {gate["gate"]: gate for gate in report["gates"]}
    for code in ("url_residue", "bold_residue", "divider_residue", "jwt_residue"):
        assert gates[code]["pass"], code
    # 2026-11-10: this used to assert `pass is False` because the corpus had one
    # article and the gate expected 1,691. That is the defect, not the contract:
    # coverage asks "does every archived message have derived text", not "is this
    # the corpus as it was in October". One archived article, one derived file.
    assert gates["coverage"]["pass"] is True
    assert gates["coverage"]["observed"] == {"derived": 1, "archive": 1}


def test_a_missing_derived_file_fails_coverage(tmp_path):
    """The gate still catches what it was written for."""
    archive = make_archive(tmp_path)
    out = tmp_path / "derived"
    main(["--archive", str(archive), "--out", str(out)])
    # A second message in the archive with no derived text beside it: the exact
    # shortfall the gate exists for, now that growth is not a shortfall.
    index = archive / "index.jsonl"
    rows = index.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["message_id"] = 108
    index.write_text(
        "\n".join(rows + [json.dumps(row)]) + "\n", encoding="utf-8"
    )
    main(["--archive", str(archive), "--out", str(out)])
    report = json.loads((out / "flatten-report.json").read_text(encoding="utf-8"))
    gates = {gate["gate"]: gate for gate in report["gates"]}
    assert gates["coverage"]["pass"] is False
    assert gates["coverage"]["observed"] == {"derived": 1, "archive": 2}


def test_flatten_is_byte_identical_on_re_run(tmp_path):
    archive = make_archive(tmp_path)
    out = tmp_path / "derived"
    main(["--archive", str(archive), "--out", str(out)])
    first = (out / "107.txt").read_bytes()
    main(["--archive", str(archive), "--out", str(out)])
    assert (out / "107.txt").read_bytes() == first


def test_read_index_and_classifiers():
    assert (
        classify_transactional("207425 is your Stratechery access code")
        == "access_code"
    )
    assert classify_transactional("Your receipt from Stratechery") == "receipt"
    assert (
        classify_transactional("Your Stratechery subscription will renew soon.")
        == "renewal"
    )
    assert classify_transactional("An Interview With Someone") is None

    assert classify_kind("X (Stratechery Daily Update 1-2-2020)") == "daily"
    assert classify_kind("X (Stratechery Update 1-2-2020)") == "update"
    assert classify_kind("X (Stratechery Weekly Article 1-2-2020)") == "weekly"
    assert classify_kind("X (Stratechery Article 1-2-2020)") == "weekly"
    assert classify_kind("X (Stratechery Interview 1-2-2020)") == "interview"
    assert classify_kind("X (This Week in Stratechery)") == "weekly"
    assert classify_kind("No tag at all") == "other"


def test_report_lists_a_failure_for_a_missing_eml(tmp_path):
    archive = make_archive(tmp_path)
    (archive / "raw" / "107.eml").unlink()
    out = tmp_path / "derived"
    assert main(["--archive", str(archive), "--out", str(out)]) == 1
    report = json.loads((out / "flatten-report.json").read_text(encoding="utf-8"))
    assert report["failures"][0]["message_id"] == 107
