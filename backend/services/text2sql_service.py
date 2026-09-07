"""Generate, guard, and execute one read-only SQL query."""

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.schemas.text2sql import GeneratedSQL, Text2SQLRequest, Text2SQLResult
from backend.security.sql_guard import GuardedSQL, SQLGuard, TABLE_SCHEMAS
from backend.services.llm_service import StructuredLLM


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
        return self._guard.validate(generated.sql)

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
