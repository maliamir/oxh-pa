"""Office role HTTP endpoints."""

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from app.auth import AuthenticatedActor, require_role
from app.database import get_db_session
from app.errors import COMMON_PROBLEM_RESPONSES
from app.models import AuthorizationStatus, UserRole
from app.schemas import OfficeAuthorizationResponse, PriorAuthorizationCreateRequest
from app.service import (
    get_office_authorization,
    list_office_authorizations,
    submit_prior_authorization,
)

router = APIRouter(prefix="/v1/office", tags=["office"])


@router.post(
    "/prior-authorizations",
    response_model=OfficeAuthorizationResponse,
    status_code=201,
    summary="Submit a prior authorization request",
    description=(
        "Creates a new prior authorization for an actively linked member with active coverage "
        "on service_date (evaluated in the practice's local timezone from today through +1 calendar year). "
        "Requires an Idempotency-Key header: identical replays return HTTP 200 with the existing record; "
        "reusing a key with a modified body returns HTTP 409 (IDEMPOTENCY_KEY_CONFLICT). "
        "Validation and eligibility errors return HTTP 422 as application/problem+json."
    ),
    responses={
        200: {
            "model": OfficeAuthorizationResponse,
            "description": "Idempotent replay of an already-accepted submission.",
        },
        **COMMON_PROBLEM_RESPONSES,
    },
)
def create_prior_authorization_endpoint(
    payload: PriorAuthorizationCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="Practice-scoped unique key (1-128 chars) ensuring safe retries.",
    ),
    x_correlation_id: str | None = Header(
        default=None,
        alias="X-Correlation-ID",
        description="Optional client correlation identifier echoed back and searchable by support.",
    ),
    actor: AuthenticatedActor = Depends(require_role(UserRole.OFFICE)),
    db: Session = Depends(get_db_session),
) -> OfficeAuthorizationResponse:
    _ = x_correlation_id
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    corr_id = getattr(request.state, "correlation_id", None)
    result, created = submit_prior_authorization(
        db,
        actor=actor,
        idempotency_key=idempotency_key,
        payload=payload,
        request_id=req_id,
        correlation_id=corr_id,
    )
    response.status_code = 201 if created else 200
    return result


@router.get(
    "/prior-authorizations",
    response_model=list[OfficeAuthorizationResponse],
    summary="List prior authorizations for the caller's practice",
    description=(
        "Returns prior authorizations submitted by the authenticated office's practice, "
        "ordered newest first. Omits internal reviewer denial_reason notes."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def list_office_authorizations_endpoint(
    status: AuthorizationStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: AuthenticatedActor = Depends(require_role(UserRole.OFFICE)),
    db: Session = Depends(get_db_session),
) -> list[OfficeAuthorizationResponse]:
    return list_office_authorizations(db, actor=actor, status=status, limit=limit, offset=offset)


@router.get(
    "/prior-authorizations/{authorization_id}",
    response_model=OfficeAuthorizationResponse,
    summary="Get a single prior authorization for the caller's practice",
    description=(
        "Returns the practice-scoped projection for authorization_id. "
        "Returns HTTP 404 (AUTHORIZATION_NOT_FOUND) if the ID belongs to another practice."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def get_office_authorization_endpoint(
    authorization_id: uuid.UUID,
    actor: AuthenticatedActor = Depends(require_role(UserRole.OFFICE)),
    db: Session = Depends(get_db_session),
) -> OfficeAuthorizationResponse:
    return get_office_authorization(db, actor=actor, authorization_id=authorization_id)
