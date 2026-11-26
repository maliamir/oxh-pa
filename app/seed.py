"""Deterministic synthetic seed data for local development and automated tests."""

import uuid
from datetime import date, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.database import get_session_factory
from app.models import (
    Coverage,
    Member,
    Practice,
    PracticeMember,
    ReviewerPatient,
    User,
    UserRole,
)

PRACTICE_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
HAWAII_PRACTICE_ID = uuid.UUID("10000000-0000-0000-0000-000000000002")

LINKED_MEMBER_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
UNLINKED_MEMBER_ID = uuid.UUID("20000000-0000-0000-0000-000000000002")
HAWAII_MEMBER_ID = uuid.UUID("20000000-0000-0000-0000-000000000003")

OFFICE_USER_ID = uuid.UUID("30000000-0000-0000-0000-000000000001")
MEMBER_USER_ID = uuid.UUID("30000000-0000-0000-0000-000000000002")
REVIEWER_USER_ID = uuid.UUID("30000000-0000-0000-0000-000000000003")
CONFLICTED_REVIEWER_USER_ID = uuid.UUID("30000000-0000-0000-0000-000000000004")
ADMIN_USER_ID = uuid.UUID("30000000-0000-0000-0000-000000000005")
SECOND_REVIEWER_USER_ID = uuid.UUID("30000000-0000-0000-0000-000000000006")
HAWAII_OFFICE_USER_ID = uuid.UUID("30000000-0000-0000-0000-000000000007")

COVERAGE_ID = uuid.UUID("40000000-0000-0000-0000-000000000001")
HAWAII_COVERAGE_ID = uuid.UUID("40000000-0000-0000-0000-000000000002")

PRACTICE_MEMBER_ID = uuid.UUID("50000000-0000-0000-0000-000000000001")
HAWAII_PRACTICE_MEMBER_ID = uuid.UUID("50000000-0000-0000-0000-000000000002")

REVIEWER_PATIENT_ID = uuid.UUID("60000000-0000-0000-0000-000000000001")


