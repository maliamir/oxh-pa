"""Workflow, business rules, transaction boundaries, and role-specific projections."""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, or_, select, text, update
from sqlalchemy.orm import Session, selectinload

from app.auth import AuthenticatedActor
from app.database import get_session_factory
from app.exceptions import DomainError, ProblemFieldError
from app.models import (
    AuditEvent,
    AuthorizationProcedure,
    AuthorizationStatus,
    Coverage,
    Practice,
    PracticeMember,
    PriorAuthorization,
    ProcedureDecision,
    ReviewerPatient,
    SubmissionAttempt,
    User,
    UserRole,
)
from app.schemas import (
    AdminAuthorizationResponse,
    AdminProcedureDetail,
    AuditEventResponse,
    AuthorizationDecisionRequest,
    MemberAuthorizationResponse,
    MemberProcedureOutcome,
    OfficeAuthorizationResponse,
    OfficeProcedureItem,
    PriorAuthorizationCreateRequest,
    ReviewerAuthorizationResponse,
    ReviewerProcedureDetail,
    SubmissionAttemptResponse,
)


def _one_calendar_year_ahead(today: date) -> date:
    try:
        return today.replace(year=today.year + 1)
    except ValueError:
        return today.replace(year=today.year + 1, month=2, day=28)


def _compute_payload_hash(payload: PriorAuthorizationCreateRequest) -> str:
    canonical = {
        "member_id": str(payload.member_id),
        "diagnosis_code": payload.diagnosis_code,
        "service_date": payload.service_date.isoformat(),
        "clinical_note": payload.clinical_note,
        "procedures": [
            {
                "code_system": proc.code_system.value,
                "code": proc.code,
                "units": proc.units,
            }
            for proc in payload.procedures
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _persist_submission_attempt_independent(
    *,
    request_id: str,
    correlation_id: str | None,
    actor: AuthenticatedActor,
    practice_id: uuid.UUID,
    idempotency_key: str | None,
    accepted: bool,
    error_codes: list[str],
    authorization_id: uuid.UUID | None = None,
) -> None:
    """Commit submission diagnostics in an independent session so outer rollbacks never erase it."""
    factory = get_session_factory()
    with factory() as diag_db:
        diag_db.add(
            SubmissionAttempt(
                request_id=request_id,
                correlation_id=correlation_id,
                actor_id=actor.user_id,
                practice_id=practice_id,
                idempotency_key=idempotency_key,
                accepted=accepted,
                error_codes=error_codes,
                authorization_id=authorization_id,
            )
        )
        diag_db.commit()


def _append_audit_event(
    db: Session,
    *,
    authorization_id: uuid.UUID,
    actor: AuthenticatedActor,
    action: str,
    request_id: str,
    details: dict[str, Any],
) -> None:
    db.add(
        AuditEvent(
            authorization_id=authorization_id,
            actor_id=actor.user_id,
            actor_role=actor.role,
            action=action,
            request_id=request_id,
            details=details,
        )
    )


def _acquire_practice_idempotency_lock(
    db: Session, practice_id: uuid.UUID, idempotency_key: str
) -> None:
    lock_material = f"{practice_id}:{idempotency_key}".encode()
    digest = hashlib.sha256(lock_material).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})


def _load_authorization_with_procedures(
    db: Session, authorization_id: uuid.UUID
) -> PriorAuthorization | None:
    stmt = (
        select(PriorAuthorization)
        .options(
            selectinload(PriorAuthorization.procedures).selectinload(
                AuthorizationProcedure.decision
            )
        )
        .where(PriorAuthorization.id == authorization_id)
    )
    return db.execute(stmt).scalar_one_or_none()


