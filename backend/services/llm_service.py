"""Single gateway for structured LLM calls."""

from typing import Protocol, TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from backend.config import Settings


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
            raise LLMServiceError("Structured LLM request failed.") from exc

        if message.refusal:
            raise LLMServiceError("The model refused the structured request.")
        if message.parsed is None:
            raise LLMServiceError("The model returned no structured result.")
        return message.parsed

    def close(self) -> None:
        """Release the shared HTTP connection pool owned by the OpenAI client."""
        self._client.close()
