"""Normalisation rules, exercised on synthetic bodies.

No real archive content is committed: each fixture reproduces the structural
shape the normaliser keys on (Mailchimp preamble, Passport lede, pseudo-table
padding, footers), not an actual article.
"""

import pytest

from stratechery_search.normalise import NormaliseError, normalise_body


def test_mailchimp_preamble_and_footer_are_removed():
    raw = """
The Daily Stratechery email, including exclusive content for members of Stratechery.com
‌ ‌ ‌ ‌
View in browser (https://example.com/article?utm_source=x)

https://example.com/

** A Synthetic Title
------------------------------------------------------------
Wednesday, November 14, 2018

Good morning,

The body starts here and talks about **aggregation** in prose.

Discuss on the Stratechery Member Forum (https://forum.example.com/t/thing)

Copyright © 2018 Example LLC (#) , All rights reserved.
 You are receiving this email because you subscribed to Example.com
To unsubscribe from email, click here (https://example.com/unsub) .
Our mailing address is:
Example LLC
USA
 (#)
"""
    body = normalise_body(raw)
    assert body.startswith("A Synthetic Title")
    assert "Wednesday, November 14, 2018" in body
    assert "aggregation" in body
    assert "View in browser" not in body
    assert "Copyright" not in body
    assert "Our mailing address" not in body
    assert "**" not in body
    for residue in ("http://", "https://", "(#)"):
        assert residue not in body


def test_passport_lede_is_kept_and_wrapper_removed():
    # Built at runtime so the secret scanner does not see a credential-shaped
    # literal in the source; it is a synthetic token, not a real one.
    token = ".".join(["eyJhbGciOiJSUzI1NiJ9", "eyJhIjoxfQ", "c2lnbmF0dXJl"])
    raw = f"""
This is the lede that summarises the synthetic article.

View in browser ( https://example.com/article?access_token={token} )

( https://example.com )

***************************************************************************************************
A Synthetic Title
***************************************************************************************************

( https://example.com/article )

Good morning,

Body text.

This Update is also available as a podcast. To receive it in your podcast player, visit Example.

Subscription Information
Member: Test
You are receiving this email because you are subscribed to Example ( https://example.com ).
© 2026 Example LLC ( https://www.example.com ) , Somewhere
"""
    body = normalise_body(raw)
    assert body.startswith("This is the lede")
    assert "A Synthetic Title" in body
    assert "Body text." in body
    assert "View in browser" not in body
    assert "Subscription Information" not in body
    assert "eyJhbGci" not in body
    assert "access_token" not in body


def test_ascii_table_padding_is_collapsed_to_prose():
    raw = "\n".join(
        [
            "| | | | | " + "************************************************************************************************" + " |",
            "| | | | | A Synthetic Title" + " " * 200 + "|",
            "| | | | | " + "********************************" + " |",
            "| | | | | Tuesday, December 21, 2021" + " " * 150 + "|",
            "| | | | | " + "A paragraph that was padded out by the HTML-to-text renderer." + " " * 400 + "|",
            "| | | | | " + "+" + "-" * 1000 + "+",
            "| | | | | " + "Another paragraph." + " " * 400 + "|",
        ]
    )
    body = normalise_body(raw)
    assert body == (
        "A Synthetic Title\n\n"
        "Tuesday, December 21, 2021\n\n"
        "A paragraph that was padded out by the HTML-to-text renderer.\n\n"
        "Another paragraph.\n"
    )


def test_italic_markers_are_kept_because_they_are_not_bold_markers():
    body = normalise_body("*DG:* A spoken line.\n\n**A bold phrase.**\n")
    assert "*DG:* A spoken line." in body
    assert "A bold phrase." in body
    assert "**" not in body


def test_empty_body_raises():
    with pytest.raises(NormaliseError):
        normalise_body("   \n\n ‌ ‌ \n")


def test_url_only_parentheses_leave_no_stray_punctuation():
    body = normalise_body("A fact here ( https://example.com/a/b?q=1 ) . And more.\n")
    assert body == "A fact here. And more.\n"