def seed_database(db: Session) -> None:
    today = date.today()
    coverage_start = today - timedelta(days=180)
    coverage_end = today + timedelta(days=540)

    practices_rows = [
        {
            "id": PRACTICE_ID,
            "name": "Oxbridge Orthopedic & Spine Clinic (Maine/Eastern)",
            "npi": "1234567893",
            "timezone": "America/New_York",
        },
        {
            "id": HAWAII_PRACTICE_ID,
            "name": "Oxbridge Pacific Medical Group (Honolulu)",
            "npi": "1987654324",
            "timezone": "Pacific/Honolulu",
        },
    ]
    for p_row in practices_rows:
        db.execute(
            pg_insert(Practice)
            .values(**p_row)
            .on_conflict_do_update(
                index_elements=[Practice.id],
                set_={
                    "name": p_row["name"],
                    "npi": p_row["npi"],
                    "timezone": p_row["timezone"],
                },
            )
        )

    members_rows = [
        {
            "id": LINKED_MEMBER_ID,
            "member_number": "OXH-MEM-1001",
            "full_name": "Jordan Vance",
            "date_of_birth": date(1984, 5, 14),
        },
        {
            "id": UNLINKED_MEMBER_ID,
            "member_number": "OXH-MEM-1002",
            "full_name": "Avery Patel",
            "date_of_birth": date(1990, 9, 22),
        },
        {
            "id": HAWAII_MEMBER_ID,
            "member_number": "OXH-MEM-1003",
            "full_name": "Kailani Kealoha",
            "date_of_birth": date(1979, 11, 3),
        },
    ]
    for row in members_rows:
        db.execute(
            pg_insert(Member)
            .values(**row)
            .on_conflict_do_update(
                index_elements=[Member.id],
                set_={
                    "member_number": row["member_number"],
                    "full_name": row["full_name"],
                    "date_of_birth": row["date_of_birth"],
                },
            )
        )

    users_rows = [
        {
            "id": OFFICE_USER_ID,
            "email": "office.coord@oxh.example",
            "full_name": "Elena Rostova",
            "role": UserRole.OFFICE,
            "member_id": None,
            "practice_id": PRACTICE_ID,
            "is_active": True,
        },
        {
            "id": HAWAII_OFFICE_USER_ID,
            "email": "honolulu.coord@oxh.example",
            "full_name": "Malia Nakoa",
            "role": UserRole.OFFICE,
            "member_id": None,
            "practice_id": HAWAII_PRACTICE_ID,
            "is_active": True,
        },
        {
            "id": MEMBER_USER_ID,
            "email": "jordan.vance@oxh.example",
            "full_name": "Jordan Vance",
            "role": UserRole.MEMBER,
            "member_id": LINKED_MEMBER_ID,
            "practice_id": None,
            "is_active": True,
        },
        {
            "id": REVIEWER_USER_ID,
            "email": "dr.chen@oxh.example",
            "full_name": "Dr. Marcus Chen",
            "role": UserRole.REVIEWER,
            "member_id": None,
            "practice_id": None,
            "is_active": True,
        },
        {
            "id": CONFLICTED_REVIEWER_USER_ID,
            "email": "dr.mercer@oxh.example",
            "full_name": "Dr. Clara Mercer",
            "role": UserRole.REVIEWER,
            "member_id": None,
            "practice_id": None,
            "is_active": True,
        },
        {
            "id": ADMIN_USER_ID,
            "email": "ops.admin@oxh.example",
            "full_name": "Devon Brooks",
            "role": UserRole.ADMIN,
            "member_id": None,
            "practice_id": None,
            "is_active": True,
        },
        {
            "id": SECOND_REVIEWER_USER_ID,
            "email": "dr.okafor@oxh.example",
            "full_name": "Dr. Tunde Okafor",
            "role": UserRole.REVIEWER,
            "member_id": None,
            "practice_id": None,
            "is_active": True,
        },
    ]
    for u_row in users_rows:
        db.execute(
            pg_insert(User)
            .values(**u_row)
            .on_conflict_do_update(
                index_elements=[User.id],
                set_={
                    "email": u_row["email"],
                    "full_name": u_row["full_name"],
                    "role": u_row["role"],
                    "member_id": u_row["member_id"],
                    "practice_id": u_row["practice_id"],
                    "is_active": u_row["is_active"],
                },
            )
        )

    for pm_id, prac_id, mem_id in [
        (PRACTICE_MEMBER_ID, PRACTICE_ID, LINKED_MEMBER_ID),
        (HAWAII_PRACTICE_MEMBER_ID, HAWAII_PRACTICE_ID, HAWAII_MEMBER_ID),
    ]:
        db.execute(
            pg_insert(PracticeMember)
            .values(
                id=pm_id,
                practice_id=prac_id,
                member_id=mem_id,
                active=True,
            )
            .on_conflict_do_update(
                index_elements=[PracticeMember.id],
                set_={"active": True},
            )
        )

    for cov_id, mem_id, plan in [
        (COVERAGE_ID, LINKED_MEMBER_ID, "Oxbridge Gold PPO 2026"),
        (HAWAII_COVERAGE_ID, HAWAII_MEMBER_ID, "Oxbridge Pacific PPO 2026"),
    ]:
        db.execute(
            pg_insert(Coverage)
            .values(
                id=cov_id,
                member_id=mem_id,
                plan_name=plan,
                effective_start=coverage_start,
                effective_end=coverage_end,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[Coverage.id],
                set_={
                    "effective_start": coverage_start,
                    "effective_end": coverage_end,
                    "is_active": True,
                },
            )
        )

    db.execute(
        pg_insert(ReviewerPatient)
        .values(
            id=REVIEWER_PATIENT_ID,
            reviewer_user_id=CONFLICTED_REVIEWER_USER_ID,
            member_id=LINKED_MEMBER_ID,
            relationship_note="Treating specialist within last 12 months",
        )
        .on_conflict_do_update(
            index_elements=[ReviewerPatient.id],
            set_={"relationship_note": "Treating specialist within last 12 months"},
        )
    )

    db.commit()


def main() -> None:
    session_factory = get_session_factory()
    with session_factory() as db:
        seed_database(db)


if __name__ == "__main__":
    main()
