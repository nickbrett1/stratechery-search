"""One definition of "which passages are eligible", rendered twice.

Phase 1 filters in SQL: a date range, a kind, a sender, `has_images`, and one
unconditional clause — transactional mail is never searchable. Phase 2 adds a
second arm that queries LanceDB, which has no `articles` table and therefore
needs the same predicate spelled a second way.

Two hand-written predicates is how the two arms come to disagree about what
"2023, interviews, no transactions" means — and a disagreement in *filtering*
looks exactly like a ranking problem, forever. So both renderings come from the
one object below, which is built from the same validated parameters the HTTP
layer already produced. There is no other place to add a filter.

``is_transactional = 0`` is unconditional here, not a default: it is not a
parameter a caller can pass, and a reader must never reach a 2021 access code
because a cosine distance happened to be small.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Column names differ between the two stores by design: SQLite joins `articles`
#: (alias ``a``) and tests ``n_images > 0``, while the Lance row denormalises the
#: filter columns onto the row and stores the answer as a boolean. Keeping the
#: mapping in one table makes a drift a diff rather than a bug report.
SQL_TABLE_ALIAS = "a"


@dataclass(frozen=True)
class FilterSet:
    """The eligibility predicate, as data — never as a string."""

    start: str | None = None
    end: str | None = None
    kinds: tuple[str, ...] = ()
    senders: tuple[str, ...] = ()
    has_images: bool | None = None

    # -- SQLite -----------------------------------------------------------

    def sql(self) -> tuple[str, list[Any]]:
        """``WHERE`` fragment and bound values for `search.sqlite`."""
        clauses: list[str] = []
        values: list[Any] = []
        a = SQL_TABLE_ALIAS
        if self.start:
            clauses.append(f"{a}.sent_at >= ?")
            values.append(f"{self.start}T00:00:00Z")
        if self.end:
            clauses.append(f"{a}.sent_at <= ?")
            values.append(f"{self.end}T23:59:59Z")
        if self.kinds:
            clauses.append(f"{a}.kind IN (" + ",".join("?" * len(self.kinds)) + ")")
            values.extend(self.kinds)
        if self.senders:
            clauses.append(f"{a}.sender IN (" + ",".join("?" * len(self.senders)) + ")")
            values.extend(self.senders)
        if self.has_images is not None:
            clauses.append(f"{a}.n_images > 0" if self.has_images else f"{a}.n_images = 0")
        clauses.append(f"{a}.is_transactional = 0")
        return (" AND ".join(clauses), values)

    # -- LanceDB ----------------------------------------------------------

    def lance(self) -> str:
        """The same predicate in LanceDB ``where()`` syntax.

        LanceDB prefilters by default: ``where()`` restricts the rows eligible for
        retrieval *before* candidates are chosen. That is the correct primitive
        here rather than a workaround, and nothing in the reader may opt into
        postfiltering, which can return zero rows when matches exist.
        """
        clauses: list[str] = []
        if self.start:
            clauses.append(f"sent_at >= {_literal(f'{self.start}T00:00:00Z')}")
        if self.end:
            clauses.append(f"sent_at <= {_literal(f'{self.end}T23:59:59Z')}")
        if self.kinds:
            clauses.append("kind IN (" + ", ".join(_literal(k) for k in self.kinds) + ")")
        if self.senders:
            clauses.append(
                "sender IN (" + ", ".join(_literal(s) for s in self.senders) + ")"
            )
        if self.has_images is not None:
            clauses.append(f"has_images = {'true' if self.has_images else 'false'}")
        clauses.append("is_transactional = false")
        return " AND ".join(clauses)

    @classmethod
    def from_params(cls, params: Any) -> FilterSet:
        """Build from an ``api.SearchParams`` without importing it (cycle-free)."""
        return cls(
            start=params.start,
            end=params.end,
            kinds=tuple(params.kinds),
            senders=tuple(params.senders),
            has_images=params.has_images,
        )


def _literal(value: str) -> str:
    """A quoted string literal for the Lance expression language.

    Single quotes are doubled, which is the SQL convention LanceDB follows. The
    inputs are already validated upstream (dates are ``YYYY-MM-DD``, kinds are a
    closed set) so this is belt-and-braces rather than the only guard — but a
    sender name comes from a querystring and is not closed-set.
    """
    return "'" + str(value).replace("'", "''") + "'"
