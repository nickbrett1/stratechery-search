"""Normalise a Stratechery plain-text email body into indexable prose.

The input is the ``text/plain`` part of a message in the archive. That part
carries the article, but also the mailing-list chrome that surrounds it:

* a Mailchimp preamble ("The Daily Stratechery email...", "View in browser"),
* a Passport/ESP footer (podcast blurb, subscription details, copyright),
* parenthesised URLs, bare share URLs and signed ``access_token`` JWTs,
* Markdown-ish ``**bold**`` markers and ``---``/``****`` divider runs,
* for the 2021-2022 Passport era, an ASCII-table rendering of the HTML where
  every prose line is padded to ~1,150 characters behind ``| | | | |``.

The normaliser is deliberately line/regex level, not an HTML parser: design
section 1 records that the HTML is ESP layout and that ``text/plain`` is the
extraction target. Every rule here is deterministic so a re-run is byte
identical, which the flatten gates require.
"""

from __future__ import annotations

import re

FLATTENER_VERSION = "1"
NORMALIZER_VERSION = "1"

ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"

#: The Mailchimp-era preamble. Its presence means the first article heading has
#: to be located; without it (Passport era) the lede at the top is article text.
PREAMBLE_RE = re.compile(r"^\s*The Daily Stratechery email\b", re.IGNORECASE)
VIEW_IN_BROWSER_RE = re.compile(
    r"^\s*View\s+(?:this\s+email\s+)?in\s+(?:your\s+)?browser\b", re.IGNORECASE
)
TITLE_MARKER_RE = re.compile(r"^\s*\*\*\s+\S")
DIVIDER_RE = re.compile(r"^\s*(?:[-*_=]\s*){3,}$")

#: Start of mailing-list chrome at the end of a body. Matched at line start
#: after the pseudo-table prefixes have been stripped. The order does not
#: matter; the earliest match wins.
FOOTER_ANCHORS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^Copyright\s+©",
        r"^©\s*\d{4}\b",
        r"^Our mailing address is:?",
        r"^You are receiving this email because\b",
        r"^Subscription Information\b",
        r"^Member since:",
        r"^To unsubscribe from email\b",
        r"^unsubscribe from this list\b",
        r"^update subscription preferences\b",
        r"^Email Marketing Powered by MailChimp\b",
        r"^(?:The\s+)?(?:Daily\s+|Stratechery\s+)?Update\s+is\s+intended\s+for\s+a\s+single\s+recipient\b",
        r"^The\s+Stratechery\s+Update\s+is\s+intended\s+for\s+a\s+single\s+recipient\b",
        r"^(?:This|The)\s+(?:Daily\s+|Weekly\s+|Stratechery\s+)?Update\s+(?:is|will be)\s+(?:also\s+)?available\s+as\s+a\s+podcast\b",
        r"^This\s+Daily\s+Update\s+Interview\s+is\s+also\s+available\s+as\s+a\s+podcast\b",
        r"^Thanks\s+for\s+being\s+a\s+(?:supporter|subscriber),",
        r"^Listen to this update and other Stratechery Plus content\b",
        r"^Discuss on the Stratechery Member Forum\b",
        r"^Subscribe to the Stratechery Daily Update\b",
        r"^Don’t forget to share!?$",
    )
)

#: A parenthesised URL, including the Passport style ``( https://… )``. A URL
#: itself never contains whitespace, so this cannot run past its closing paren.
PAREN_URL_RE = re.compile(r"\(\s*https?://[^)\s]*(?:\s*https?://[^)\s]*)*\s*\)")
BARE_URL_RE = re.compile(r"https?://\S+")
#: Signed token as it appears inside ``access_token=`` parameters. The residue
#: gate treats this as a leak, so it is removed even when a URL strip missed it.
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
MARKDOWN_BOLD_RE = re.compile(r"\*\*")
ANCHOR_ARTEFACT_RE = re.compile(r"\(\s*#\s*\)")

#: Standalone ESP chrome lines that sit *inside* the article block (after the
#: heading, before the body) and so survive the header/footer cuts.
CHROME_LINE_RES = (
    re.compile(r"^Listen to this Update in your podcast player\b", re.IGNORECASE),
)

#: Pseudo-table prefixes/suffixes from the 2021-2022 HTML-to-text rendering.
_LEADING_CELLS_RE = re.compile(r"^\s*(?:\|\s*)+")
_TRAILING_CELLS_RE = re.compile(r"\s*(?:\|\s*)+$")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
#: Removing a parenthesised URL leaves "here ," and " ;" behind. Closing that
#: gap is cosmetic but it is what a reader sees in every snippet.
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


