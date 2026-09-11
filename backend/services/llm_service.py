"""Single gateway for structured LLM calls."""

from time import perf_counter_ns
from typing import Protocol, TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from backend.config import Settings
from backend.utils.trace_context import emit_llm_trace


StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


class LLMServiceError(RuntimeError):
    code = "LLM_ERROR"


class StructuredLLM(Protocol):
    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredOutputT],
    ) -> StructuredOutputT: ...


class LLMService:
    """OpenAI-compatible implementation; callers depend on StructuredLLM."""

    def __init__(self, settings: Settings, client: OpenAI | None = None) -> None:
        api_key = settings.llm_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise LLMServiceError("LLM_API_KEY is not configured.")
        if settings.llm_model is None:
            raise LLMServiceError("LLM_MODEL is not configured.")

        self._model = settings.llm_model
        self._client = client or OpenAI(
            api_key=api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredOutputT],
    ) -> StructuredOutputT:
        started_ns = perf_counter_ns()
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
                temperature=0,
            )
            message = completion.choices[0].message
        except (OpenAIError, ValidationError, IndexError, ValueError) as exc:
            self._emit_trace(
                response_model,
                started_ns,
                status="failed",
                error=type(exc).__name__,
            )
            raise LLMServiceError("Structured LLM request failed.") from exc

        if message.refusal:
            self._emit_trace(
                response_model,
                started_ns,
                status="failed",
                error="LLM_REFUSAL",
                completion=completion,
            )
            raise LLMServiceError("The model refused the structured request.")
        if message.parsed is None:
            self._emit_trace(
                response_model,
                started_ns,
                status="failed",
                error="EMPTY_STRUCTURED_RESULT",
                completion=completion,
            )
            raise LLMServiceError("The model returned no structured result.")
        self._emit_trace(
            response_model,
            started_ns,
            status="success",
            error=None,
            completion=completion,
        )
        return message.parsed

    def _emit_trace(
        self,
        response_model: type[BaseModel],
        started_ns: int,
        *,
        status: str,
        error: str | None,
        completion: object | None = None,
    ) -> None:
        usage = getattr(completion, "usage", None)
        tokens = None
        if usage is not None:
            tokens = {
                "input": int(getattr(usage, "prompt_tokens", 0)),
                "output": int(getattr(usage, "completion_tokens", 0)),
                "total": int(getattr(usage, "total_tokens", 0)),
            }
        emit_llm_trace(
            model=str(getattr(completion, "model", None) or self._model),
            response_schema=response_model.__name__,
            latency_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            status=status,
            error=error,
            tokens=tokens,
        )

    def close(self) -> None:
        """Release the shared HTTP connection pool owned by the OpenAI client."""
        self._client.close()
