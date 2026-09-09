"""Document-level BM25 retrieval independent of database and API models."""

from dataclasses import dataclass
from typing import Callable

from backend.retrieval.bm25 import BM25Index
from backend.retrieval.tokenizer import tokenize


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    id: int
    title: str
    content: str
    category: str | None = None
    source: str | None = None

    @property
    def searchable_text(self) -> str:
        return "\n".join(
            value for value in (self.title, self.category, self.content) if value
        )


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    document: RetrievalDocument
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: tuple[RetrievalHit, ...]
    total: int


class BM25Retriever:
    """Tokenize a document collection once, then expose deterministic search."""

    def __init__(
        self,
        documents: list[RetrievalDocument],
        *,
        tokenizer: Callable[[str], list[str]] = tokenize,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._documents = documents
        self._tokenizer = tokenizer
        self._index = BM25Index(
            [tokenizer(document.searchable_text) for document in documents],
            k1=k1,
            b=b,
        )

    def search(self, query: str, limit: int = 5) -> RetrievalResult:
        if limit < 1:
            raise ValueError("limit must be positive.")
        query_tokens = self._tokenizer(query)
        ranked = self._index.top_k(
            query_tokens,
            max(1, len(self._documents)),
        )
        return RetrievalResult(
            hits=tuple(
                RetrievalHit(
                    document=self._documents[item.document_index],
                    score=item.score,
                )
                for item in ranked[:limit]
            ),
            total=len(ranked),
        )
