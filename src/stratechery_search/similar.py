"""More like this: nearest passages to a whole article.

The design note (``stratechery-search-phase4-more-like-this``) settles three
things this module must not re-litigate:

* **Both sides are corpus-side vectors**, so there is no query embedding, no
  ``input_type`` asymmetry, no gateway call and therefore nothing to degrade to.
  The whole class of defects the hybrid arm carries simply cannot occur here.
* **The article is a set of passage vectors, not a vector.** Whole-article
  embedding was measured and refuted on this corpus (``self-disruption`` rank
  426 of 1,647 against the passage arm's 2), so "an article's centroid" is not
  an option here — it is the thing that lost.
* **One shared matrix product, not ``n`` kNN calls.** A median article has 12
  passages, so a loop over them sweeps a 245 MB table twelve times for the same
  answer. The corpus is swept once, the article's rows are scored in the same
  pass, and that is what makes the endpoint cheap enough to fetch after paint.

The self-mask is not an optimisation either. A passage's nearest neighbours are
its own adjacent passages, so the top ten of an unmasked search is the article
you are reading, in reading order, at distances near zero.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: How many articles ``/article/{id}/similar`` returns by default, and the cap.
DEFAULT_LIMIT = 8
MAX_LIMIT = 50


class SimilarError(RuntimeError):
    """The store cannot answer this question — 503, never a 500.

    A chunk-set mismatch between the Lance store and ``search.sqlite`` is a
    deployment mistake (phase 2 §4.5c), and the article page must still render
    with the block absent rather than fail as a whole.
    """

    code = "similar_unavailable"


@dataclass(frozen=True)
class Neighbour:
    chunk_id: str
    distance: float
    #: How many of the query article's own passages had *this* article as their
    #: best match. Evidence of coverage that needs no threshold: an article that
    #: echoes one paragraph scores 1, one that runs alongside it scores 12.
    matched_passages: int


def _numpy() -> Any:
    """``numpy``, or a 503 — never an ImportError escaping as a dropped request.

    The import is deliberately inside the call, like ``lancedb`` in
    :mod:`.vector_store`: the browser suite runs this server in an image with no
    vector stack at all, and a missing wheel must cost the *block*, not the
    article page. An ImportError escaping a handler closes the connection with
    no response — the client sees ERR_EMPTY_RESPONSE, which reads as a dead
    server rather than an absent feature.
    """
    try:
        import numpy
    except ImportError as error:  # a build without the vector stack
        raise SimilarError(f"numpy is not installed: {error}") from error
    return numpy


def _article_of(chunk_id: str) -> int:
    return int(chunk_id.split(":", 1)[0])


class Space:
    """The corpus matrix, held in memory and invalidated on store mtime.

    Same cache key as the table handle in :mod:`.vector_store` and for the same
    recorded reason: a cached handle made a rebuilt store invisible for the life
    of a process, and the mtime is what fixed it.
    """

    def __init__(self, path: Path, backend: Any) -> None:
        self.path = Path(path)
        self.backend = backend
        self._lock = threading.Lock()
        self._stamp: int | None = None
        self._ids: list[str] = []
        self._matrix: Any = None
        self._load_ms: int = 0
        #: Provenance as the store reports it, so `/similar` can say what the
        #: ranking was computed *from* rather than implying a model it did not use.
        self._embed_models: tuple[str, ...] = ()
        self._chunker_versions: tuple[int, ...] = ()

    # -- the cache -------------------------------------------------------

    def _stamp_now(self) -> int:
        try:
            return (self.path / "passages.lance").stat().st_mtime_ns
        except OSError:
            try:
                return self.path.stat().st_mtime_ns
            except OSError:
                return 0

    def _load(self, conn: Any = None) -> tuple[list[str], Any]:
        import time as _time

        np = _numpy()

        started = _time.perf_counter()
        try:
            table = self.backend.table(conn)
            rows = (
                table.search()
                .select(["chunk_id", "vector", "is_transactional"])
                .limit(None)
                .to_list()
            )
        except Exception as error:  # noqa: BLE001 - reported as 503, never 500
            raise SimilarError(f"the vector store is unavailable: {error}") from error
        if not rows:
            raise SimilarError("the vector store is empty")

        ids: list[str] = []
        vectors = []
        models: set[str] = set()
        chunkers: set[int] = set()
        for row in rows:
            # Transactional mail is excluded as a *target*, not merely filtered
            # from the answer: an access code must never appear beside an
            # article, and the 44 transactional articles' chunks are in the
            # store (it was embedded for parity with `chunks`, not with
            # `searchable_articles`).
            if row.get("is_transactional"):
                continue
            ids.append(row["chunk_id"])
            vectors.append(row["vector"])
            if row.get("embed_model"):
                models.add(str(row["embed_model"]))
            if row.get("chunker_version") is not None:
                chunkers.add(int(row["chunker_version"]))
        matrix = np.asarray(vectors, dtype="float32")
        self._ids, self._matrix = ids, matrix
        self._embed_models = tuple(sorted(models))
        self._chunker_versions = tuple(sorted(chunkers))
        self._load_ms = int((_time.perf_counter() - started) * 1000)
        return ids, matrix

    def ready(self, conn: Any = None) -> tuple[list[str], Any]:
        stamp = self._stamp_now()
        if self._matrix is None or stamp != self._stamp:
            with self._lock:
                if self._matrix is None or stamp != self._stamp:
                    self._load(conn)
                    self._stamp = stamp
        return self._ids, self._matrix

    @property
    def load_ms(self) -> int:
        return self._load_ms

    def basis(self, passages: int) -> dict:
        """What the ranking was computed from. One model, or the fact of a mix."""
        return {
            "passages": passages,
            "passages_in_store": len(self._ids),
            # A store holding two models cannot be ranked against itself; saying
            # so in the payload is cheaper than a plausible-looking number that
            # means nothing.
            "embed_model": (
                self._embed_models[0]
                if len(self._embed_models) == 1
                else ("mixed" if self._embed_models else None)
            ),
            "chunker_version": (
                self._chunker_versions[0] if len(self._chunker_versions) == 1 else None
            ),
        }

    # -- the query -------------------------------------------------------

    def _scores(self, query_chunk_ids: list[str], *, exclude_article: int, conn: Any):
        np = _numpy()

        ids, matrix = self.ready(conn)
        position = {chunk_id: index for index, chunk_id in enumerate(ids)}
        rows = [position[chunk_id] for chunk_id in query_chunk_ids if chunk_id in position]
        if not rows:
            # Nothing of this article is in the store: a stale index, and the
            # parity check should have caught it first.
            raise SimilarError("none of this article's passages are in the store")

        scores = matrix[rows] @ matrix.T  # (n, N) cosine similarity
        # Self-mask before ranking, by article rather than by row: the
        # near-duplicates are the *other* passages of the same article, and an
        # ordinal window misses the last-passage/first-passage case.
        targets = np.fromiter(
            (_article_of(chunk_id) for chunk_id in ids), dtype=np.int64, count=len(ids)
        )
        scores[:, targets == exclude_article] = -np.inf
        return ids, targets, rows, scores

    def passage_neighbours(
        self,
        query_chunk_ids: list[str],
        *,
        exclude_article: int,
        limit: int,
        conn: Any = None,
    ) -> list[tuple[str, Neighbour]]:
        """Each query passage's own best match — the passage-grain view.

        Returned with the query chunk it came from, because "this paragraph has a
        near-twin here" is only verifiable when the reader can see *which*
        paragraph. No fold: two passages of mine matching the same article are
        two rows on purpose at this grain.
        """
        np = _numpy()

        ids, targets, rows, scores = self._scores(
            query_chunk_ids, exclude_article=exclude_article, conn=conn
        )
        best_per_row = np.argmax(scores, axis=1)
        answered = []
        for row_index, column in enumerate(best_per_row):
            score = float(scores[row_index, column])
            if score == -np.inf:
                continue
            answered.append(
                (
                    ids[rows[row_index]],
                    Neighbour(
                        chunk_id=ids[column],
                        distance=round(1.0 - score, 6),
                        matched_passages=1,
                    ),
                )
            )
        answered.sort(key=lambda pair: (pair[1].distance, pair[1].chunk_id))
        return answered[:limit]

    def neighbours(
        self,
        query_chunk_ids: list[str],
        *,
        exclude_article: int,
        limit: int,
        conn: Any = None,
    ) -> list[Neighbour]:
        """The ``limit`` nearest *articles*, by their single best passage.

        ``max`` over the article's passages, never ``mean``: mean-over-a-daily is
        the error that refuted whole-article vectors, one level down. An article
        is like this one if **one** of its passages is; the count says how much
        of it echoes.
        """
        np = _numpy()

        ids, targets, _rows, scores = self._scores(
            query_chunk_ids, exclude_article=exclude_article, conn=conn
        )
        best_per_row = np.argmax(scores, axis=1)  # each query passage's best match
        best = scores.max(axis=0)  # each target's best match over my passages
        matched = np.bincount(best_per_row, minlength=len(ids))

        # One row per article, carrying its best passage. `argmax` per article
        # would be a second pass; taking the best column directly is the same
        # answer because the score is already the max over my passages.
        order = np.argsort(-best, kind="stable")
        chosen: list[Neighbour] = []
        seen: set[int] = set()
        for index in order:
            score = float(best[index])
            if score == -np.inf:
                break
            article = int(targets[index])
            if article in seen:
                continue
            seen.add(article)
            chosen.append(
                Neighbour(
                    chunk_id=ids[index],
                    distance=round(1.0 - score, 6),
                    matched_passages=int(matched[index]),
                )
            )
            if len(chosen) >= limit:
                break
        # Ties break on chunk_id, always: without it the same article yields two
        # orders and every later comparison is noise.
        chosen.sort(key=lambda neighbour: (neighbour.distance, neighbour.chunk_id))
        return chosen
