#!/usr/bin/env python3
"""Build the three-message corpus somewhere a dev server can serve it.

    python3 scripts/e2e-corpus.py .e2e

Playwright needs a running app, and the app is useless without an index. Rather
than reach for the real 1,691-article archive (which is not in the repo, not in CI,
and would make every browser test depend on a 98 MB download) the browser suite
uses the same fixture corpus as the contract tests — same module, one definition.

Idempotent: an existing corpus is reused unless ``--force`` is passed, so a local
`npx playwright test` loop does not re-flatten and re-index on every run.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `corpus` is a test module, not an installed one: the package ships the search,
# not the fixture. `src` is on the path for the same reason from the other side —
# corpus.py imports `stratechery_search`, and requiring the caller to remember
# PYTHONPATH=src is a trap: it worked in the devcontainer, where the package is
# installed, and failed in CI with ModuleNotFoundError on the first run.
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from corpus import build  # noqa: E402  (path had to be set up first)

INDEX = Path("index") / "search.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root", nargs="?", default=".e2e", type=Path, help="where to build"
    )
    parser.add_argument(
        "--force", action="store_true", help="rebuild even if an index exists"
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if args.force and root.exists():
        shutil.rmtree(root)
    if (root / INDEX).exists():
        print(f"corpus already built: {root / INDEX}", file=sys.stderr)
        return 0

    root.mkdir(parents=True, exist_ok=True)
    archive, _derived, index_path = build(root)
    print(f"archive: {archive}")
    print(f"index:   {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
