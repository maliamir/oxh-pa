"""PostgreSQL-backed behavioral tests covering all oxh-pa invariants."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import (
    AuditEvent,
    AuthorizationStatus,
    PriorAuthorization,
    ProcedureDecision,
    UserRole,
)
from app.seed import (
    ADMIN_USER_ID,
    CONFLICTED_REVIEWER_USER_ID,
    HAWAII_MEMBER_ID,
    HAWAII_OFFICE_USER_ID,
    LINKED_MEMBER_ID,
    MEMBER_USER_ID,
    OFFICE_USER_ID,
    PRACTICE_ID,
    REVIEWER_USER_ID,
    SECOND_REVIEWER_USER_ID,
    UNLINKED_MEMBER_ID,
)
from app.tokens import mint_token


def _auth_header(user_id: uuid.UUID, role: UserRole) -> dict[str, str]:
    token = mint_token(user_id=user_id, role=role)
    return {"Authorization": f"Bearer {token}"}


def _valid_submission_payload(service_date: date | None = None) -> dict[str, object]:
    target_date = service_date or (date.today() + timedelta(days=7))
    return {
        "member_id": str(LINKED_MEMBER_ID),
        "diagnosis_code": "M54.5",
        "service_date": target_date.isoformat(),
        "clinical_note": "Persistent lumbar radiculopathy despite 6 weeks physical therapy.",
        "procedures": [
            {"code_system": "CPT", "code": "72148", "units": 1},
            {"code_system": "HCPCS", "code": "E0747", "units": 2},
        ],
    }


def test_1_idempotent_submission_business_errors_and_support_lookup(client: TestClient) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    admin_headers = _auth_header(ADMIN_USER_ID, UserRole.ADMIN)
    payload = _valid_submission_payload()

    r1 = client.post(
        "/v1/office/prior-authorizations",
        json=payload,
        headers={
            **office_headers,
            "Idempotency-Key": "idem-001",
            "X-Correlation-ID": "corr-submit-1",
        },
    )
    assert r1.status_code == 201
    created = r1.json()
    auth_id = created["id"]
    assert created["status"] == "submitted"
    assert len(created["procedures"]) == 2
    assert r1.headers["X-Correlation-ID"] == "corr-submit-1"
    assert r1.headers["X-Request-ID"] != "corr-submit-1"

    r2 = client.post(
        "/v1/office/prior-authorizations",
        json=payload,
        headers={
            **office_headers,
            "Idempotency-Key": "idem-001",
            "X-Correlation-ID": "corr-submit-2",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == auth_id

    # Replaying with the exact same client X-Request-ID never causes a 500
    altered = {**payload, "diagnosis_code": "M54.2"}
    r3 = client.post(
        "/v1/office/prior-authorizations",
        json=altered,
        headers={
            **office_headers,
            "Idempotency-Key": "idem-001",
            "X-Request-ID": "reused-client-id",
        },
    )
    assert r3.status_code == 409
    assert r3.headers["content-type"].startswith("application/problem+json")
    assert r3.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    # Second call with the same client X-Request-ID also succeeds cleanly without 500
    r3_repeat = client.post(
        "/v1/office/prior-authorizations",
        json=altered,
        headers={
            **office_headers,
            "Idempotency-Key": "idem-001",
            "X-Request-ID": "reused-client-id",
        },
    )
    assert r3_repeat.status_code == 409

    # Unlinked member short-circuits coverage check so coverage status is not disclosed
    bad_business = {
        "member_id": str(UNLINKED_MEMBER_ID),
        "diagnosis_code": "M54.5",
        "service_date": (date.today() - timedelta(days=5)).isoformat(),
        "clinical_note": "Sensitive note that must never be copied into diagnostics",
        "procedures": [{"code_system": "CPT", "code": "99213", "units": 1}],
    }
    r4 = client.post(
        "/v1/office/prior-authorizations",
        json=bad_business,
        headers={
            **office_headers,
            "Idempotency-Key": "idem-bad-1",
            "X-Correlation-ID": "corr-bad-biz",
        },
    )
    assert r4.status_code == 422
    err_body = r4.json()
    codes = {e["code"] for e in err_body["errors"]}
    assert codes == {"SERVICE_DATE_IN_PAST", "MEMBER_NOT_LINKED"}

    # Support can look up by server request_id or client correlation_id
    sup = client.get(f"/v1/admin/submission-attempts/{err_body['request_id']}", headers=admin_headers)
    assert sup.status_code == 200
    sup_json = sup.json()
    assert sup_json["request_id"] == err_body["request_id"]
    assert sup_json["correlation_id"] == "corr-bad-biz"
    assert sup_json["accepted"] is False
    assert "MEMBER_NOT_LINKED" in sup_json["error_codes"]
    assert "clinical_note" not in sup_json

    sup_by_corr = client.get("/v1/admin/submission-attempts/corr-bad-biz", headers=admin_headers)
    assert sup_by_corr.status_code == 200
    assert sup_by_corr.json()["request_id"] == err_body["request_id"]

    # Search submission attempts by idempotency_key includes replay (200) and conflict (409)
    search_resp = client.get(
        "/v1/admin/submission-attempts",
        params={"practice_id": str(PRACTICE_ID), "idempotency_key": "idem-001"},
        headers=admin_headers,
    )
    assert search_resp.status_code == 200
    attempts = search_resp.json()
    assert len(attempts) == 4
    assert sum(1 for a in attempts if a["accepted"]) == 2
    assert sum(1 for a in attempts if "IDEMPOTENCY_KEY_CONFLICT" in a["error_codes"]) == 2


def test_2_concurrent_identical_submissions_create_one_authorization(
    client: TestClient, db_session: Session
) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    payload = _valid_submission_payload()

    def _submit(idx: int) -> tuple[int, str]:
        resp = client.post(
            "/v1/office/prior-authorizations",
            json=payload,
            headers={
                **office_headers,
                "Idempotency-Key": "idem-concurrent-key",
                "X-Correlation-ID": f"corr-conc-{idx}",
            },
        )
        return resp.status_code, resp.json()["id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_submit, range(6)))

    statuses = [r[0] for r in results]
    ids = {r[1] for r in results}
    assert len(ids) == 1
    assert statuses.count(201) == 1
    assert statuses.count(200) == 5

    count = len(db_session.execute(select(PriorAuthorization)).scalars().all())
    assert count == 1


def test_3_schema_validation_identifies_every_bad_field_and_records_attempt(
    client: TestClient,
) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    admin_headers = _auth_header(ADMIN_USER_ID, UserRole.ADMIN)
    bad_payload = {
        "member_id": "not-a-uuid",
        "diagnosis_code": "INVALID!",
        "service_date": "not-a-date",
        "procedures": [
            {"code_system": "CPT", "code": "BAD", "units": 0},
            {"code_system": "HCPCS", "code": "99999", "units": 31},
        ],
    }
    resp = client.post(
        "/v1/office/prior-authorizations",
        json=bad_payload,
        headers={
            **office_headers,
            "Idempotency-Key": "idem-schema-1",
            "X-Correlation-ID": "corr-schema-1",
        },
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    fields = {err["field"] for err in body["errors"]}
    assert "member_id" in fields
    assert "diagnosis_code" in fields
    assert "service_date" in fields
    assert "procedures.0.code" in fields
    assert "procedures.0.units" in fields
    assert "procedures.1.code" in fields
    assert "procedures.1.units" in fields

    # Schema validation failures are persisted for support lookup without raw payload values
    sup = client.get(f"/v1/admin/submission-attempts/{body['request_id']}", headers=admin_headers)
    assert sup.status_code == 200
    sup_data = sup.json()
    assert sup_data["accepted"] is False
    assert sup_data["practice_id"] == str(PRACTICE_ID)
    assert sup_data["idempotency_key"] == "idem-schema-1"
    assert len(sup_data["error_codes"]) == 7


def test_4_conflicted_reviewer_cannot_see_or_claim_request_and_queue_hides_notes(
    client: TestClient,
) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    conflicted_headers = _auth_header(CONFLICTED_REVIEWER_USER_ID, UserRole.REVIEWER)
    clean_headers = _auth_header(REVIEWER_USER_ID, UserRole.REVIEWER)

    created = client.post(
        "/v1/office/prior-authorizations",
        json=_valid_submission_payload(),
        headers={**office_headers, "Idempotency-Key": "idem-conflict-1"},
    ).json()
    auth_id = created["id"]

    q_conflicted = client.get("/v1/reviewer/queue", headers=conflicted_headers)
    assert q_conflicted.status_code == 200
    assert all(item["id"] != auth_id for item in q_conflicted.json())

    claim_conflicted = client.post(
        f"/v1/reviewer/prior-authorizations/{auth_id}/claim", headers=conflicted_headers
    )
    assert claim_conflicted.status_code == 404

    get_conflicted = client.get(
        f"/v1/reviewer/prior-authorizations/{auth_id}", headers=conflicted_headers
    )
    assert get_conflicted.status_code == 404

    q_clean = client.get("/v1/reviewer/queue", headers=clean_headers)
    matched = [item for item in q_clean.json() if item["id"] == auth_id]
    assert len(matched) == 1
    assert matched[0]["clinical_note"] is None


def test_5_final_decision_atomic_audited_and_private_to_member_and_office(
    client: TestClient,
) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    reviewer_headers = _auth_header(REVIEWER_USER_ID, UserRole.REVIEWER)
    member_headers = _auth_header(MEMBER_USER_ID, UserRole.MEMBER)
    admin_headers = _auth_header(ADMIN_USER_ID, UserRole.ADMIN)

    created = client.post(
        "/v1/office/prior-authorizations",
        json=_valid_submission_payload(),
        headers={**office_headers, "Idempotency-Key": "idem-decide-1"},
    ).json()
    auth_id = created["id"]
    p1_id = created["procedures"][0]["id"]
    p2_id = created["procedures"][1]["id"]

    claim = client.post(
        f"/v1/reviewer/prior-authorizations/{auth_id}/claim", headers=reviewer_headers
    )
    assert claim.status_code == 200
    assert claim.json()["clinical_note"] is not None

    # Reviewer can list their assigned in_review cases
    assigned = client.get("/v1/reviewer/assigned", headers=reviewer_headers)
    assert assigned.status_code == 200
    assert any(item["id"] == auth_id for item in assigned.json())

    # Admin can search all authorizations
    admin_list = client.get(
        "/v1/admin/prior-authorizations",
        params={"status": "in_review", "assigned_reviewer_id": str(REVIEWER_USER_ID)},
        headers=admin_headers,
    )
    assert admin_list.status_code == 200
    assert any(item["id"] == auth_id for item in admin_list.json())

    decision_body = {
        "decisions": [
            {"procedure_id": p1_id, "outcome": "approved"},
            {
                "procedure_id": p2_id,
                "outcome": "denied",
                "denial_reason": "Conservative bracing trial not documented.",
            },
        ]
    }
    decided = client.post(
        f"/v1/reviewer/prior-authorizations/{auth_id}/decision",
        json=decision_body,
        headers=reviewer_headers,
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "decided"

    mem_view = client.get(f"/v1/member/prior-authorizations/{auth_id}", headers=member_headers)
    assert mem_view.status_code == 200
    mem_json = mem_view.json()
    assert mem_json["status"] == "decided"
    assert "clinical_note" not in mem_json
    for proc in mem_json["procedures"]:
        assert "denial_reason" not in proc
        assert proc["outcome"] in ("approved", "denied")

    off_view = client.get(f"/v1/office/prior-authorizations/{auth_id}", headers=office_headers)
    assert off_view.status_code == 200
    off_json = off_view.json()
    assert "clinical_note" in off_json
    for proc in off_json["procedures"]:
        assert "denial_reason" not in proc

    audits = client.get(
        f"/v1/admin/prior-authorizations/{auth_id}/audit-events", headers=admin_headers
    )
    assert audits.status_code == 200
    actions = [ev["action"] for ev in audits.json()]
    assert actions == [
        "authorization.submitted",
        "authorization.claimed",
        "authorization.decided",
    ]


def test_6_partial_decisions_do_not_change_state_or_create_decision_rows(
    client: TestClient, db_session: Session
) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    reviewer_headers = _auth_header(REVIEWER_USER_ID, UserRole.REVIEWER)

    created = client.post(
        "/v1/office/prior-authorizations",
        json=_valid_submission_payload(),
        headers={**office_headers, "Idempotency-Key": "idem-partial-1"},
    ).json()
    auth_id = created["id"]
    p1_id = created["procedures"][0]["id"]

    client.post(f"/v1/reviewer/prior-authorizations/{auth_id}/claim", headers=reviewer_headers)

    partial = client.post(
        f"/v1/reviewer/prior-authorizations/{auth_id}/decision",
        json={"decisions": [{"procedure_id": p1_id, "outcome": "approved"}]},
        headers=reviewer_headers,
    )
    assert partial.status_code == 422
    assert partial.json()["code"] == "INCOMPLETE_PROCEDURE_DECISIONS"

    auth_row = db_session.get(PriorAuthorization, uuid.UUID(auth_id))
    assert auth_row is not None
    assert auth_row.status == AuthorizationStatus.IN_REVIEW

    decision_rows = db_session.execute(select(ProcedureDecision)).scalars().all()
    assert len(decision_rows) == 0


def test_7_concurrent_reviewer_claims_produce_one_success_and_one_conflict(
    client: TestClient,
) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    rev1_headers = _auth_header(REVIEWER_USER_ID, UserRole.REVIEWER)
    rev2_headers = _auth_header(SECOND_REVIEWER_USER_ID, UserRole.REVIEWER)

    created = client.post(
        "/v1/office/prior-authorizations",
        json=_valid_submission_payload(),
        headers={**office_headers, "Idempotency-Key": "idem-race-claim-1"},
    ).json()
    auth_id = created["id"]

    def _claim(hdrs: dict[str, str]) -> int:
        return client.post(
            f"/v1/reviewer/prior-authorizations/{auth_id}/claim", headers=hdrs
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(_claim, [rev1_headers, rev2_headers]))

    assert sorted(codes) == [200, 409]


def test_8_admin_reassignment_revokes_previous_reviewer(client: TestClient) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    rev1_headers = _auth_header(REVIEWER_USER_ID, UserRole.REVIEWER)
    rev2_headers = _auth_header(SECOND_REVIEWER_USER_ID, UserRole.REVIEWER)
    admin_headers = _auth_header(ADMIN_USER_ID, UserRole.ADMIN)

    created = client.post(
        "/v1/office/prior-authorizations",
        json=_valid_submission_payload(),
        headers={**office_headers, "Idempotency-Key": "idem-reassign-1"},
    ).json()
    auth_id = created["id"]
    p1_id = created["procedures"][0]["id"]
    p2_id = created["procedures"][1]["id"]

    assert (
        client.post(
            f"/v1/reviewer/prior-authorizations/{auth_id}/claim", headers=rev1_headers
        ).status_code
        == 200
    )

    reassigned = client.post(
        f"/v1/admin/prior-authorizations/{auth_id}/reassign",
        json={"reviewer_id": str(SECOND_REVIEWER_USER_ID)},
        headers=admin_headers,
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["assigned_reviewer_id"] == str(SECOND_REVIEWER_USER_ID)

    assert (
        client.get(
            f"/v1/reviewer/prior-authorizations/{auth_id}", headers=rev1_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/reviewer/prior-authorizations/{auth_id}/decision",
            json={
                "decisions": [
                    {"procedure_id": p1_id, "outcome": "approved"},
                    {"procedure_id": p2_id, "outcome": "approved"},
                ]
            },
            headers=rev1_headers,
        ).status_code
        == 404
    )

    assert (
        client.get(
            f"/v1/reviewer/prior-authorizations/{auth_id}", headers=rev2_headers
        ).status_code
        == 200
    )


def test_9_postgresql_rejects_audit_event_mutation(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    office_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    created = client.post(
        "/v1/office/prior-authorizations",
        json=_valid_submission_payload(),
        headers={**office_headers, "Idempotency-Key": "idem-immutable-audit-1"},
    ).json()
    auth_id = uuid.UUID(created["id"])

    with session_factory() as db:
        ev = db.execute(
            select(AuditEvent).where(AuditEvent.authorization_id == auth_id)
        ).scalar_one()
        ev_id = ev.id

        with pytest.raises(DatabaseError):
            db.execute(
                text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"),
                {"id": ev_id},
            )
            db.commit()
        db.rollback()

        with pytest.raises(DatabaseError):
            db.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": ev_id})
            db.commit()
        db.rollback()


def test_10_nationwide_practice_timezone_and_duplicate_procedure_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_utc = datetime(2026, 6, 15, 6, 0, 0, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr("app.service.datetime", _FrozenDateTime)

    eastern_headers = _auth_header(OFFICE_USER_ID, UserRole.OFFICE)
    hawaii_headers = _auth_header(HAWAII_OFFICE_USER_ID, UserRole.OFFICE)

    r_eastern = client.post(
        "/v1/office/prior-authorizations",
        json={
            "member_id": str(LINKED_MEMBER_ID),
            "diagnosis_code": "M54.5",
            "service_date": "2026-06-14",
            "procedures": [{"code_system": "CPT", "code": "72148", "units": 1}],
        },
        headers={**eastern_headers, "Idempotency-Key": "idem-tz-east-1"},
    )
    assert r_eastern.status_code == 422
    assert any(e["code"] == "SERVICE_DATE_IN_PAST" for e in r_eastern.json()["errors"])

    r_hawaii = client.post(
        "/v1/office/prior-authorizations",
        json={
            "member_id": str(HAWAII_MEMBER_ID),
            "diagnosis_code": "M54.5",
            "service_date": "2026-06-14",
            "procedures": [{"code_system": "CPT", "code": "72148", "units": 1}],
        },
        headers={**hawaii_headers, "Idempotency-Key": "idem-tz-hi-1"},
    )
    assert r_hawaii.status_code == 201

    r_dup = client.post(
        "/v1/office/prior-authorizations",
        json={
            "member_id": str(LINKED_MEMBER_ID),
            "diagnosis_code": "M54.5",
            "service_date": "2026-06-20",
            "procedures": [
                {"code_system": "CPT", "code": "72148", "units": 1},
                {"code_system": "CPT", "code": "72148", "units": 2},
            ],
        },
        headers={**eastern_headers, "Idempotency-Key": "idem-dup-proc-1"},
    )
    assert r_dup.status_code == 422
    assert r_dup.json()["code"] == "VALIDATION_FAILED"


def test_11_expired_token_rejected_and_production_secret_guard(client: TestClient) -> None:
    expired_token = mint_token(
        user_id=OFFICE_USER_ID,
        role=UserRole.OFFICE,
        issued_at=datetime.now(UTC) - timedelta(hours=2),
        expires_in=timedelta(hours=1),
    )
    resp = client.get(
        "/v1/office/prior-authorizations",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_TOKEN_EXPIRED"

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(app_env="production")
