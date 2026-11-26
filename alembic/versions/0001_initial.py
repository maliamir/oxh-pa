"""Initial Oxbridge Health Prior Authorization schema and append-only triggers.

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    user_role_enum = postgresql.ENUM(
        "member", "office", "reviewer", "admin", name="user_role", create_type=False
    )
    auth_status_enum = postgresql.ENUM(
        "submitted", "in_review", "decided", name="authorization_status", create_type=False
    )
    procedure_system_enum = postgresql.ENUM(
        "CPT", "HCPCS", name="procedure_system", create_type=False
    )
    decision_outcome_enum = postgresql.ENUM(
        "approved", "denied", name="decision_outcome", create_type=False
    )

    user_role_enum.create(op.get_bind(), checkfirst=True)
    auth_status_enum.create(op.get_bind(), checkfirst=True)
    procedure_system_enum.create(op.get_bind(), checkfirst=True)
    decision_outcome_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "practices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("npi", sa.String(length=20), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("npi"),
    )

    op.create_table(
        "members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_number", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_number"),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("role", user_role_enum, nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("practice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('member', 'office', 'reviewer', 'admin')",
            name="ck_users_role_valid",
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["practice_id"], ["practices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "practice_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("practice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["practice_id"], ["practices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("practice_id", "member_id", name="uq_practice_members_practice_member"),
    )
    op.create_index("ix_practice_members_member_id", "practice_members", ["member_id"], unique=False)

    op.create_table(
        "coverages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_name", sa.String(length=200), nullable=False),
        sa.Column("effective_start", sa.Date(), nullable=False),
        sa.Column("effective_end", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("effective_end >= effective_start", name="ck_coverages_dates_valid"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_coverages_member_dates",
        "coverages",
        ["member_id", "effective_start", "effective_end"],
        unique=False,
    )

    op.create_table(
        "reviewer_patients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_note", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reviewer_user_id", "member_id", name="uq_reviewer_patients_reviewer_member"
        ),
    )
    op.create_index(
        "ix_reviewer_patients_reviewer_member",
        "reviewer_patients",
        ["reviewer_user_id", "member_id"],
        unique=False,
    )

    op.create_table(
        "prior_authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("practice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("diagnosis_code", sa.String(length=16), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("clinical_note", sa.Text(), nullable=True),
        sa.Column("status", auth_status_enum, nullable=False),
        sa.Column("assigned_reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'in_review', 'decided')",
            name="ck_prior_authorizations_status_valid",
        ),
        sa.ForeignKeyConstraint(["assigned_reviewer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["practice_id"], ["practices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "practice_id", "idempotency_key", name="uq_prior_authorizations_practice_idempotency"
        ),
    )
    op.create_index(
        "ix_prior_authorizations_member_created",
        "prior_authorizations",
        ["member_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_prior_authorizations_practice_created",
        "prior_authorizations",
        ["practice_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_prior_authorizations_queue",
        "prior_authorizations",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_prior_authorizations_reviewer",
        "prior_authorizations",
        ["assigned_reviewer_id"],
        unique=False,
    )

    op.create_table(
        "authorization_procedures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authorization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_system", procedure_system_enum, nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code_system IN ('CPT', 'HCPCS')",
            name="ck_auth_procedures_code_system_valid",
        ),
        sa.CheckConstraint("units >= 1 AND units <= 30", name="ck_auth_procedures_units_1_30"),
        sa.ForeignKeyConstraint(
            ["authorization_id"], ["prior_authorizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_procedures_authorization_id",
        "authorization_procedures",
        ["authorization_id"],
        unique=False,
    )

    op.create_table(
        "procedure_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("procedure_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authorization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", decision_outcome_enum, nullable=False),
        sa.Column("denial_reason", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(outcome = 'denied' AND denial_reason IS NOT NULL AND btrim(denial_reason) <> '') "
            "OR (outcome = 'approved' AND denial_reason IS NULL)",
            name="ck_procedure_decisions_denial_reason",
        ),
        sa.CheckConstraint(
            "outcome IN ('approved', 'denied')",
            name="ck_procedure_decisions_outcome_valid",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_id"], ["prior_authorizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["procedure_id"], ["authorization_procedures.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("procedure_id", name="uq_procedure_decisions_procedure_id"),
    )
    op.create_index(
        "ix_procedure_decisions_authorization_id",
        "procedure_decisions",
        ["authorization_id"],
        unique=False,
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("authorization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_role", user_role_enum, nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["authorization_id"], ["prior_authorizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_auth_created",
        "audit_events",
        ["authorization_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"], unique=False)

    op.create_table(
        "submission_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("practice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("error_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("authorization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["authorization_id"], ["prior_authorizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["practice_id"], ["practices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_submission_attempts_practice_created",
        "submission_attempts",
        ["practice_id", "created_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_append_only_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Table % is append-only; % operations are prohibited', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_procedure_decisions_append_only
        BEFORE UPDATE OR DELETE ON procedure_decisions
        FOR EACH ROW
        EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW
        EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events;")
    op.execute("DROP TRIGGER IF EXISTS trg_procedure_decisions_append_only ON procedure_decisions;")
    op.execute("DROP FUNCTION IF EXISTS prevent_append_only_mutation();")

    op.drop_index("ix_submission_attempts_practice_created", table_name="submission_attempts")
    op.drop_table("submission_attempts")

    op.drop_index("ix_audit_events_request_id", table_name="audit_events")
    op.drop_index("ix_audit_events_auth_created", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_procedure_decisions_authorization_id", table_name="procedure_decisions")
    op.drop_table("procedure_decisions")

    op.drop_index("ix_auth_procedures_authorization_id", table_name="authorization_procedures")
    op.drop_table("authorization_procedures")

    op.drop_index("ix_prior_authorizations_reviewer", table_name="prior_authorizations")
    op.drop_index("ix_prior_authorizations_queue", table_name="prior_authorizations")
    op.drop_index("ix_prior_authorizations_practice_created", table_name="prior_authorizations")
    op.drop_index("ix_prior_authorizations_member_created", table_name="prior_authorizations")
    op.drop_table("prior_authorizations")

    op.drop_index("ix_reviewer_patients_reviewer_member", table_name="reviewer_patients")
    op.drop_table("reviewer_patients")

    op.drop_index("ix_coverages_member_dates", table_name="coverages")
    op.drop_table("coverages")

    op.drop_index("ix_practice_members_member_id", table_name="practice_members")
    op.drop_table("practice_members")

    op.drop_table("users")
    op.drop_table("members")
    op.drop_table("practices")

    sa.Enum(name="decision_outcome").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="procedure_system").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="authorization_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
