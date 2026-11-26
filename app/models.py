"""SQLAlchemy 2 persistence models and database constraints."""

import enum
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base


class UserRole(str, enum.Enum):
    MEMBER = "member"
    OFFICE = "office"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class AuthorizationStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    DECIDED = "decided"


class ProcedureSystem(str, enum.Enum):
    CPT = "CPT"
    HCPCS = "HCPCS"


class DecisionOutcome(str, enum.Enum):
    APPROVED = "approved"
    DENIED = "denied"


class Practice(Base):
    __tablename__ = "practices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    npi: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/New_York")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @validates("timezone")
    def validate_timezone(self, _key: str, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid IANA timezone: {value}") from exc
        return value

    practice_members: Mapped[list["PracticeMember"]] = relationship(back_populates="practice")
    prior_authorizations: Mapped[list["PriorAuthorization"]] = relationship(
        back_populates="practice"
    )
    submission_attempts: Mapped[list["SubmissionAttempt"]] = relationship(
        back_populates="practice"
    )


class Member(Base):
    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    practice_members: Mapped[list["PracticeMember"]] = relationship(back_populates="member")
    coverages: Mapped[list["Coverage"]] = relationship(back_populates="member")
    reviewer_patients: Mapped[list["ReviewerPatient"]] = relationship(back_populates="member")
    prior_authorizations: Mapped[list["PriorAuthorization"]] = relationship(
        back_populates="member"
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('member', 'office', 'reviewer', 'admin')",
            name="ck_users_role_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="RESTRICT"), nullable=True
    )
    practice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("practices.id", ondelete="RESTRICT"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PracticeMember(Base):
    __tablename__ = "practice_members"
    __table_args__ = (
        UniqueConstraint("practice_id", "member_id", name="uq_practice_members_practice_member"),
        Index("ix_practice_members_member_id", "member_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    practice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("practices.id", ondelete="RESTRICT"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="RESTRICT"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    practice: Mapped["Practice"] = relationship(back_populates="practice_members")
    member: Mapped["Member"] = relationship(back_populates="practice_members")


class Coverage(Base):
    __tablename__ = "coverages"
    __table_args__ = (
        CheckConstraint("effective_end >= effective_start", name="ck_coverages_dates_valid"),
        Index("ix_coverages_member_dates", "member_id", "effective_start", "effective_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="RESTRICT"), nullable=False
    )
    plan_name: Mapped[str] = mapped_column(String(200), nullable=False)
    effective_start: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship(back_populates="coverages")


class ReviewerPatient(Base):
    __tablename__ = "reviewer_patients"
    __table_args__ = (
        UniqueConstraint(
            "reviewer_user_id", "member_id", name="uq_reviewer_patients_reviewer_member"
        ),
        Index("ix_reviewer_patients_reviewer_member", "reviewer_user_id", "member_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="RESTRICT"), nullable=False
    )
    relationship_note: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship(back_populates="reviewer_patients")


class PriorAuthorization(Base):
    __tablename__ = "prior_authorizations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted', 'in_review', 'decided')",
            name="ck_prior_authorizations_status_valid",
        ),
        CheckConstraint(
            "(status <> 'decided') OR (decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_prior_authorizations_decided_requires_approver",
        ),
        UniqueConstraint(
            "practice_id", "idempotency_key", name="uq_prior_authorizations_practice_idempotency"
        ),
        Index("ix_prior_authorizations_member_created", "member_id", "created_at"),
        Index("ix_prior_authorizations_queue", "status", "created_at"),
        Index("ix_prior_authorizations_practice_created", "practice_id", "created_at"),
        Index("ix_prior_authorizations_reviewer", "assigned_reviewer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    practice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("practices.id", ondelete="RESTRICT"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    diagnosis_code: Mapped[str] = mapped_column(String(16), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    clinical_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AuthorizationStatus] = mapped_column(
        Enum(
            AuthorizationStatus,
            name="authorization_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=AuthorizationStatus.SUBMITTED,
    )
    assigned_reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    practice: Mapped["Practice"] = relationship(back_populates="prior_authorizations")
    member: Mapped["Member"] = relationship(back_populates="prior_authorizations")
    procedures: Mapped[list["AuthorizationProcedure"]] = relationship(
        back_populates="authorization", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="authorization")


class AuthorizationProcedure(Base):
    __tablename__ = "authorization_procedures"
    __table_args__ = (
        CheckConstraint("units >= 1 AND units <= 30", name="ck_auth_procedures_units_1_30"),
        CheckConstraint(
            "code_system IN ('CPT', 'HCPCS')", name="ck_auth_procedures_code_system_valid"
        ),
        Index("ix_auth_procedures_authorization_id", "authorization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prior_authorizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_system: Mapped[ProcedureSystem] = mapped_column(
        Enum(
            ProcedureSystem,
            name="procedure_system",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    authorization: Mapped["PriorAuthorization"] = relationship(back_populates="procedures")
    decision: Mapped["ProcedureDecision | None"] = relationship(
        back_populates="procedure", uselist=False
    )


class ProcedureDecision(Base):
    __tablename__ = "procedure_decisions"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('approved', 'denied')", name="ck_procedure_decisions_outcome_valid"
        ),
        CheckConstraint(
            "(outcome = 'denied' AND denial_reason IS NOT NULL AND btrim(denial_reason) <> '') "
            "OR (outcome = 'approved' AND denial_reason IS NULL)",
            name="ck_procedure_decisions_denial_reason",
        ),
        UniqueConstraint("procedure_id", name="uq_procedure_decisions_procedure_id"),
        Index("ix_procedure_decisions_authorization_id", "authorization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    procedure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("authorization_procedures.id", ondelete="RESTRICT"),
        nullable=False,
    )
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prior_authorizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    outcome: Mapped[DecisionOutcome] = mapped_column(
        Enum(
            DecisionOutcome,
            name="decision_outcome",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    denial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    procedure: Mapped["AuthorizationProcedure"] = relationship(back_populates="decision")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_auth_created", "authorization_id", "created_at"),
        Index("ix_audit_events_request_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prior_authorizations.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    actor_role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            create_type=False,
        ),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    authorization: Mapped["PriorAuthorization"] = relationship(back_populates="audit_events")


class SubmissionAttempt(Base):
    __tablename__ = "submission_attempts"
    __table_args__ = (
        Index("ix_submission_attempts_practice_created", "practice_id", "created_at"),
        Index("ix_submission_attempts_correlation_id", "correlation_id"),
        Index("ix_submission_attempts_idempotency_key", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    practice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("practices.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    authorization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prior_authorizations.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    practice: Mapped["Practice | None"] = relationship(back_populates="submission_attempts")
