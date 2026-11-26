"""Pydantic v2 request commands and role-specific response projections."""

import re
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import AuthorizationStatus, DecisionOutcome, ProcedureSystem, UserRole

_ICD10_PATTERN = re.compile(r"^[A-TV-Z][0-9]{2}(\.[A-Z0-9]{1,4})?$")
_CPT_PATTERN = re.compile(r"^[0-9]{4}[0-9A-Z]$")
_HCPCS_PATTERN = re.compile(r"^[A-V][0-9]{4}$")


class ProcedureSubmissionItem(BaseModel):
    code_system: ProcedureSystem
    code: str = Field(min_length=1, max_length=16)
    units: int

    @field_validator("units")
    @classmethod
    def validate_units(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Units must be at least 1.")
        if value > 30:
            raise ValueError(
                "Units exceed the 30-unit online submission limit (guard against entry typos); "
                "verify quantity or contact Utilization Management for clinical exception review."
            )
        return value

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str, info: Any) -> str:
        normalized = value.strip().upper()
        system = info.data.get("code_system")
        if system == ProcedureSystem.CPT and not _CPT_PATTERN.match(normalized):
            raise ValueError("CPT code must be 5 alphanumeric characters (e.g. 99213 or 70553)")
        if system == ProcedureSystem.HCPCS and not _HCPCS_PATTERN.match(normalized):
            raise ValueError("HCPCS code must start with A-V followed by 4 digits (e.g. E0601)")
        if not normalized:
            raise ValueError("Procedure code must not be blank")
        return normalized


class PriorAuthorizationCreateRequest(BaseModel):
    member_id: uuid.UUID
    diagnosis_code: str = Field(min_length=3, max_length=16)
    service_date: date
    clinical_note: str | None = Field(default=None, max_length=4000)
    procedures: list[ProcedureSubmissionItem] = Field(min_length=1)

    @field_validator("diagnosis_code")
    @classmethod
    def validate_diagnosis_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _ICD10_PATTERN.match(normalized):
            raise ValueError("Diagnosis code must be a valid ICD-10-CM format (e.g. M54.5 or E11.9)")
        return normalized

    @field_validator("clinical_note")
    @classmethod
    def validate_clinical_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed if trimmed else None

    @model_validator(mode="after")
    def validate_unique_procedures(self) -> "PriorAuthorizationCreateRequest":
        seen: set[tuple[ProcedureSystem, str]] = set()
        for item in self.procedures:
            key = (item.code_system, item.code)
            if key in seen:
                raise ValueError(
                    f"Duplicate procedure {item.code_system.value}:{item.code} in request; "
                    "combine quantities into units (1-30) on a single line item."
                )
            seen.add(key)
        return self


class ProcedureDecisionInput(BaseModel):
    procedure_id: uuid.UUID
    outcome: DecisionOutcome
    denial_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_denial_reason(self) -> "ProcedureDecisionInput":
        if self.denial_reason is not None:
            stripped = self.denial_reason.strip()
            self.denial_reason = stripped if stripped else None
        if self.outcome == DecisionOutcome.DENIED and not self.denial_reason:
            raise ValueError("denial_reason is required when outcome is 'denied'")
        if self.outcome == DecisionOutcome.APPROVED and self.denial_reason is not None:
            raise ValueError("denial_reason must be omitted or null when outcome is 'approved'")
        return self


class AuthorizationDecisionRequest(BaseModel):
    decisions: list[ProcedureDecisionInput] = Field(min_length=1)


class ReassignReviewerRequest(BaseModel):
    reviewer_id: uuid.UUID


# --- Role-Specific Projections ---


class MemberProcedureOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code_system: ProcedureSystem
    code: str
    units: int
    outcome: DecisionOutcome | None = None


class MemberAuthorizationResponse(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    diagnosis_code: str
    service_date: date
    status: AuthorizationStatus
    created_at: datetime
    decided_at: datetime | None = None
    procedures: list[MemberProcedureOutcome]


class OfficeProcedureItem(BaseModel):
    id: uuid.UUID
    code_system: ProcedureSystem
    code: str
    units: int
    outcome: DecisionOutcome | None = None


class OfficeAuthorizationResponse(BaseModel):
    id: uuid.UUID
    practice_id: uuid.UUID
    member_id: uuid.UUID
    diagnosis_code: str
    service_date: date
    clinical_note: str | None = None
    status: AuthorizationStatus
    created_at: datetime
    decided_at: datetime | None = None
    procedures: list[OfficeProcedureItem]


class ReviewerProcedureDetail(BaseModel):
    id: uuid.UUID
    code_system: ProcedureSystem
    code: str
    units: int
    outcome: DecisionOutcome | None = None
    denial_reason: str | None = None


class ReviewerAuthorizationResponse(BaseModel):
    id: uuid.UUID
    practice_id: uuid.UUID
    member_id: uuid.UUID
    diagnosis_code: str
    service_date: date
    clinical_note: str | None = None
    status: AuthorizationStatus
    assigned_reviewer_id: uuid.UUID | None = None
    claimed_at: datetime | None = None
    decided_at: datetime | None = None
    created_at: datetime
    procedures: list[ReviewerProcedureDetail]


class AdminProcedureDetail(BaseModel):
    id: uuid.UUID
    code_system: ProcedureSystem
    code: str
    units: int
    outcome: DecisionOutcome | None = None
    denial_reason: str | None = None
    decided_by_user_id: uuid.UUID | None = None
    decided_at: datetime | None = None


class AdminAuthorizationResponse(BaseModel):
    id: uuid.UUID
    practice_id: uuid.UUID
    member_id: uuid.UUID
    submitted_by_user_id: uuid.UUID
    idempotency_key: str
    diagnosis_code: str
    service_date: date
    clinical_note: str | None = None
    status: AuthorizationStatus
    assigned_reviewer_id: uuid.UUID | None = None
    claimed_at: datetime | None = None
    decided_by_user_id: uuid.UUID | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    procedures: list[AdminProcedureDetail]


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    authorization_id: uuid.UUID
    actor_id: uuid.UUID
    actor_role: UserRole
    action: str
    request_id: str
    details: dict[str, Any]
    created_at: datetime


class SubmissionAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: str
    correlation_id: str | None = None
    actor_id: uuid.UUID | None = None
    practice_id: uuid.UUID | None = None
    idempotency_key: str | None = None
    accepted: bool
    error_codes: list[str]
    authorization_id: uuid.UUID | None = None
    created_at: datetime
