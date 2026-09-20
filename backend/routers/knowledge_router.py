"""Read-only knowledge inventory for the Business Data console."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.schemas.common import ErrorResponse, Page, PaginationParams, SuccessResponse
from backend.schemas.knowledge import KnowledgeRead
from backend.services.knowledge_service import list_knowledge_page


router = APIRouter(
    prefix="/knowledge",
    tags=["knowledge"],
    responses={
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


@router.get("", response_model=SuccessResponse[Page[KnowledgeRead]])
def get_knowledge(
    pagination: Annotated[PaginationParams, Query()],
    session: Annotated[Session, Depends(get_db)],
) -> SuccessResponse[Page[KnowledgeRead]]:
    return SuccessResponse(data=list_knowledge_page(session, pagination))
