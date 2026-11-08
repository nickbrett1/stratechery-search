"""A closed, versioned gazetteer of companies and people, matched on derived text.

This is the ``3-pre`` rung: a *facet* is a curated dimension with one canonical
id per article, not an open-vocabulary extraction pass. The distinction is the
whole point of the design note — a facet is cheap, deterministic, diffable and
re-runnable forever; a mention table is metered and its taxonomy drifts. So the
taxonomy lives in ``entities.yaml`` in the repo, reviewed like code, and this
module is the only thing that reads it.

Three rules here are bought by measured failures from the vocabulary work
([[stratechery-search-vocab-recommendation]]), not by taste:

**Case matters.** ``WHO`` matched the pronoun *who* 1,379 times and the
organisation 7. A surface that is also an ordinary English word is therefore
listed ``case_sensitive``, and the build reports both counts for every surface so
the choice is a measurement rather than a guess.

**Word boundaries, not substrings.** SMIC's 27 hits were *strict containment*
(``semiconductor``), which is fine for discovering a candidate and wrong for
counting one. Every surface is anchored with a custom boundary that treats
letters and digits as word characters — so ``TSMC's`` is a mention and
``TSMCsemi`` is not.

**Longest match wins.** ``Apple TV`` must not also count as ``Apple``. All
alternatives are ordered by surface length, and the scanner resumes after each
match, so at any position the longest alias that fits is the one taken.

What is deliberately absent: stemming (``integrator`` was df 31 unstemmed and
2,400 under porter — the index has already lost that distinction and this layer
must not inherit it), and plurals (a trailing ``s`` would turn *Arm* into
*Arms*); possessives are allowed because ``TSMC's`` is the same mention.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

#: The only two kinds, and the reason the file is not "entities" in general:
#: companies and people are closed, nameable, and stable across a decade. A
#: concept is the open-vocabulary problem, which stays in ladder phase 3.
KINDS = ("company", "person")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#: Letters and digits count as word characters; anything else does not. Used
#: instead of ``\b`` because ``\b`` is wrong at the edges of ``U.S.`` and ``3M``.
_LEFT = r"(?<![A-Za-z0-9])"
_RIGHT = r"(?![A-Za-z0-9])"
#: ``TSMC's`` and ``TSMC’s`` are mentions of TSMC. Plurals are not: see the note
#: on ``Arm`` above.
_POSSESSIVE = r"(?:['\u2019]s)?"
_SPACE = r"[\s\u00a0]+"
_POSSESSIVE_RE = re.compile(r"['\u2019]s$")


class FacetError(ValueError):
    """The taxonomy file, or the built facet index, is not in the accepted shape."""


@dataclass(frozen=True)
class Alias:
    surface: str
    case_sensitive: bool = False


@dataclass(frozen=True)
class Entity:
    id: str
    kind: str
    name: str
    aliases: tuple[Alias, ...]


@dataclass(frozen=True)
class Taxonomy:
    version: int
    entities: tuple[Entity, ...]


# -- the file ---------------------------------------------------------------


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _flow_list(value: str, path: Path, number: int) -> list[str]:
    """``[a, "b c", 'd']`` -> ``["a", "b c", "d"]``. Quotes protect commas."""
    raw = value.strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        raise FacetError(f"{path}:{number}: expected a [a, b] list, got {value!r}")
    inner = raw[1:-1]
    parts: list[str] = []
    current = ""
    quote = ""
    for char in inner:
        if quote:
            if char == quote:
                quote = ""
            current += char
        elif char in "\"'":
            quote = char
            current += char
        elif char == ",":
            parts.append(current)
            current = ""
        else:
            current += char
    if quote:
        raise FacetError(f"{path}:{number}: unterminated quote in {value!r}")
    parts.append(current)
    return [_unquote(part) for part in parts if part.strip()]


#: Keys an entry may carry. ``measured`` is free text and kept verbatim: the
#: numbers in it are the *record* of an admission decision, and the authoritative
#: counts come from the build report, not from parsing prose back.
_ENTRY_KEYS = {"id", "kind", "name", "aliases", "case_sensitive", "measured"}


def load_taxonomy(path: Path) -> Taxonomy:
    """Parse ``entities.yaml`` — a strict subset of YAML, refused loudly.

    The accepted shape is exactly what the file uses::

        version: 1
        entities:
          - id: tsmc
            kind: company
            name: TSMC
            aliases: [TSMC, "Taiwan Semiconductor"]
            case_sensitive: [TSMC]
            measured: df_case=224 df_union=224

    Comments must own their whole line; inline ``#`` is an error rather than a
    silent truncation, because a truncated alias list is a wrong count nobody
    would notice.
    """
    if not path.exists():
        raise FacetError(f"{path}: no such file")
    version: int | None = None
    entries: list[dict] = []
    current: dict | None = None
    seen_ids: set[str] = set()

    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("version:"):
            try:
                version = int(line.split(":", 1)[1].strip())
            except ValueError as exc:
                raise FacetError(
                    f"{path}:{number}: version must be an integer"
                ) from exc
            continue
        if line.strip() == "entities:":
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            body = stripped[2:].strip()
            key, _, value = body.partition(":")
            if key.strip() != "id":
                raise FacetError(
                    f"{path}:{number}: a new entry must start with id:, got {key!r}"
                )
            if current is not None:
                entries.append(current)
            current = {"id": _unquote(value), "line": number}
            continue
        if current is None:
            raise FacetError(f"{path}:{number}: {line!r} outside any entity")
        key, sep, value = stripped.partition(":")
        key = key.strip()
        if not sep:
            raise FacetError(f"{path}:{number}: expected key: value, got {line!r}")
        if key not in _ENTRY_KEYS:
            raise FacetError(
                f"{path}:{number}: unsupported key {key!r}"
                f" (allowed: {', '.join(sorted(_ENTRY_KEYS))})"
            )
        if key in current:
            raise FacetError(f"{path}:{number}: duplicate {key!r} in one entity")
        current[key] = (
            _flow_list(value, path, number)
            if key in {"aliases", "case_sensitive"}
            else _unquote(value)
        )
    if current is not None:
        entries.append(current)

    if version is None:
        raise FacetError(f"{path}: missing version:")
    if not entries:
        raise FacetError(f"{path}: no entities")

    entities: list[Entity] = []
    for entry in entries:
        line = entry.get("line")
        entity_id = entry.get("id", "")
        if not _ID_RE.match(entity_id):
            raise FacetError(
                f"{path}:{line}: id {entity_id!r} must be lower_snake_case"
                " (it becomes a SQL key and a JSON key)"
            )
        if entity_id in seen_ids:
            raise FacetError(f"{path}:{line}: duplicate id {entity_id!r}")
        seen_ids.add(entity_id)
        kind = entry.get("kind", "")
        if kind not in KINDS:
            raise FacetError(f"{path}:{line}: kind must be one of {KINDS}")
        name = entry.get("name", "").strip()
        if not name:
            raise FacetError(f"{path}:{line}: missing name:")
        surfaces = entry.get("aliases") or []
        if not surfaces:
            raise FacetError(f"{path}:{line}: no aliases")
        sensitive = set(entry.get("case_sensitive") or [])
        stray = sensitive - set(surfaces)
        if stray:
            raise FacetError(
                f"{path}:{line}: case_sensitive names {sorted(stray)}"
                " which are not in aliases"
            )
        if name.casefold() not in {surface.casefold() for surface in surfaces}:
            # Not an error, but it is the kind of omission that makes a facet
            # quietly wrong: the display name should be matchable.
            raise FacetError(
                f"{path}:{line}: name {name!r} is not one of its own aliases"
            )
        entities.append(
            Entity(
                id=entity_id,
                kind=kind,
                name=name,
                aliases=tuple(
                    Alias(surface=surface, case_sensitive=surface in sensitive)
                    for surface in surfaces
                ),
            )
        )
    return Taxonomy(version=version, entities=tuple(entities))


def taxonomy_digest(path: Path) -> str:
    """The file's own hash — what ``facet_meta`` records as provenance."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- the matcher ------------------------------------------------------------


