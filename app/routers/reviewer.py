"""Reviewer role HTTP endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth import AuthenticatedActor, require_role
from app.database import get_db_session
from app.errors import COMMON_PROBLEM_RESPONSES
from app.models import AuthorizationStatus, UserRole
from app.schemas import AuthorizationDecisionRequest, ReviewerAuthorizationResponse
from app.service import (
    claim_authorization,
    decide_authorization,
    get_reviewer_authorization,
    list_reviewer_assigned,
    list_reviewer_queue,
)

router = APIRouter(prefix="/v1/reviewer", tags=["reviewer"])


@router.get(
    "/queue",
    response_model=list[ReviewerAuthorizationResponse],
    summary="List unclaimed prior authorizations available to review",
    description=(
        "Returns submitted requests in FIFO order, automatically excluding any member "
        "with whom the authenticated reviewer has a treating relationship. "
        "Clinical notes are omitted until a reviewer claims the request."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def get_reviewer_queue_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: AuthenticatedActor = Depends(require_role(UserRole.REVIEWER)),
    db: Session = Depends(get_db_session),
) -> list[ReviewerAuthorizationResponse]:
    return list_reviewer_queue(db, actor=actor, limit=limit, offset=offset)


@router.get(
    "/assigned",
    response_model=list[ReviewerAuthorizationResponse],
    summary="List prior authorizations assigned to the authenticated reviewer",
    description=(
        "Returns prior authorizations assigned to the authenticated reviewer (defaults to status=in_review), "
        "including clinical notes and procedure details."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def get_reviewer_assigned_endpoint(
    status: AuthorizationStatus | None = Query(default=AuthorizationStatus.IN_REVIEW),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: AuthenticatedActor = Depends(require_role(UserRole.REVIEWER)),
    db: Session = Depends(get_db_session),
) -> list[ReviewerAuthorizationResponse]:
    return list_reviewer_assigned(db, actor=actor, status=status, limit=limit, offset=offset)


@router.post(
    "/prior-authorizations/{authorization_id}/claim",
    response_model=ReviewerAuthorizationResponse,
    summary="Atomically claim a submitted prior authorization",
    description=(
        "Transitions a submitted authorization to in_review and assigns it to the caller. "
        "Returns HTTP 404 if the authorization does not exist or involves a treated patient; "
        "returns HTTP 409 (AUTHORIZATION_CLAIM_CONFLICT) if another reviewer claimed it first."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def claim_authorization_endpoint(
    authorization_id: uuid.UUID,
    request: Request,
    actor: AuthenticatedActor = Depends(require_role(UserRole.REVIEWER)),
    db: Session = Depends(get_db_session),
) -> ReviewerAuthorizationResponse:
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return claim_authorization(
        db,
        actor=actor,
        authorization_id=authorization_id,
        request_id=req_id,
    )


@router.get(
    "/prior-authorizations/{authorization_id}",
    response_model=ReviewerAuthorizationResponse,
    summary="Get a prior authorization assigned to the caller",
    description=(
        "Returns full clinical details for an authorization currently assigned to the caller. "
        "Returns HTTP 404 if unassigned or assigned to a different reviewer."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def get_reviewer_authorization_endpoint(
    authorization_id: uuid.UUID,
    actor: AuthenticatedActor = Depends(require_role(UserRole.REVIEWER)),
    db: Session = Depends(get_db_session),
) -> ReviewerAuthorizationResponse:
    return get_reviewer_authorization(db, actor=actor, authorization_id=authorization_id)


@router.post(
    "/prior-authorizations/{authorization_id}/decision",
    response_model=ReviewerAuthorizationResponse,
    summary="Submit final per-procedure decisions for an in-review request",
    description=(
        "Atomically records an approved or denied decision for every procedure on the request "
        "(denial_reason required when outcome='denied', forbidden when outcome='approved') "
        "and transitions the authorization to decided."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def decide_authorization_endpoint(
    authorization_id: uuid.UUID,
    payload: AuthorizationDecisionRequest,
    request: Request,
    actor: AuthenticatedActor = Depends(require_role(UserRole.REVIEWER)),
    db: Session = Depends(get_db_session),
) -> ReviewerAuthorizationResponse:
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return decide_authorization(
        db,
        actor=actor,
        authorization_id=authorization_id,
        payload=payload,
        request_id=req_id,
    )
