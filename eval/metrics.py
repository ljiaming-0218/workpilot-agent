"""Small deterministic metrics shared by evaluation suites."""

from collections.abc import Sequence


def accuracy(outcomes: Sequence[bool]) -> float:
    """Return the fraction of correct cases without dropping failures."""
    return sum(outcomes) / len(outcomes) if outcomes else 0.0


def hit_at_k(
    ranked_document_ids: Sequence[int],
    relevant_document_ids: set[int],
    k: int,
) -> bool:
    """Return whether at least one relevant document appears in the top K."""
    return any(document_id in relevant_document_ids for document_id in ranked_document_ids[:k])


def reciprocal_rank(
    ranked_document_ids: Sequence[int],
    relevant_document_ids: set[int],
) -> float:
    """Return 1/rank for the first relevant result, or zero when none is found."""
    for rank, document_id in enumerate(ranked_document_ids, start=1):
        if document_id in relevant_document_ids:
            return 1.0 / rank
    return 0.0


def percentile(values: Sequence[int | float], fraction: float) -> float:
    """Return a nearest-rank percentile without external dependencies."""
    if not values:
        return 0.0
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1].")
    ordered = sorted(values)
    rank = max(1, int(len(ordered) * fraction + 0.999999))
    return float(ordered[rank - 1])