def _surface_regex(surface: str, case_sensitive: bool) -> str:
    body = _SPACE.join(re.escape(part) for part in surface.split())
    body += _POSSESSIVE
    # ``(?-i:…)`` is needed even though the pattern compiles case-insensitively
    # for the *insensitive* surfaces: one pattern, per-alias case rules.
    wrapped = f"(?-i:{body})" if case_sensitive else f"(?i:{body})"
    return f"{_LEFT}{wrapped}{_RIGHT}"


class Matcher:
    """One compiled alternation over every alias of every entity.

    A single pattern rather than a loop per entity, because the rules that matter
    (longest match, no double counting) are properties of the *ordering*:
    alternatives are sorted by surface length, so a shorter name can never win at
    a position where a longer one also starts.
    """

    def __init__(self, taxonomy: Taxonomy) -> None:
        ordered: list[tuple[str, Alias]] = []
        for entity in taxonomy.entities:
            for alias in entity.aliases:
                ordered.append((entity.id, alias))
        # Longest first, and ties broken by surface so the pattern is stable
        # across runs (a dict-order dependency here would make builds differ).
        ordered.sort(key=lambda pair: (-len(pair[1].surface), pair[1].surface))
        alternatives: list[str] = []
        self.group_to_entity: dict[str, str] = {}
        for position, (entity_id, alias) in enumerate(ordered):
            group = f"a{position}"
            self.group_to_entity[group] = entity_id
            alternatives.append(
                f"(?P<{group}>{_surface_regex(alias.surface, alias.case_sensitive)})"
            )
        self.pattern = re.compile("|".join(alternatives))
        self._surface_by_group = {
            f"a{position}": alias.surface for position, (_, alias) in enumerate(ordered)
        }

    def find(self, text: str) -> list[tuple[str, str]]:
        """``[(entity_id, canonical surface)]`` in order of appearance."""
        return [
            (entity_id, surface) for entity_id, surface, _ in self.find_verbose(text)
        ]

    def find_verbose(self, text: str) -> list[tuple[str, str, bool]]:
        """As :meth:`find`, plus whether the text matched in the exact case.

        That third field is what makes the ``case_sensitive`` column a
        *measurement*: if a surface is listed case-insensitively but only ever
        appears in one casing, the flag is free; if it appears in several, the
        flag is doing work and the count says how much. ``Apple`` is the case
        worth checking — the company and the fruit are the same string.
        """
        found: list[tuple[str, str, bool]] = []
        for match in self.pattern.finditer(text or ""):
            group = match.lastgroup
            if group is None:  # pragma: no cover - every alternative is named
                continue
            surface = self._surface_by_group[group]
            literal = _POSSESSIVE_RE.sub("", match.group())
            found.append((self.group_to_entity[group], surface, literal == surface))
        return found

    def surfaces(self, text: str) -> Counter[tuple[str, str]]:
        return Counter(self.find(text))


