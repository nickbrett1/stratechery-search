"""What the corpus page owes the reader about its own shape.

Two answers, both of them derived from the data rather than typed into the page:
where sustained daily coverage begins (``daily_from_month``) and which years are
still short *after* that point (``stats_notes``).

Kept out of ``build_index`` because two callers need the same answer: the
indexer, which writes a build-time snapshot into ``stats_notes``, and ``/stats``,
which recomputes it. Both are pure functions of the counts, so computing them at
request time means they can never describe an archive they were not built from.
"""

from __future__ import annotations

from collections.abc import Iterable


def stats_notes(by_year: dict[int, int], daily_from_year: int | None = None) -> list[dict]:
    """Flag interior years holding less than half the corpus baseline.

    This is the §3 ``notes[]`` machinery carrying design §1's honesty
    requirement: a bar chart is not allowed to imply that a thin year is what
    the publisher did, when the thin year is a property of this archive.

    The baseline is the mean of the *other interior* years. The first and last
    calendar years are excluded from it because they are partial by
    construction — the archive starts on the first message this mailbox ever
    received and stops at the extraction date — and including them would let a
    one-message 2016 mask a real gap later on.

    **Years before the daily update arrived are not anomalous, so they are not
    noted.** The archive is a mailbox: it holds exactly what was subscribed to
    it, so 2017 is 51 weekly articles and no daily updates because the daily
    subscription starts on 2018-03-19, not because Stratechery published less.
    That boundary is stated once, by the chart's own caption (which reads it
    from ``/stats``), and repeating it as a note is how the same fact ends up
    written twice and wrong once. A year *after* the boundary that still comes
    up short is a genuine gap in the source and gets the note.
    """
    notes = []
    years = sorted(by_year)
    if len(years) < 3:
        return notes
    interior = years[1:-1]
    for year in interior:
        if daily_from_year is not None and year < daily_from_year:
            continue
        others = [by_year[other] for other in interior if other != year]
        if not others:
            continue
        baseline = int(round(sum(others) / len(others)))
        if baseline and by_year[year] < baseline / 2:
            notes.append(
                {
                    "code": f"under_represented_year_{year}",
                    "year": year,
                    "articles": by_year[year],
                    "neighbouring_mean": baseline,
                    # No cause is claimed. The note can see a count, not why the
                    # count is low, and an unexplained gap is better reported
                    # than confidently explained.
                    "message": (
                        f"{year} holds {by_year[year]} messages against ~{baseline} "
                        "in the surrounding years. Read that as a property of this "
                        "archive, not of Stratechery's output."
                    ),
                }
            )
    return notes


#: How many months of a year must carry a daily update before that year is taken
#: to be inside the daily subscription rather than before it. Half a year is
#: deliberately forgiving of a quiet month and deliberately unforgiving of a
#: stray sample — this archive has exactly two such samples, a [FREE] and a
#: [FIXED] daily update from 2017, and a rule that treated either as the start
#: of coverage would say "daily updates from September 2017" and be wrong by
#: fourteen months.
DAILY_MONTHS_MIN = 6


def daily_from_month(months_with_daily: Iterable[str]) -> str | None:
    """First month of sustained daily coverage, as ``YYYY-MM-01``.

    The corpus page states this beside its per-year chart: before it, a short bar
    means "not subscribed yet", not "published less". The caller decides what
    counts as a daily update; this function only decides whether the archive can
    honestly claim a boundary at all.

    Returns ``None`` when no year carries enough daily months — a three-message
    fixture must not be able to claim "daily updates from March 2026" on the
    strength of one message.
    """
    by_year: dict[str, set[str]] = {}
    for month in months_with_daily:
        by_year.setdefault(month[:4], set()).add(month)
    for year in sorted(by_year):
        months = sorted(by_year[year])
        if len(months) >= DAILY_MONTHS_MIN:
            return f"{months[0]}-01"
    return None
