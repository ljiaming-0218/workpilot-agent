"""Single gateway for structured LLM calls."""

import json
import logging
from time import perf_counter_ns
from typing import Any, Protocol, TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from backend.config import Settings
from backend.utils.trace_context import emit_llm_trace


StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)
logger = logging.getLogger(__name__)


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
        self._extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}}
            if not settings.llm_enable_thinking
            else None
        )
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
        schema_prompt = _with_response_schema(system_prompt, response_model)
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": schema_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                extra_body=self._extra_body,
            )
            message = completion.choices[0].message
        except (OpenAIError, IndexError, ValueError) as exc:
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

        content = message.content
        try:
            payload = _extract_json_object(content)
            parsed = response_model.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "LLM_SCHEMA_VALIDATION_FAILED schema=%s errors=%s",
                response_model.__name__,
                _summarize_validation_errors(exc),
            )
            self._emit_trace(
                response_model,
                started_ns,
                status="failed",
                error="SCHEMA_VALIDATION_ERROR",
                completion=completion,
            )
            raise LLMServiceError(
                "The model response failed schema validation."
            ) from exc
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "LLM_JSON_PARSE_FAILED schema=%s response_length=%s error=%s",
                response_model.__name__,
                len(content) if isinstance(content, str) else None,
                str(exc),
            )
            self._emit_trace(
                response_model,
                started_ns,
                status="failed",
                error="JSON_PARSE_ERROR",
                completion=completion,
            )
            raise LLMServiceError(
                "The model returned invalid structured JSON."
            ) from exc

        self._emit_trace(
            response_model,
            started_ns,
            status="success",
            error=None,
            completion=completion,
        )
        return parsed

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


def _with_response_schema(
    system_prompt: str,
    response_model: type[BaseModel],
) -> str:
    """Append the exact Pydantic schema for providers that ignore native schemas."""
    schema = json.dumps(
        response_model.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"{system_prompt}\n\n"
        "Return exactly one JSON object that conforms to the following JSON Schema. "
        "Use the exact field names, include every required field, add no extra fields, "
        "and do not wrap the object in Markdown or code fences.\n"
        f"JSON_SCHEMA: {schema}"
    )


def _extract_json_object(content: str | None) -> dict[str, Any]:
    """Parse a JSON object, tolerating code fences or short surrounding text."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("The model returned empty content.")

    text = content.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        payload = None
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is None:
            raise ValueError("No valid JSON object was found in the model response.")

    if not isinstance(payload, dict):
        raise ValueError("The structured model response must be a JSON object.")
    return payload


def _summarize_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    """Keep diagnostics useful without logging model-returned business values."""
    return [
        {
            "type": str(error.get("type", "unknown")),
            "location": ".".join(str(part) for part in error.get("loc", ())),
        }
        for error in exc.errors(include_url=False, include_input=False)[:10]
    ]