class NormaliseError(ValueError):
    """The body could not be normalised into article text."""


def _clean_pseudo_table(lines: list[str]) -> list[str]:
    """Strip ASCII-table cell padding left by the 2021-2022 text renderer.

    Only lines that actually contain a pipe are touched, so ordinary prose with
    an em-dash or a bullet is left alone. A line made only of table strokes
    becomes empty and is dropped by the later blank-line pass.
    """
    out: list[str] = []
    for line in lines:
        was_table = "|" in line
        if was_table:
            line = _LEADING_CELLS_RE.sub("", line)
            line = _TRAILING_CELLS_RE.sub("", line)
        line = _MULTI_SPACE_RE.sub(" ", line).rstrip()
        out.append(line)
        # The 2021-2022 renderer puts one paragraph per padded table row with
        # no blank lines between them. Restore the paragraph boundary that the
        # chunker and the reader both expect.
        if (
            was_table
            and line.strip()
            and not DIVIDER_RE.match(line)
            and not set(line) <= set("+-=_ |")
        ):
            out.append("")
    return out


def _first_non_blank(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if line.strip():
            return i
    return None


def _strip_preamble(lines: list[str]) -> list[str]:
    """Drop the Mailchimp preamble, keeping the article heading and body.

    Two shapes exist in the corpus:

    * ``** Title`` followed within a few lines by a ``----`` rule (2016, 2018+),
    * ``** Title`` then a date line then the rule (2017).

    If no heading marker is found, everything up to and including the
    "View in browser" line goes; that is still better than keeping the banner.
    """
    first = _first_non_blank(lines)
    if first is None:
        return []

    if PREAMBLE_RE.match(lines[first]):
        heading = None
        for i in range(first, len(lines)):
            if not TITLE_MARKER_RE.match(lines[i]):
                continue
            window = [ln for ln in lines[i + 1 : i + 5] if ln.strip()]
            if any(DIVIDER_RE.match(ln) for ln in window):
                heading = i
                break
        if heading is not None:
            return lines[heading:]

        # No heading: cut the banner itself, keep the rest.
        for i in range(first, len(lines)):
            if VIEW_IN_BROWSER_RE.match(lines[i]):
                return lines[i + 1 :]
        return lines[first:]

    # Passport era: the lede above "View in browser" is article text. Remove
    # only the wrapper lines; the URL inside them is stripped globally below.
    return [ln for ln in lines if not VIEW_IN_BROWSER_RE.match(ln)]


def _cut_footer(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if any(anchor.match(line) for anchor in FOOTER_ANCHORS):
            return lines[:i]
    return lines


def normalise_body(raw: str) -> str:
    """Normalise one decoded ``text/plain`` body into article prose.

    Raises :class:`NormaliseError` if nothing but whitespace survives: an empty
    derived file would pass every residue gate and silently disappear from the
    index.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate({ord(ch): None for ch in ZERO_WIDTH})

    lines = _clean_pseudo_table(text.split("\n"))
    lines = _strip_preamble(lines)
    lines = _cut_footer(lines)

    cleaned: list[str] = []
    for line in lines:
        # Drop rule runs before the bold strip: a 99-asterisk rule would
        # otherwise be reduced to a single stray "*" line and survive.
        stripped = line.strip()
        if len(stripped) >= 3 and set(stripped) <= set("*-_="):
            continue
        if any(chrome.match(stripped) for chrome in CHROME_LINE_RES):
            continue
        line = PAREN_URL_RE.sub("", line)
        line = BARE_URL_RE.sub("", line)
        line = JWT_RE.sub("", line)
        line = ANCHOR_ARTEFACT_RE.sub("", line)
        line = MARKDOWN_BOLD_RE.sub("", line)
        line = _EMPTY_PARENS_RE.sub("", line)
        line = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", line)
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        if not line:
            cleaned.append("")
            continue
        if DIVIDER_RE.match(line):
            continue
        # ASCII-table strokes left behind by the 2021-2022 renderer, e.g.
        # "+----------------+" or "| | | |".
        if len(line) >= 3 and set(line) <= set("+-=_ |"):
            continue
        cleaned.append(line)

    body = _MULTI_BLANK_RE.sub("\n\n", "\n".join(cleaned)).strip()
    if not body:
        raise NormaliseError("body is empty after normalisation")
    return body + "\n"
