"""Which years the corpus page has to explain.

Kept out of ``build_index`` because two callers need the same answer: the
indexer, which writes the build-time snapshot into ``stats_notes``, and
``/stats``, which recomputes it. The note is a pure function of the per-year
counts and the date the daily update started arriving, so computing it at
request time means a note can never describe an archive it was not built from.
"""

from __future__ import annotations


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
