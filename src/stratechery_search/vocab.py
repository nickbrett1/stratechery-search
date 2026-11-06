"""A deliberately small reader for ``vocab.yaml``.

Phase 1 ships no runtime dependencies (``dependencies = []``), so the synonym
table is read by a parser that understands exactly the shape the file uses and
refuses anything else. That is a feature: a hand-edited vocabulary file that
grows a surprising YAML construct should fail loudly at load time rather than
be silently misparsed into a query rewrite nobody can explain.

Accepted shape::

    terms:
      - canonical: aggregation theory
        aliases: [aggregation, aggregators, aggregation theory]

Comments (``#``) and blank lines are ignored; quoting is optional; a value may
not contain a ``#`` or ``[``/``]`` other than the alias list.
"""

from __future__ import annotations

import re
from pathlib import Path

_ENTRY_RE = re.compile(r"^\s*-\s*canonical:\s*(.+?)\s*$")
_ALIAS_RE = re.compile(r"^\s*aliases:\s*\[(.*)\]\s*$")


class VocabError(ValueError):
    """The vocabulary file is not in the accepted shape."""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_vocabulary(path: Path) -> dict[str, dict]:
    """Parse ``path`` into ``{canonical: {"canonical", "aliases"}}``."""
    entries: dict[str, dict] = {}
    current: dict | None = None

    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = (
            raw_line.split("#", 1)[0].rstrip() if "#" in raw_line else raw_line.rstrip()
        )
        if not line.strip():
            continue
        if line.strip() == "terms:":
            continue

        entry_match = _ENTRY_RE.match(line)
        if entry_match:
            canonical = _unquote(entry_match.group(1))
            if not canonical:
                raise VocabError(f"{path}:{number}: empty canonical term")
            if canonical in entries:
                raise VocabError(f"{path}:{number}: duplicate term {canonical!r}")
            current = {"canonical": canonical, "aliases": []}
            entries[canonical] = current
            continue

        alias_match = _ALIAS_RE.match(line)
        if alias_match:
            if current is None:
                raise VocabError(f"{path}:{number}: aliases before any canonical term")
            aliases = [
                _unquote(part)
                for part in alias_match.group(1).split(",")
                if part.strip()
            ]
            current["aliases"] = aliases
            continue

        raise VocabError(f"{path}:{number}: unsupported line {raw_line!r}")

    for canonical, entry in entries.items():
        if canonical not in entry["aliases"]:
            entry["aliases"].append(canonical)
    return entries