def submit_prior_authorization(
    db: Session,
    *,
    actor: AuthenticatedActor,
    idempotency_key: str | None,
    payload: PriorAuthorizationCreateRequest,
    request_id: str,
    correlation_id: str | None = None,
) -> tuple[OfficeAuthorizationResponse, bool]:
    if actor.practice_id is None:
        raise DomainError(
            status=403,
            title="Practice Required",
            detail="Office user is not bound to a practice.",
            code="OFFICE_PRACTICE_MISSING",
        )
    practice_id = actor.practice_id

    clean_key = (idempotency_key or "").strip()
    if not clean_key or len(clean_key) > 128:
        _persist_submission_attempt_independent(
            request_id=request_id,
            correlation_id=correlation_id,
            actor=actor,
            practice_id=practice_id,
            idempotency_key=clean_key[:128] if clean_key else None,
            accepted=False,
            error_codes=["MISSING_IDEMPOTENCY_KEY"],
        )
        raise DomainError(
            status=400,
            title="Idempotency-Key Required",
            detail="A nonblank Idempotency-Key header (1-128 characters) is required.",
            code="MISSING_IDEMPOTENCY_KEY",
            errors=[
                ProblemFieldError(
                    code="MISSING_IDEMPOTENCY_KEY",
                    field="headers.Idempotency-Key",
                    message="Idempotency-Key header is required.",
                )
            ],
        )

    payload_hash = _compute_payload_hash(payload)

    with db.begin_nested():
        _acquire_practice_idempotency_lock(db, practice_id, clean_key)

        existing_stmt = (
            select(PriorAuthorization)
            .options(
                selectinload(PriorAuthorization.procedures).selectinload(
                    AuthorizationProcedure.decision
                )
            )
            .where(
                PriorAuthorization.practice_id == practice_id,
                PriorAuthorization.idempotency_key == clean_key,
            )
        )
        existing = db.execute(existing_stmt).scalar_one_or_none()

        if existing is not None:
            if existing.request_payload_hash != payload_hash:
                _persist_submission_attempt_independent(
                    request_id=request_id,
                    correlation_id=correlation_id,
                    actor=actor,
                    practice_id=practice_id,
                    idempotency_key=clean_key,
                    accepted=False,
                    error_codes=["IDEMPOTENCY_KEY_CONFLICT"],
                    authorization_id=existing.id,
                )
                raise DomainError(
                    status=409,
                    title="Idempotency Key Conflict",
                    detail="Idempotency-Key was already used with a different request payload.",
                    code="IDEMPOTENCY_KEY_CONFLICT",
                    errors=[
                        ProblemFieldError(
                            code="IDEMPOTENCY_KEY_CONFLICT",
                            field="headers.Idempotency-Key",
                            message="Idempotency-Key cannot be reused with different body.",
                        )
                    ],
                )

            _persist_submission_attempt_independent(
                request_id=request_id,
                correlation_id=correlation_id,
                actor=actor,
                practice_id=practice_id,
                idempotency_key=clean_key,
                accepted=True,
                error_codes=[],
                authorization_id=existing.id,
            )
            return project_office_authorization(existing), False

        practice = db.get(Practice, practice_id)
        if practice is None:
            raise DomainError(
                status=403,
                title="Practice Not Found",
                detail="Practice does not exist.",
                code="PRACTICE_NOT_FOUND",
            )

        practice_tz = ZoneInfo(practice.timezone)
        practice_today = datetime.now(practice_tz).date()
        max_service_date = _one_calendar_year_ahead(practice_today)

        business_errors: list[ProblemFieldError] = []

        if payload.service_date < practice_today:
            business_errors.append(
                ProblemFieldError(
                    code="SERVICE_DATE_IN_PAST",
                    field="service_date",
                    message=(
                        f"service_date ({payload.service_date.isoformat()}) cannot precede "
                        f"practice local date ({practice_today.isoformat()})."
                    ),
                )
            )
        elif payload.service_date > max_service_date:
            business_errors.append(
                ProblemFieldError(
                    code="SERVICE_DATE_TOO_FAR",
                    field="service_date",
                    message=(
                        f"service_date ({payload.service_date.isoformat()}) cannot be more than "
                        f"one calendar year ahead ({max_service_date.isoformat()})."
                    ),
                )
            )

        link_stmt = select(PracticeMember.id).where(
            PracticeMember.practice_id == practice_id,
            PracticeMember.member_id == payload.member_id,
            PracticeMember.active.is_(True),
        )
        is_linked = db.execute(link_stmt).scalar_one_or_none() is not None
        if not is_linked:
            business_errors.append(
                ProblemFieldError(
                    code="MEMBER_NOT_LINKED",
                    field="member_id",
                    message="Member is not actively linked to the submitting practice.",
                )
            )
        else:
            # Only check member coverage once practice linkage is verified to avoid leaking
            # coverage status for members outside the submitting practice.
            cov_stmt = select(Coverage.id).where(
                Coverage.member_id == payload.member_id,
                Coverage.is_active.is_(True),
                Coverage.effective_start <= payload.service_date,
                Coverage.effective_end >= payload.service_date,
            )
            has_coverage = db.execute(cov_stmt).first() is not None
            if not has_coverage:
                business_errors.append(
                    ProblemFieldError(
                        code="NO_ACTIVE_COVERAGE",
                        field="service_date",
                        message="Member does not have active coverage on the requested service_date.",
                    )
                )

        if business_errors:
            err_codes = [e.code for e in business_errors]
            _persist_submission_attempt_independent(
                request_id=request_id,
                correlation_id=correlation_id,
                actor=actor,
                practice_id=practice_id,
                idempotency_key=clean_key,
                accepted=False,
                error_codes=err_codes,
            )
            raise DomainError(
                status=422,
                title="Prior Authorization Business Rule Violation",
                detail="One or more clinical or eligibility rules failed.",
                code="BUSINESS_RULE_VIOLATION",
                errors=business_errors,
            )

        auth = PriorAuthorization(
            practice_id=practice_id,
            member_id=payload.member_id,
            submitted_by_user_id=actor.user_id,
            idempotency_key=clean_key,
            request_payload_hash=payload_hash,
            diagnosis_code=payload.diagnosis_code,
            service_date=payload.service_date,
            clinical_note=payload.clinical_note,
            status=AuthorizationStatus.SUBMITTED,
        )
        for item in payload.procedures:
            auth.procedures.append(
                AuthorizationProcedure(
                    code_system=item.code_system,
                    code=item.code,
                    units=item.units,
                )
            )
        db.add(auth)
        db.flush()

        _append_audit_event(
            db,
            authorization_id=auth.id,
            actor=actor,
            action="authorization.submitted",
            request_id=request_id,
            details={
                "practice_id": str(practice_id),
                "member_id": str(payload.member_id),
                "procedure_count": len(auth.procedures),
                "service_date": payload.service_date.isoformat(),
                **({"correlation_id": correlation_id} if correlation_id else {}),
            },
        )

    db.commit()
    _persist_submission_attempt_independent(
        request_id=request_id,
        correlation_id=correlation_id,
        actor=actor,
        practice_id=practice_id,
        idempotency_key=clean_key,
        accepted=True,
        error_codes=[],
        authorization_id=auth.id,
    )
    created = _load_authorization_with_procedures(db, auth.id)
    assert created is not None
    return project_office_authorization(created), True


