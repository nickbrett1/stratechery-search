"""Build ``facets.sqlite`` — the 3-pre gazetteer index — from the derived text.

Usage::

    python -m stratechery_search.build_facets \
        --derived data/derived \
        --taxonomy entities.yaml \
        --out data/index/facets.sqlite

Why a separate file rather than more tables in ``search.sqlite``: the index is
rebuilt wholesale by :mod:`stratechery_search.build_index`, so facet tables living
inside it would be silently destroyed by an index rebuild. The gazetteer is also
the artifact that changes most often — testing one new alias must not require a
29,918-row FTS rebuild — and it is joined at **article grain**, so it has no need
to sit beside the passage table.

The report printed to stdout is the measurement the taxonomy is admitted on: per
entity and per surface document counts, so ``df_alias_union - df_largest_surface``
(G3 of the design note) is a number rather than an opinion. ``index-report.json``
is written beside the database for the same reason ``build_index`` writes one.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .facets import FacetError, build, load_taxonomy, taxonomy_digest

DEFAULT_DERIVED = Path("data/derived")
DEFAULT_TAXONOMY = Path("entities.yaml")
DEFAULT_OUT = Path("data/index/facets.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived", type=Path, default=DEFAULT_DERIVED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--index", type=Path, default=Path("data/index/search.sqlite"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="where to write the JSON report (default: next to --out)",
    )
    args = parser.parse_args(argv)

    try:
        taxonomy = load_taxonomy(args.taxonomy)
    except FacetError as error:
        print(f"taxonomy: {error}", file=sys.stderr)
        return 2

    if not args.derived.exists():
        print(f"no derived layer at {args.derived}", file=sys.stderr)
        return 2
    if not args.index.exists():
        print(f"no index at {args.index}", file=sys.stderr)
        return 2

    connection = sqlite3.connect(f"file:{args.index}?mode=ro", uri=True)
    connection.row_factory = None
    try:
        report = build(
            connection,
            taxonomy,
            args.derived,
            args.out,
            digest=taxonomy_digest(args.taxonomy),
        )
    finally:
        connection.close()

    report["taxonomy"] = {
        "version": taxonomy.version,
        "entities": [entity.id for entity in taxonomy.entities],
    }
    report_path = args.report or args.out.with_name("facets-report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    # A missing derived file is a broken build, not a warning: every gate in this
    # project counts articles, and one silently absent article moves every count.
    if report["missing_derived"]:
        print(
            f"missing derived text for {len(report['missing_derived'])} articles:"
            f" {report['missing_derived'][:5]}",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "out": str(args.out),
                "articles": report["articles"],
                "pairs": report["pairs"],
                "entities": report["entities"],
                "untagged": len(report["untagged"]),
                "per_entity": report["per_entity"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
