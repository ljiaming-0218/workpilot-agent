"""Generate, guard, and execute one read-only SQL query."""

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.schemas.text2sql import GeneratedSQL, Text2SQLRequest, Text2SQLResult
from backend.security.sql_guard import GuardedSQL, SQLGuard, SQLGuardError, TABLE_SCHEMAS
from backend.services.llm_service import StructuredLLM
from backend.utils.trace_context import emit_sql_trace


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "text2sql.txt"
TEXT2SQL_SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8").strip()


class Text2SQLService:
    """Orchestrate LLM generation, deterministic AST validation, and DB execution."""

    def __init__(
        self,
        llm: StructuredLLM,
        guard: SQLGuard | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._llm = llm
        self._guard = guard or SQLGuard()
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))

    def generate_sql(self, question: str) -> GuardedSQL:
        request = Text2SQLRequest(question=question)
        generated = self._llm.generate_structured(
            TEXT2SQL_SYSTEM_PROMPT,
            self._build_user_prompt(request.question),
            GeneratedSQL,
        )
        started_ns = perf_counter_ns()
        try:
            guarded = self._guard.validate(generated.sql)
        except SQLGuardError as exc:
            emit_sql_trace(
                sql=generated.sql,
                guard_result={"allowed": False, "code": exc.code},
                latency_ms=_latency_ms(started_ns),
                status="failed",
                error=str(exc),
            )
            raise
        emit_sql_trace(
            sql=guarded.sql,
            guard_result={
                "allowed": True,
                "tables": list(guarded.tables),
                "limit": guarded.limit,
            },
            latency_ms=_latency_ms(started_ns),
            status="success",
            error=None,
        )
        return guarded

    def query(self, session: Session, question: str) -> Text2SQLResult:
        guarded = self.generate_sql(question)
        result = session.execute(text(guarded.sql))
        columns = list(result.keys())
        rows = [
            {
                column: self._serialize_value(value)
                for column, value in zip(columns, row, strict=True)
            }
            for row in result.fetchall()
        ]
        return Text2SQLResult(
            sql=guarded.sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
        )

    def _build_user_prompt(self, question: str) -> str:
        schema_lines = [
            f"{table}({', '.join(sorted(columns))})"
            for table, columns in sorted(TABLE_SCHEMAS.items())
        ]
        now_value = self._utc_now()
        if now_value.tzinfo is None:
            raise ValueError("utc_now must return a timezone-aware datetime.")
        now = now_value.astimezone(timezone.utc).isoformat()
        question_json = json.dumps(question, ensure_ascii=False)
        return (
            "SCHEMA_ALLOWLIST:\n"
            + "\n".join(schema_lines)
            + f"\n\nCURRENT_UTC: {now}"
            + f"\n\nUSER_QUESTION_JSON: {question_json}"
        )

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(value, (date, time)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, bytes):
            return value.hex()
        return str(value)


def _latency_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)