def list_office_authorizations(
    db: Session,
    *,
    actor: AuthenticatedActor,
    status: AuthorizationStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[OfficeAuthorizationResponse]:
    stmt = (
        select(PriorAuthorization)
        .options(
            selectinload(PriorAuthorization.procedures).selectinload(
                AuthorizationProcedure.decision
            )
        )
        .where(PriorAuthorization.practice_id == actor.practice_id)
    )
    if status is not None:
        stmt = stmt.where(PriorAuthorization.status == status)
    stmt = stmt.order_by(PriorAuthorization.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [project_office_authorization(row) for row in rows]


def get_office_authorization(
    db: Session, *, actor: AuthenticatedActor, authorization_id: uuid.UUID
) -> OfficeAuthorizationResponse:
    auth = _load_authorization_with_procedures(db, authorization_id)
    if auth is None or auth.practice_id != actor.practice_id:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )
    return project_office_authorization(auth)


def list_member_authorizations(
    db: Session,
    *,
    actor: AuthenticatedActor,
    limit: int = 50,
    offset: int = 0,
) -> list[MemberAuthorizationResponse]:
    stmt = (
        select(PriorAuthorization)
        .options(
            selectinload(PriorAuthorization.procedures).selectinload(
                AuthorizationProcedure.decision
            )
        )
        .where(PriorAuthorization.member_id == actor.member_id)
        .order_by(PriorAuthorization.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(stmt).scalars().all()
    return [project_member_authorization(row) for row in rows]


def get_member_authorization(
    db: Session, *, actor: AuthenticatedActor, authorization_id: uuid.UUID
) -> MemberAuthorizationResponse:
    auth = _load_authorization_with_procedures(db, authorization_id)
    if auth is None or auth.member_id != actor.member_id:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )
    return project_member_authorization(auth)


def list_reviewer_queue(
    db: Session,
    *,
    actor: AuthenticatedActor,
    limit: int = 50,
    offset: int = 0,
) -> list[ReviewerAuthorizationResponse]:
    conflict_subq = exists(
        select(ReviewerPatient.id).where(
            ReviewerPatient.reviewer_user_id == actor.user_id,
            ReviewerPatient.member_id == PriorAuthorization.member_id,
        )
    )
    stmt = (
        select(PriorAuthorization)
        .options(
            selectinload(PriorAuthorization.procedures).selectinload(
                AuthorizationProcedure.decision
            )
        )
        .where(
            PriorAuthorization.status == AuthorizationStatus.SUBMITTED,
            ~conflict_subq,
        )
        .order_by(PriorAuthorization.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(stmt).scalars().all()
    # Minimum necessary PHI: clinical_note is withheld until a reviewer claims the case.
    return [project_reviewer_authorization(row, include_clinical_note=False) for row in rows]


def list_reviewer_assigned(
    db: Session,
    *,
    actor: AuthenticatedActor,
    status: AuthorizationStatus | None = AuthorizationStatus.IN_REVIEW,
    limit: int = 50,
    offset: int = 0,
) -> list[ReviewerAuthorizationResponse]:
    stmt = (
        select(PriorAuthorization)
        .options(
            selectinload(PriorAuthorization.procedures).selectinload(
                AuthorizationProcedure.decision
            )
        )
        .where(PriorAuthorization.assigned_reviewer_id == actor.user_id)
    )
    if status is not None:
        stmt = stmt.where(PriorAuthorization.status == status)
    stmt = stmt.order_by(PriorAuthorization.claimed_at.desc(), PriorAuthorization.created_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [project_reviewer_authorization(row, include_clinical_note=True) for row in rows]


def _has_reviewer_conflict(db: Session, reviewer_id: uuid.UUID, member_id: uuid.UUID) -> bool:
    stmt = select(ReviewerPatient.id).where(
        ReviewerPatient.reviewer_user_id == reviewer_id,
        ReviewerPatient.member_id == member_id,
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def claim_authorization(
    db: Session,
    *,
    actor: AuthenticatedActor,
    authorization_id: uuid.UUID,
    request_id: str,
) -> ReviewerAuthorizationResponse:
    auth = _load_authorization_with_procedures(db, authorization_id)
    if auth is None:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )

    if _has_reviewer_conflict(db, actor.user_id, auth.member_id):
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )

    now = datetime.now(UTC)
    claim_stmt = (
        update(PriorAuthorization)
        .where(
            and_(
                PriorAuthorization.id == authorization_id,
                PriorAuthorization.status == AuthorizationStatus.SUBMITTED,
                PriorAuthorization.assigned_reviewer_id.is_(None),
            )
        )
        .values(
            status=AuthorizationStatus.IN_REVIEW,
            assigned_reviewer_id=actor.user_id,
            claimed_at=now,
            updated_at=now,
        )
        .returning(PriorAuthorization.id)
    )
    claimed_id = db.execute(claim_stmt).scalar_one_or_none()
    if claimed_id is None:
        db.rollback()
        raise DomainError(
            status=409,
            title="Authorization Cannot Be Claimed",
            detail="Prior authorization is no longer available in the submitted queue.",
            code="AUTHORIZATION_CLAIM_CONFLICT",
        )

    _append_audit_event(
        db,
        authorization_id=authorization_id,
        actor=actor,
        action="authorization.claimed",
        request_id=request_id,
        details={"assigned_reviewer_id": str(actor.user_id)},
    )
    db.commit()

    refreshed = _load_authorization_with_procedures(db, authorization_id)
    assert refreshed is not None
    return project_reviewer_authorization(refreshed, include_clinical_note=True)


def get_reviewer_authorization(
    db: Session, *, actor: AuthenticatedActor, authorization_id: uuid.UUID
) -> ReviewerAuthorizationResponse:
    auth = _load_authorization_with_procedures(db, authorization_id)
    if auth is None or auth.assigned_reviewer_id != actor.user_id:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )
    return project_reviewer_authorization(auth, include_clinical_note=True)


def decide_authorization(
    db: Session,
    *,
    actor: AuthenticatedActor,
    authorization_id: uuid.UUID,
    payload: AuthorizationDecisionRequest,
    request_id: str,
) -> ReviewerAuthorizationResponse:
    stmt = (
        select(PriorAuthorization)
        .options(
            selectinload(PriorAuthorization.procedures).selectinload(
                AuthorizationProcedure.decision
            )
        )
        .where(PriorAuthorization.id == authorization_id)
        .with_for_update()
    )
    auth = db.execute(stmt).scalar_one_or_none()
    if auth is None or auth.assigned_reviewer_id != actor.user_id:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )

    if auth.status != AuthorizationStatus.IN_REVIEW:
        raise DomainError(
            status=409,
            title="Invalid Authorization State",
            detail=f"Cannot record decision when authorization status is '{auth.status.value}'.",
            code="INVALID_AUTHORIZATION_STATE",
        )

    if _has_reviewer_conflict(db, actor.user_id, auth.member_id):
        raise DomainError(
            status=403,
            title="Reviewer Conflict of Interest",
            detail="Reviewer has a treating relationship with this member and cannot decide.",
            code="REVIEWER_PATIENT_CONFLICT",
        )

    expected_proc_ids = {proc.id for proc in auth.procedures}
    submitted_proc_ids = [item.procedure_id for item in payload.decisions]

    if len(submitted_proc_ids) != len(set(submitted_proc_ids)):
        raise DomainError(
            status=422,
            title="Duplicate Procedure Decision",
            detail="Each procedure may only be decided once.",
            code="DUPLICATE_PROCEDURE_DECISION",
            errors=[
                ProblemFieldError(
                    code="DUPLICATE_PROCEDURE_DECISION",
                    field="decisions",
                    message="Duplicate procedure_id entries are not allowed.",
                )
            ],
        )

    submitted_set = set(submitted_proc_ids)
    unknown_ids = submitted_set - expected_proc_ids
    missing_ids = expected_proc_ids - submitted_set

    if unknown_ids or missing_ids:
        errors: list[ProblemFieldError] = []
        for uid in sorted(str(x) for x in unknown_ids):
            errors.append(
                ProblemFieldError(
                    code="UNKNOWN_PROCEDURE_ID",
                    field="decisions.procedure_id",
                    message=f"Procedure '{uid}' does not belong to this authorization.",
                )
            )
        for mid in sorted(str(x) for x in missing_ids):
            errors.append(
                ProblemFieldError(
                    code="MISSING_PROCEDURE_DECISION",
                    field="decisions",
                    message=f"Decision is required for procedure '{mid}'.",
                )
            )
        raise DomainError(
            status=422,
            title="Incomplete or Invalid Procedure Decisions",
            detail="Exactly one decision is required for every procedure on the authorization.",
            code="INCOMPLETE_PROCEDURE_DECISIONS",
            errors=errors,
        )

    for proc in auth.procedures:
        if proc.decision is not None:
            raise DomainError(
                status=409,
                title="Decision Already Recorded",
                detail="One or more procedures already have a recorded decision.",
                code="DECISION_ALREADY_EXISTS",
            )

    now = datetime.now(UTC)
    for item in payload.decisions:
        db.add(
            ProcedureDecision(
                procedure_id=item.procedure_id,
                authorization_id=auth.id,
                outcome=item.outcome,
                denial_reason=item.denial_reason,
                decided_by_user_id=actor.user_id,
                decided_at=now,
            )
        )

    auth.status = AuthorizationStatus.DECIDED
    auth.decided_by_user_id = actor.user_id
    auth.decided_at = now
    auth.updated_at = now

    _append_audit_event(
        db,
        authorization_id=auth.id,
        actor=actor,
        action="authorization.decided",
        request_id=request_id,
        details={
            "decided_by_user_id": str(actor.user_id),
            "outcomes": [
                {"procedure_id": str(d.procedure_id), "outcome": d.outcome.value}
                for d in payload.decisions
            ],
        },
    )

    db.commit()
    refreshed = _load_authorization_with_procedures(db, auth.id)
    assert refreshed is not None
    return project_reviewer_authorization(refreshed, include_clinical_note=True)


def list_admin_authorizations(
    db: Session,
    *,
    status: AuthorizationStatus | None = None,
    practice_id: uuid.UUID | None = None,
    member_id: uuid.UUID | None = None,
    assigned_reviewer_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AdminAuthorizationResponse]:
    stmt = select(PriorAuthorization).options(
        selectinload(PriorAuthorization.procedures).selectinload(
            AuthorizationProcedure.decision
        )
    )
    if status is not None:
        stmt = stmt.where(PriorAuthorization.status == status)
    if practice_id is not None:
        stmt = stmt.where(PriorAuthorization.practice_id == practice_id)
    if member_id is not None:
        stmt = stmt.where(PriorAuthorization.member_id == member_id)
    if assigned_reviewer_id is not None:
        stmt = stmt.where(PriorAuthorization.assigned_reviewer_id == assigned_reviewer_id)

    stmt = stmt.order_by(PriorAuthorization.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [project_admin_authorization(row) for row in rows]


def get_admin_authorization(
    db: Session, *, authorization_id: uuid.UUID
) -> AdminAuthorizationResponse:
    auth = _load_authorization_with_procedures(db, authorization_id)
    if auth is None:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )
    return project_admin_authorization(auth)


def reassign_reviewer(
    db: Session,
    *,
    actor: AuthenticatedActor,
    authorization_id: uuid.UUID,
    new_reviewer_id: uuid.UUID,
    request_id: str,
) -> AdminAuthorizationResponse:
    stmt = (
        select(PriorAuthorization)
        .options(
            selectinload(PriorAuthorization.procedures).selectinload(
                AuthorizationProcedure.decision
            )
        )
        .where(PriorAuthorization.id == authorization_id)
        .with_for_update()
    )
    auth = db.execute(stmt).scalar_one_or_none()
    if auth is None:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )

    if auth.status != AuthorizationStatus.IN_REVIEW:
        raise DomainError(
            status=409,
            title="Invalid Authorization State",
            detail="Reassignment is only permitted while the authorization is in_review.",
            code="INVALID_AUTHORIZATION_STATE",
        )

    target_reviewer = db.get(User, new_reviewer_id)
    if (
        target_reviewer is None
        or not target_reviewer.is_active
        or target_reviewer.role != UserRole.REVIEWER
    ):
        raise DomainError(
            status=422,
            title="Invalid Target Reviewer",
            detail="Target user must be an active reviewer.",
            code="INVALID_TARGET_REVIEWER",
            errors=[
                ProblemFieldError(
                    code="INVALID_TARGET_REVIEWER",
                    field="reviewer_id",
                    message="Target user must be an active user with role 'reviewer'.",
                )
            ],
        )

    if _has_reviewer_conflict(db, new_reviewer_id, auth.member_id):
        raise DomainError(
            status=422,
            title="Reviewer Conflict of Interest",
            detail="Target reviewer has a treating relationship with this member.",
            code="REVIEWER_PATIENT_CONFLICT",
            errors=[
                ProblemFieldError(
                    code="REVIEWER_PATIENT_CONFLICT",
                    field="reviewer_id",
                    message="Target reviewer treats this member and cannot be assigned.",
                )
            ],
        )

    previous_reviewer_id = auth.assigned_reviewer_id
    now = datetime.now(UTC)
    auth.assigned_reviewer_id = new_reviewer_id
    auth.claimed_at = now
    auth.updated_at = now

    _append_audit_event(
        db,
        authorization_id=auth.id,
        actor=actor,
        action="authorization.reassigned",
        request_id=request_id,
        details={
            "previous_reviewer_id": str(previous_reviewer_id) if previous_reviewer_id else None,
            "new_reviewer_id": str(new_reviewer_id),
        },
    )

    db.commit()
    refreshed = _load_authorization_with_procedures(db, auth.id)
    assert refreshed is not None
    return project_admin_authorization(refreshed)


def list_audit_events(
    db: Session, *, authorization_id: uuid.UUID
) -> list[AuditEventResponse]:
    auth_exists = (
        db.execute(
            select(PriorAuthorization.id).where(PriorAuthorization.id == authorization_id)
        ).scalar_one_or_none()
        is not None
    )
    if not auth_exists:
        raise DomainError(
            status=404,
            title="Prior Authorization Not Found",
            detail="Prior authorization was not found.",
            code="AUTHORIZATION_NOT_FOUND",
        )

    stmt = (
        select(AuditEvent)
        .where(AuditEvent.authorization_id == authorization_id)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
    )
    events = db.execute(stmt).scalars().all()
    return [AuditEventResponse.model_validate(ev) for ev in events]


def get_submission_attempt(db: Session, *, lookup_id: str) -> SubmissionAttemptResponse:
    stmt = (
        select(SubmissionAttempt)
        .where(
            or_(
                SubmissionAttempt.request_id == lookup_id,
                SubmissionAttempt.correlation_id == lookup_id,
            )
        )
        .order_by(SubmissionAttempt.created_at.desc())
        .limit(1)
    )
    attempt = db.execute(stmt).scalar_one_or_none()
    if attempt is None:
        raise DomainError(
            status=404,
            title="Submission Attempt Not Found",
            detail="No submission diagnostic attempt was found for that request_id or correlation_id.",
            code="SUBMISSION_ATTEMPT_NOT_FOUND",
        )
    return SubmissionAttemptResponse.model_validate(attempt)


def search_submission_attempts(
    db: Session,
    *,
    practice_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    accepted: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SubmissionAttemptResponse]:
    stmt = select(SubmissionAttempt)
    if practice_id is not None:
        stmt = stmt.where(SubmissionAttempt.practice_id == practice_id)
    if idempotency_key is not None:
        stmt = stmt.where(SubmissionAttempt.idempotency_key == idempotency_key)
    if correlation_id is not None:
        stmt = stmt.where(SubmissionAttempt.correlation_id == correlation_id)
    if accepted is not None:
        stmt = stmt.where(SubmissionAttempt.accepted.is_(accepted))
    if since is not None:
        stmt = stmt.where(SubmissionAttempt.created_at >= since)
    if until is not None:
        stmt = stmt.where(SubmissionAttempt.created_at <= until)

    stmt = stmt.order_by(SubmissionAttempt.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [SubmissionAttemptResponse.model_validate(row) for row in rows]


# --- Role-Specific Projection Builders ---


def _sorted_procedures(auth: PriorAuthorization) -> list[AuthorizationProcedure]:
    return sorted(auth.procedures, key=lambda p: (p.created_at, str(p.id)))


def project_member_authorization(auth: PriorAuthorization) -> MemberAuthorizationResponse:
    return MemberAuthorizationResponse(
        id=auth.id,
        member_id=auth.member_id,
        diagnosis_code=auth.diagnosis_code,
        service_date=auth.service_date,
        status=auth.status,
        created_at=auth.created_at,
        decided_at=auth.decided_at,
        procedures=[
            MemberProcedureOutcome(
                id=proc.id,
                code_system=proc.code_system,
                code=proc.code,
                units=proc.units,
                outcome=proc.decision.outcome if proc.decision else None,
            )
            for proc in _sorted_procedures(auth)
        ],
    )


def project_office_authorization(auth: PriorAuthorization) -> OfficeAuthorizationResponse:
    return OfficeAuthorizationResponse(
        id=auth.id,
        practice_id=auth.practice_id,
        member_id=auth.member_id,
        diagnosis_code=auth.diagnosis_code,
        service_date=auth.service_date,
        clinical_note=auth.clinical_note,
        status=auth.status,
        created_at=auth.created_at,
        decided_at=auth.decided_at,
        procedures=[
            OfficeProcedureItem(
                id=proc.id,
                code_system=proc.code_system,
                code=proc.code,
                units=proc.units,
                outcome=proc.decision.outcome if proc.decision else None,
            )
            for proc in _sorted_procedures(auth)
        ],
    )


def project_reviewer_authorization(
    auth: PriorAuthorization, *, include_clinical_note: bool = True
) -> ReviewerAuthorizationResponse:
    return ReviewerAuthorizationResponse(
        id=auth.id,
        practice_id=auth.practice_id,
        member_id=auth.member_id,
        diagnosis_code=auth.diagnosis_code,
        service_date=auth.service_date,
        clinical_note=auth.clinical_note if include_clinical_note else None,
        status=auth.status,
        assigned_reviewer_id=auth.assigned_reviewer_id,
        claimed_at=auth.claimed_at,
        decided_at=auth.decided_at,
        created_at=auth.created_at,
        procedures=[
            ReviewerProcedureDetail(
                id=proc.id,
                code_system=proc.code_system,
                code=proc.code,
                units=proc.units,
                outcome=proc.decision.outcome if proc.decision else None,
                denial_reason=proc.decision.denial_reason if proc.decision else None,
            )
            for proc in _sorted_procedures(auth)
        ],
    )


def project_admin_authorization(auth: PriorAuthorization) -> AdminAuthorizationResponse:
    return AdminAuthorizationResponse(
        id=auth.id,
        practice_id=auth.practice_id,
        member_id=auth.member_id,
        submitted_by_user_id=auth.submitted_by_user_id,
        idempotency_key=auth.idempotency_key,
        diagnosis_code=auth.diagnosis_code,
        service_date=auth.service_date,
        clinical_note=auth.clinical_note,
        status=auth.status,
        assigned_reviewer_id=auth.assigned_reviewer_id,
        claimed_at=auth.claimed_at,
        decided_by_user_id=auth.decided_by_user_id,
        decided_at=auth.decided_at,
        created_at=auth.created_at,
        updated_at=auth.updated_at,
        procedures=[
            AdminProcedureDetail(
                id=proc.id,
                code_system=proc.code_system,
                code=proc.code,
                units=proc.units,
                outcome=proc.decision.outcome if proc.decision else None,
                denial_reason=proc.decision.denial_reason if proc.decision else None,
                decided_by_user_id=proc.decision.decided_by_user_id if proc.decision else None,
                decided_at=proc.decision.decided_at if proc.decision else None,
            )
            for proc in _sorted_procedures(auth)
        ],
    )
