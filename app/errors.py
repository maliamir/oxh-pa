"""RFC 9457 Problem Details error model and exception handling."""

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.database import get_session_factory
from app.exceptions import DomainError, ProblemFieldError
from app.models import SubmissionAttempt, User

logger = logging.getLogger("oxh.errors")


class ProblemDetail(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None
    code: str
    request_id: str | None = None
    correlation_id: str | None = None
    errors: list[ProblemFieldError] = Field(default_factory=list)


COMMON_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ProblemDetail,
        "description": "Malformed request or missing required header (e.g. MISSING_IDEMPOTENCY_KEY).",
    },
    401: {
        "model": ProblemDetail,
        "description": "Missing, expired, or invalid Bearer JWT (e.g. AUTH_MISSING_TOKEN, AUTH_TOKEN_EXPIRED).",
    },
    403: {
        "model": ProblemDetail,
        "description": "Authenticated caller lacks required role or permission (e.g. AUTH_FORBIDDEN_ROLE).",
    },
    404: {
        "model": ProblemDetail,
        "description": "Resource does not exist or is hidden by tenant/conflict isolation (AUTHORIZATION_NOT_FOUND).",
    },
    409: {
        "model": ProblemDetail,
        "description": "State or idempotency conflict (e.g. IDEMPOTENCY_KEY_CONFLICT, AUTHORIZATION_CLAIM_CONFLICT).",
    },
    422: {
        "model": ProblemDetail,
        "description": "Schema or clinical/eligibility validation error (VALIDATION_FAILED, BUSINESS_RULE_VIOLATION).",
    },
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    code: str,
    errors: list[ProblemFieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    req_id = _request_id(request)
    corr_id = _correlation_id(request)
    payload = ProblemDetail(
        type=f"https://oxbridgehealth.example/problems/{code.lower().replace('_', '-')}",
        title=title,
        status=status,
        detail=detail,
        instance=str(request.url.path),
        code=code,
        request_id=req_id,
        correlation_id=corr_id,
        errors=errors or [],
    )
    resp_headers = dict(headers or {})
    if req_id:
        resp_headers["X-Request-ID"] = req_id
    if corr_id:
        resp_headers["X-Correlation-ID"] = corr_id
    return JSONResponse(
        status_code=status,
        content=payload.model_dump(mode="json"),
        headers=resp_headers,
        media_type="application/problem+json",
    )


def _format_loc(loc: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for item in loc:
        if item in ("body", "query", "path", "header"):
            continue
        parts.append(str(item))
    return ".".join(parts) if parts else "body"


def _record_schema_submission_attempt(request: Request, error_codes: list[str]) -> None:
    if request.method != "POST" or request.url.path != "/v1/office/prior-authorizations":
        return

    req_id = _request_id(request) or str(uuid.uuid4())
    corr_id = _correlation_id(request)
    raw_idem = request.headers.get("Idempotency-Key", "").strip()
    idem_key = raw_idem[:128] if raw_idem else None

    actor_id: uuid.UUID | None = None
    practice_id: uuid.UUID | None = None

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            try:
                from app.auth import decode_and_verify_token

                user_id, _ = decode_and_verify_token(token)
                actor_id = user_id
            except DomainError:
                actor_id = None

    try:
        session_factory = get_session_factory()
        with session_factory() as db:
            if actor_id is not None:
                user = db.get(User, actor_id)
                if user is not None:
                    practice_id = user.practice_id
            db.add(
                SubmissionAttempt(
                    request_id=req_id,
                    correlation_id=corr_id,
                    actor_id=actor_id,
                    practice_id=practice_id,
                    idempotency_key=idem_key,
                    accepted=False,
                    error_codes=error_codes,
                    authorization_id=None,
                )
            )
            db.commit()
    except Exception:
        logger.warning("Failed to persist schema submission diagnostic for request_id=%s", req_id)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return problem_response(
            request,
            status=exc.status,
            title=exc.title,
            detail=exc.detail,
            code=exc.code,
            errors=exc.errors,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors: list[ProblemFieldError] = []
        for err in exc.errors():
            loc = err.get("loc", ())
            field_path = _format_loc(loc if isinstance(loc, tuple) else (loc,))
            raw_type = str(err.get("type", "validation_error")).upper()
            msg = str(err.get("msg", "Invalid value"))
            field_errors.append(
                ProblemFieldError(
                    code=f"SCHEMA_{raw_type}",
                    field=field_path,
                    message=msg,
                )
            )
        _record_schema_submission_attempt(request, [e.code for e in field_errors])
        return problem_response(
            request,
            status=422,
            title="Validation Failed",
            detail="One or more request fields failed schema validation.",
            code="VALIDATION_FAILED",
            errors=field_errors,
        )
