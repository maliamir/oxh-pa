"""Support independent submission attempt logging and decided state constraint.

Revision ID: 0002_support_and_decided
Revises: 0001_initial
Create Date: 2026-11-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_support_and_decided"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_prior_authorizations_decided_requires_approver",
        "prior_authorizations",
        "(status <> 'decided') OR (decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)",
    )

    op.add_column(
        "submission_attempts",
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
    )
    op.alter_column(
        "submission_attempts",
        "actor_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "submission_attempts",
        "practice_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "submission_attempts",
        "idempotency_key",
        existing_type=sa.String(length=128),
        nullable=True,
    )
    op.create_index(
        "ix_submission_attempts_correlation_id",
        "submission_attempts",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_submission_attempts_idempotency_key",
        "submission_attempts",
        ["idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_submission_attempts_idempotency_key", table_name="submission_attempts")
    op.drop_index("ix_submission_attempts_correlation_id", table_name="submission_attempts")
    op.alter_column(
        "submission_attempts",
        "idempotency_key",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.alter_column(
        "submission_attempts",
        "practice_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "submission_attempts",
        "actor_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("submission_attempts", "correlation_id")
    op.drop_constraint(
        "ck_prior_authorizations_decided_requires_approver",
        "prior_authorizations",
        type_="check",
    )
