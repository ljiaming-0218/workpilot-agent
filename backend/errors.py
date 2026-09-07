"""HTTP error envelopes without database credentials, SQL or submitted body values."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import (
    IntegrityError, InterfaceError, OperationalError, SQLAlchemyError, TimeoutError,
)
from starlette.exceptions import HTTPException

from backend.services.agent_service import AgentExecutionError
from backend.schemas.common import ErrorDetail, ErrorResponse, ValidationIssue


logger = logging.getLogger(__name__)


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    details: list[ValidationIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AgentExecutionError)
    async def agent_execution_error(request: Request, exc: AgentExecutionError) -> JSONResponse:
        logger.warning("AGENT_ERROR: request failed (%s)", type(exc.__cause__).__name__)
        return error_response(500, exc.code, "Agent execution failed.")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ValidationIssue(location=list(item["loc"]), message=item["msg"], type=item["type"])
            for item in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.", details=details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.warning("DATABASE_ERROR: request failed (%s)", type(exc).__name__)
        if isinstance(exc, IntegrityError):
            return error_response(409, "DATABASE_CONFLICT", "Data violates a database constraint.")
        if isinstance(exc, (OperationalError, InterfaceError, TimeoutError)):
            return error_response(503, "DATABASE_ERROR", "Database is temporarily unavailable.")
        return error_response(500, "DATABASE_ERROR", "Database operation failed.")
