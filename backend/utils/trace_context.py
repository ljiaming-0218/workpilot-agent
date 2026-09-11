"""Context-local bridge from shared services to the active Agent trace recorder."""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator, Protocol


class TraceSink(Protocol):
    def record_llm(
        self,
        *,
        model: str,
        response_schema: str,
        latency_ms: int,
        status: str,
        error: str | None,
        tokens: dict[str, int] | None,
    ) -> None: ...

    def record_sql(
        self,
        *,
        sql: str,
        guard_result: dict[str, Any],
        latency_ms: int,
        status: str,
        error: str | None,
    ) -> None: ...


_ACTIVE_TRACE_SINK: ContextVar[TraceSink | None] = ContextVar(
    "active_trace_sink",
    default=None,
)


@contextmanager
def bind_trace_sink(sink: TraceSink) -> Iterator[None]:
    token: Token[TraceSink | None] = _ACTIVE_TRACE_SINK.set(sink)
    try:
        yield
    finally:
        _ACTIVE_TRACE_SINK.reset(token)


def emit_llm_trace(**event: Any) -> None:
    sink = _ACTIVE_TRACE_SINK.get()
    if sink is not None:
        sink.record_llm(**event)


def emit_sql_trace(**event: Any) -> None:
    sink = _ACTIVE_TRACE_SINK.get()
    if sink is not None:
        sink.record_sql(**event)
