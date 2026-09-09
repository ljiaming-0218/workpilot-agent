"""Small dependency-free BM25 index with deterministic Top-K ranking."""

from collections import Counter
from dataclasses import dataclass
from math import log
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class BM25Score:
    document_index: int
    score: float


class BM25Index:
    """Precompute term statistics and score tokenized documents with BM25."""

    def __init__(
        self,
        documents: Sequence[Sequence[str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be positive.")
        if not 0 <= b <= 1:
            raise ValueError("b must be between 0 and 1.")
        self._k1 = k1
        self._b = b
        self._term_frequencies = [Counter(document) for document in documents]
        self._document_lengths = [len(document) for document in documents]
        self._document_count = len(documents)
        self._average_length = (
            sum(self._document_lengths) / self._document_count
            if self._document_count
            else 0.0
        )

        document_frequency: Counter[str] = Counter()
        for terms in self._term_frequencies:
            document_frequency.update(terms.keys())
        self._idf = {
            term: log(
                1
                + (self._document_count - frequency + 0.5)
                / (frequency + 0.5)
            )
            for term, frequency in document_frequency.items()
        }

    def scores(self, query_tokens: Iterable[str]) -> list[float]:
        """Return one score per document; repeated query terms count once."""
        if not self._document_count:
            return []
        scores = [0.0] * self._document_count
        if self._average_length == 0:
            return scores
        unique_query_terms = tuple(dict.fromkeys(query_tokens))
        for document_index, frequencies in enumerate(self._term_frequencies):
            document_length = self._document_lengths[document_index]
            length_ratio = document_length / self._average_length
            normalization = self._k1 * (1 - self._b + self._b * length_ratio)
            for term in unique_query_terms:
                term_frequency = frequencies.get(term, 0)
                if not term_frequency:
                    continue
                scores[document_index] += self._idf.get(term, 0.0) * (
                    term_frequency * (self._k1 + 1)
                    / (term_frequency + normalization)
                )
        return scores

    def top_k(self, query_tokens: Iterable[str], limit: int) -> list[BM25Score]:
        """Return positive-scoring documents ordered by score then source order."""
        if limit < 1:
            raise ValueError("limit must be positive.")
        ranked = [
            BM25Score(document_index=index, score=score)
            for index, score in enumerate(self.scores(query_tokens))
            if score > 0
        ]
        ranked.sort(key=lambda item: (-item.score, item.document_index))
        return ranked[:limit]
