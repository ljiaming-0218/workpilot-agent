"""Human collaboration uses the same object authorization as history and Trace."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.models.agent_run import AgentRun
from backend.models.durable_job import DurableJobAttempt
from backend.models.investigation import IncidentSubmission, InvestigationReview
from backend.models.team import TeamMember
from backend.schemas.common import SuccessResponse
from backend.schemas.investigation import ReviewUpdate
from backend.services.access_service import current_team, run_access_filter
from backend.services.durable_access import require_public_run
from backend.services.investigation_service import collaboration_view, update_review


router = APIRouter(tags=["investigations"])
Db = Annotated[Session, Depends(get_db)]
Member = Annotated[TeamMember, Depends(current_team)]


def require_p4(request: Request) -> None:
    if not getattr(request.app.state, "p4_schema_present", False):
        raise HTTPException(503, "请先执行 P4 增量迁移并重启 API")


@router.get("/agent/runs/{run_id}/collaboration", dependencies=[Depends(require_p4)])
def get_collaboration(session: Db, member: Member, run_id: int = Path(ge=1)):
    run = require_public_run(session, run_id, member.team_id)
    return SuccessResponse(data=collaboration_view(session, run))


@router.patch("/agent/runs/{run_id}/collaboration", dependencies=[Depends(require_p4)])
def patch_collaboration(payload: ReviewUpdate, session: Db, member: Member, run_id: int = Path(ge=1)):
    run = require_public_run(session, run_id, member.team_id)
    return SuccessResponse(data=update_review(session, run, member, payload))


@router.get("/investigations/summary", dependencies=[Depends(require_p4)])
def investigation_summary(session: Db, member: Member):
    """Execution and human feedback counts, grouped by declared data provenance."""
    hidden = exists(select(DurableJobAttempt.id).where(DurableJobAttempt.execution_run_id == AgentRun.id))
    kind = func.coalesce(IncidentSubmission.source_kind, "unspecified")
    statement = select(kind, AgentRun.status, func.coalesce(InvestigationReview.feedback, "unreviewed"),
                       func.count()).select_from(AgentRun).outerjoin(
        IncidentSubmission, IncidentSubmission.agent_run_id == AgentRun.id,
    ).outerjoin(InvestigationReview, InvestigationReview.agent_run_id == AgentRun.id).where(
        run_access_filter(member.team_id), ~hidden,
    ).group_by(kind, AgentRun.status, InvestigationReview.feedback)
    rows = [{"source_kind": k, "execution_status": s.value, "feedback": f, "count": n}
            for k, s, f, n in session.execute(statement)]
    return SuccessResponse(data={"groups": rows, "source_kind_basis": "submitter_declared",
                                 "metric": "execution_and_feedback_counts", "root_cause_accuracy": None})
