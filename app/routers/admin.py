"""Admin role HTTP endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth import AuthenticatedActor, require_role
from app.database import get_db_session
from app.errors import COMMON_PROBLEM_RESPONSES
from app.models import AuthorizationStatus, UserRole
from app.schemas import (
    AdminAuthorizationResponse,
    AuditEventResponse,
    ReassignReviewerRequest,
    SubmissionAttemptResponse,
)
from app.service import (
    get_admin_authorization,
    get_submission_attempt,
    list_admin_authorizations,
    list_audit_events,
    reassign_reviewer,
    search_submission_attempts,
)

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get(
    "/prior-authorizations",
    response_model=list[AdminAuthorizationResponse],
    summary="List and filter all prior authorizations",
    description=(
        "Operations admin search across all practices, members, statuses, and assigned reviewers."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def list_admin_authorizations_endpoint(
    status: AuthorizationStatus | None = Query(default=None),
    practice_id: uuid.UUID | None = Query(default=None),
    member_id: uuid.UUID | None = Query(default=None),
    assigned_reviewer_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: AuthenticatedActor = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> list[AdminAuthorizationResponse]:
    _ = actor
    return list_admin_authorizations(
        db,
        status=status,
        practice_id=practice_id,
        member_id=member_id,
        assigned_reviewer_id=assigned_reviewer_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/prior-authorizations/{authorization_id}",
    response_model=AdminAuthorizationResponse,
    summary="Get full operational record for a prior authorization",
    description="Returns complete metadata, clinical note, procedures, and per-procedure decisions.",
    responses=COMMON_PROBLEM_RESPONSES,
)
def get_admin_authorization_endpoint(
    authorization_id: uuid.UUID,
    actor: AuthenticatedActor = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> AdminAuthorizationResponse:
    _ = actor
    return get_admin_authorization(db, authorization_id=authorization_id)


@router.post(
    "/prior-authorizations/{authorization_id}/reassign",
    response_model=AdminAuthorizationResponse,
    summary="Reassign an in-review prior authorization to another reviewer",
    description=(
        "Transfers ownership of an in_review authorization to another active, non-conflicted reviewer "
        "and records an immutable authorization.reassigned audit event."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def reassign_reviewer_endpoint(
    authorization_id: uuid.UUID,
    payload: ReassignReviewerRequest,
    request: Request,
    actor: AuthenticatedActor = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> AdminAuthorizationResponse:
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return reassign_reviewer(
        db,
        actor=actor,
        authorization_id=authorization_id,
        new_reviewer_id=payload.reviewer_id,
        request_id=req_id,
    )


@router.get(
    "/prior-authorizations/{authorization_id}/audit-events",
    response_model=list[AuditEventResponse],
    summary="List ordered append-only audit events for a prior authorization",
    description="Returns every lifecycle event (who did what, when, and under which request_id).",
    responses=COMMON_PROBLEM_RESPONSES,
)
def list_audit_events_endpoint(
    authorization_id: uuid.UUID,
    actor: AuthenticatedActor = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> list[AuditEventResponse]:
    _ = actor
    return list_audit_events(db, authorization_id=authorization_id)


@router.get(
    "/submission-attempts",
    response_model=list[SubmissionAttemptResponse],
    summary="Search submission attempts for support diagnostics",
    description=(
        "Searches non-PHI submission diagnostics across accepted submissions, replays, "
        "schema validation failures, idempotency conflicts, and business rule rejections."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def search_submission_attempts_endpoint(
    practice_id: uuid.UUID | None = Query(default=None),
    idempotency_key: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    accepted: bool | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: AuthenticatedActor = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> list[SubmissionAttemptResponse]:
    _ = actor
    return search_submission_attempts(
        db,
        practice_id=practice_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        accepted=accepted,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/submission-attempts/{request_id}",
    response_model=SubmissionAttemptResponse,
    summary="Look up a submission attempt by server request_id or client correlation_id",
    description="Returns non-PHI submission outcome and error codes for support desk troubleshooting.",
    responses=COMMON_PROBLEM_RESPONSES,
)
def get_submission_attempt_endpoint(
    request_id: str,
    actor: AuthenticatedActor = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> SubmissionAttemptResponse:
    _ = actor
    return get_submission_attempt(db, lookup_id=request_id)
