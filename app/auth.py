"""HS256 JWT authentication, role verification, and current actor resolution."""

import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db_session
from app.exceptions import DomainError
from app.models import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedActor:
    user_id: uuid.UUID
    role: UserRole
    email: str
    full_name: str
    practice_id: uuid.UUID | None
    member_id: uuid.UUID | None


def decode_and_verify_token(token: str) -> tuple[uuid.UUID, UserRole]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": ["sub", "role", "iss", "aud", "exp"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise DomainError(
            status=401,
            title="Token Expired",
            detail="Bearer token has expired.",
            code="AUTH_TOKEN_EXPIRED",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise DomainError(
            status=401,
            title="Invalid Token",
            detail="Bearer token signature or claims are invalid.",
            code="AUTH_INVALID_TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    sub_raw = payload.get("sub")
    role_raw = payload.get("role")
    try:
        user_id = uuid.UUID(str(sub_raw))
        role = UserRole(str(role_raw))
    except (ValueError, TypeError) as exc:
        raise DomainError(
            status=401,
            title="Invalid Token Claims",
            detail="Token subject or role claim is malformed.",
            code="AUTH_INVALID_CLAIMS",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return user_id, role


def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: Session = Depends(get_db_session),
) -> AuthenticatedActor:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise DomainError(
            status=401,
            title="Authentication Required",
            detail="Missing or malformed Bearer authorization header.",
            code="AUTH_MISSING_TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id, token_role = decode_and_verify_token(credentials.credentials)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise DomainError(
            status=401,
            title="Inactive or Unknown Principal",
            detail="Authenticated user does not exist or is inactive.",
            code="AUTH_USER_INACTIVE",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.role != token_role:
        raise DomainError(
            status=403,
            title="Role Mismatch",
            detail="Token role claim does not match persisted user role.",
            code="AUTH_ROLE_MISMATCH",
        )

    if user.role == UserRole.OFFICE and user.practice_id is None:
        raise DomainError(
            status=403,
            title="Invalid Office Binding",
            detail="Office user is not associated with a practice.",
            code="AUTH_ROLE_BINDING_INVALID",
        )

    if user.role == UserRole.MEMBER and user.member_id is None:
        raise DomainError(
            status=403,
            title="Invalid Member Binding",
            detail="Member user is not associated with a member record.",
            code="AUTH_ROLE_BINDING_INVALID",
        )

    return AuthenticatedActor(
        user_id=user.id,
        role=user.role,
        email=user.email,
        full_name=user.full_name,
        practice_id=user.practice_id,
        member_id=user.member_id,
    )


def require_role(required_role: UserRole):
    def _dependency(
        actor: AuthenticatedActor = Depends(get_current_actor),
    ) -> AuthenticatedActor:
        if actor.role != required_role:
            raise DomainError(
                status=403,
                title="Insufficient Role",
                detail=f"This endpoint requires role '{required_role.value}'.",
                code="AUTH_FORBIDDEN_ROLE",
            )
        return actor

    return _dependency