# -- the built index --------------------------------------------------------


@dataclass(frozen=True)
class FacetEntity:
    id: str
    kind: str
    name: str


class FacetIndex:
    """The built ``facets.sqlite``, loaded into memory once.

    Deliberately *not* ``ATTACH``ed to the index connection: the two databases
    have different rebuild cadences, and a facet rebuild must be able to happen
    without touching a 29,918-row FTS rebuild. Everything here is small — 1,647
    articles × ~40 entities is tens of thousands of pairs — so loading it into a
    dict costs microseconds and removes a join that would have had to be written
    twice (SQL and LanceDB) and could therefore drift.
    """

    def __init__(
        self,
        entities: dict[str, FacetEntity],
        by_article: dict[int, tuple[str, ...]],
        articles_by_entity: dict[str, frozenset[int]],
        meta: dict[str, str],
    ) -> None:
        self.entities = entities
        self.by_article = by_article
        self.articles_by_entity = articles_by_entity
        self.meta = meta

    @property
    def version(self) -> int:
        return int(self.meta.get("taxonomy_version", "0"))

    def order(self) -> list[str]:
        """Entity ids by document frequency, so a UI can render a stable list."""
        return sorted(
            self.entities,
            key=lambda entity_id: (
                -len(self.articles_by_entity.get(entity_id, ())),
                entity_id,
            ),
        )

    def articles_for(self, entity_ids: tuple[str, ...]) -> frozenset[int]:
        """Article ids for an AND across entities (OR across their aliases).

        Raises :class:`FacetError` for an unknown id: the set is closed, so a
        typo is a 400 rather than an empty result that looks like "no articles".
        """
        unknown = [
            entity_id for entity_id in entity_ids if entity_id not in self.entities
        ]
        if unknown:
            raise FacetError(f"unknown entity: {', '.join(sorted(unknown))}")
        if not entity_ids:
            return frozenset(self.by_article)
        sets = [
            self.articles_by_entity.get(entity_id, frozenset())
            for entity_id in entity_ids
        ]
        return frozenset(frozenset.intersection(*sets)) if sets else frozenset()

    def counts(self, article_ids: set[int] | frozenset[int]) -> list[dict]:
        """Per-entity article counts over a matched set, largest first."""
        totals: Counter[str] = Counter()
        for article_id in article_ids:
            for entity_id in self.by_article.get(article_id, ()):
                totals[entity_id] += 1
        return [
            {
                "entity_id": entity_id,
                "name": self.entities[entity_id].name,
                "kind": self.entities[entity_id].kind,
                "articles": count,
            }
            for entity_id, count in sorted(
                totals.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ]


def open_index(path: Path | None) -> FacetIndex | None:
    """Load a built facet index, tolerating its absence.

    Missing is a legitimate state — the taxonomy is built per corpus, not shipped
    in the image — and it must degrade to "no entity facet" rather than take
    search down, exactly as a missing ``vocab.yaml`` means "no expansions".
    """
    if path is None or not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        entities = {
            row[0]: FacetEntity(id=row[0], kind=row[1], name=row[2])
            for row in connection.execute("SELECT entity_id, kind, name FROM entities")
        }
        by_article: dict[int, list[str]] = {}
        for article_id, entity_id in connection.execute(
            "SELECT article_id, entity_id FROM article_entities ORDER BY article_id, entity_id"
        ):
            by_article.setdefault(int(article_id), []).append(entity_id)
        meta = dict(connection.execute("SELECT key, value FROM facet_meta"))
    finally:
        connection.close()
    articles_by_entity: dict[str, frozenset[int]] = {}
    for article_id, ids in by_article.items():
        for entity_id in ids:
            articles_by_entity.setdefault(entity_id, set()).add(article_id)  # type: ignore[arg-type]
    return FacetIndex(
        entities=entities,
        by_article={key: tuple(value) for key, value in by_article.items()},
        articles_by_entity={
            key: frozenset(value) for key, value in articles_by_entity.items()
        },
        meta=meta,
    )


# -- the build --------------------------------------------------------------

BUILD_SCHEMA = """
CREATE TABLE entities (
  entity_id TEXT PRIMARY KEY,
  kind      TEXT NOT NULL,
  name      TEXT NOT NULL,
  aliases   TEXT NOT NULL
);
CREATE TABLE article_entities (
  article_id INTEGER NOT NULL,
  entity_id  TEXT    NOT NULL,
  mentions   INTEGER NOT NULL,
  surfaces   TEXT    NOT NULL,
  PRIMARY KEY (article_id, entity_id)
);
CREATE TABLE surface_docs (
  entity_id      TEXT    NOT NULL,
  surface        TEXT    NOT NULL,
  case_sensitive INTEGER NOT NULL,
  articles       INTEGER NOT NULL,
  mentions       INTEGER NOT NULL,
  PRIMARY KEY (entity_id, surface)
);
CREATE TABLE facet_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX article_entities_by_entity ON article_entities(entity_id);
"""


def build(
    conn: sqlite3.Connection,
    taxonomy: Taxonomy,
    derived: Path,
    out: Path,
    *,
    digest: str | None = None,
) -> dict:
    """Write ``out`` from the derived text layer, atomically and idempotently.

    ``out`` is written to a sibling temp file and renamed, so a failed build
    never installs an index the service would read — the same rule
    ``build_index`` follows, for the same reason.

    Article grain, unconditionally: a facet counts **articles that mention a
    company**, not passages. Counting passages would say 182 for a company whose
    work appears in 40 articles, and the panel would disagree with the result
    list by a factor of four.
    """
    matcher = Matcher(taxonomy)
    rows: list[tuple[int, str, int, str]] = []
    surface_articles: Counter[tuple[str, str]] = Counter()
    surface_mentions: Counter[tuple[str, str]] = Counter()
    #: Articles where the surface appeared in some *other* casing. A surface is
    #: only free of that if every match was exact, so this is an upper bound on
    #: what `case_sensitive: [x]` would cost.
    surface_other_case: Counter[tuple[str, str]] = Counter()
    missing: list[int] = []
    seen_articles = 0

    searchable = [
        row[0]
        for row in conn.execute(
            "SELECT message_id FROM articles WHERE is_transactional = 0 ORDER BY message_id"
        )
    ]
    for message_id in searchable:
        path = derived / f"{message_id}.txt"
        if not path.exists():
            missing.append(message_id)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        seen_articles += 1
        verbose = matcher.find_verbose(text)
        if not verbose:
            continue
        found: Counter[tuple[str, str]] = Counter()
        for entity_id, surface, _ in verbose:
            found[(entity_id, surface)] += 1
        loose = {
            (entity_id, surface) for entity_id, surface, exact in verbose if not exact
        }
        for key in loose:
            surface_other_case[key] += 1
        per_entity: dict[str, list[tuple[str, int]]] = {}
        for (entity_id, surface), count in found.items():
            per_entity.setdefault(entity_id, []).append((surface, count))
        for entity_id, surfaces in sorted(per_entity.items()):
            total = sum(count for _, count in surfaces)
            for surface, count in surfaces:
                surface_articles[(entity_id, surface)] += 1
                surface_mentions[(entity_id, surface)] += count
            rows.append(
                (
                    int(message_id),
                    entity_id,
                    total,
                    json.dumps(sorted(surfaces), ensure_ascii=False),
                )
            )

    case_sensitive = {
        (entity.id, alias.surface): alias.case_sensitive
        for entity in taxonomy.entities
        for alias in entity.aliases
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(out.suffix + ".tmp")
    if temp.exists():
        temp.unlink()
    facet_conn = sqlite3.connect(temp)
    try:
        facet_conn.executescript(BUILD_SCHEMA)
        facet_conn.executemany(
            "INSERT INTO entities VALUES (?, ?, ?, ?)",
            [
                (
                    entity.id,
                    entity.kind,
                    entity.name,
                    json.dumps(
                        [alias.surface for alias in entity.aliases],
                        ensure_ascii=False,
                    ),
                )
                for entity in taxonomy.entities
            ],
        )
        facet_conn.executemany("INSERT INTO article_entities VALUES (?, ?, ?, ?)", rows)
        facet_conn.executemany(
            "INSERT INTO surface_docs VALUES (?, ?, ?, ?, ?)",
            [
                (
                    entity_id,
                    surface,
                    1 if case_sensitive.get((entity_id, surface)) else 0,
                    surface_articles[(entity_id, surface)],
                    surface_mentions[(entity_id, surface)],
                )
                for entity_id, surface in sorted(surface_articles)
            ],
        )
        facet_conn.executemany(
            "INSERT INTO facet_meta VALUES (?, ?)",
            [
                ("taxonomy_version", str(taxonomy.version)),
                ("taxonomy_sha256", digest or ""),
                ("articles", str(seen_articles)),
                ("pairs", str(len(rows))),
            ],
        )
        facet_conn.commit()
    finally:
        facet_conn.close()
    temp.replace(out)

    articles_by_entity: Counter[str] = Counter(row[1] for row in rows)
    return {
        "articles": seen_articles,
        "missing_derived": missing,
        "pairs": len(rows),
        "entities": len(taxonomy.entities),
        "per_entity": dict(
            sorted(articles_by_entity.items(), key=lambda pair: (-pair[1], pair[0]))
        ),
        "surface_docs": {
            f"{entity_id}:{surface}": {
                "articles": surface_articles[(entity_id, surface)],
                "mentions": surface_mentions[(entity_id, surface)],
                "case_sensitive": bool(case_sensitive.get((entity_id, surface))),
                "articles_with_other_case": surface_other_case[(entity_id, surface)],
            }
            for entity_id, surface in sorted(surface_articles)
        },
        "untagged": sorted(set(searchable) - {row[0] for row in rows} - set(missing)),
    }
