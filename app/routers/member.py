"""Member role HTTP endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import AuthenticatedActor, require_role
from app.database import get_db_session
from app.errors import COMMON_PROBLEM_RESPONSES
from app.models import UserRole
from app.schemas import MemberAuthorizationResponse
from app.service import get_member_authorization, list_member_authorizations

router = APIRouter(prefix="/v1/member", tags=["member"])


@router.get(
    "/prior-authorizations",
    response_model=list[MemberAuthorizationResponse],
    summary="List prior authorizations for the authenticated member",
    description=(
        "Returns the member's prior authorizations and procedure approval/denial outcomes. "
        "Clinical notes and internal reviewer denial reasoning are excluded."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def list_member_authorizations_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: AuthenticatedActor = Depends(require_role(UserRole.MEMBER)),
    db: Session = Depends(get_db_session),
) -> list[MemberAuthorizationResponse]:
    return list_member_authorizations(db, actor=actor, limit=limit, offset=offset)


@router.get(
    "/prior-authorizations/{authorization_id}",
    response_model=MemberAuthorizationResponse,
    summary="Get a single prior authorization for the authenticated member",
    description=(
        "Returns the member-safe projection for authorization_id. "
        "Returns HTTP 404 (AUTHORIZATION_NOT_FOUND) if the record belongs to another member."
    ),
    responses=COMMON_PROBLEM_RESPONSES,
)
def get_member_authorization_endpoint(
    authorization_id: uuid.UUID,
    actor: AuthenticatedActor = Depends(require_role(UserRole.MEMBER)),
    db: Session = Depends(get_db_session),
) -> MemberAuthorizationResponse:
    return get_member_authorization(db, actor=actor, authorization_id=authorization_id)
