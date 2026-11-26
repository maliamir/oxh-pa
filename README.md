# Oxbridge Health Prior Authorization API (`oxh-pa`)

A production-shaped, intentionally compact implementation of the Oxbridge Health Prior Authorization API challenge built as a stateless Python 3.12 / FastAPI / SQLAlchemy 2 / PostgreSQL 16 modular monolith.

---

## 1. Step-by-Step Local Startup

1. **Copy environment defaults (optional—`compose.yaml` provides working local defaults):**
   ```bash
   cp .env.example .env
   ```
2. **Build and start PostgreSQL 16 and the API container:**
   ```bash
   docker compose up --build -d
   ```
   On startup, the `postgres` container initializes `oxh_prior_auth` and `oxh_prior_auth_test` via `sql/create_databases.sql`, and the `api` container executes `alembic upgrade head`, runs idempotent seeding (`python -m app.seed`) when `SEED_ON_STARTUP=true` (enabled in `compose.yaml`, disabled by default in `Dockerfile` for AWS deployments), and starts Uvicorn as non-root user `oxh`.
3. **Generate synthetic local JWT tokens:**
   ```bash
   docker compose exec api python -m app.tokens
   ```
   > **Security Note:** `python -m app.tokens` issues local HS256 JWTs anchored to `datetime.now(UTC)` signed with a development secret for local evaluation and automated tests only. Outside `development`/`test`, `Settings` refuses to start with the default `JWT_SECRET_KEY`.

---

## 2. Health, Documentation, and Database Endpoints

| Resource | URL / Connection String |
| :--- | :--- |
| **Liveness Probe** | `http://localhost:8000/health/live` |
| **Readiness Probe** | `http://localhost:8000/health/ready` |
| **Swagger UI** | `http://localhost:8000/docs` |
| **OpenAPI 3.1 JSON** | `http://localhost:8000/openapi.json` |
| **Primary PostgreSQL DB** | `postgresql+psycopg://oxh_user:oxh_password@localhost:5432/oxh_prior_auth` |
| **Test PostgreSQL DB** | `postgresql+psycopg://oxh_user:oxh_password@localhost:5432/oxh_prior_auth_test` |

---

## 3. Test and Quality Commands

```bash
ruff format --check .
ruff check .
mypy app
pytest
alembic check
```

---

## 4. Single-File Postman & Bruno Collection

Import `postman/Oxbridge-PA.postman_collection.json` directly into **Postman** or **Bruno** (no separate environment file required):

1. Open Bruno or Postman and import `postman/Oxbridge-PA.postman_collection.json`.
2. Run `docker compose exec api python -m app.tokens` and paste the `office`, `member`, `reviewer`, and `admin` JWTs into the corresponding collection variables (`office_token`, `member_token`, `reviewer_token`, `admin_token`).
3. Execute requests `01` through `09` in order. Collection scripts automatically compute `service_date` (7 days ahead) and capture `authorization_id`, `procedure_id_1`, `procedure_id_2`, and `support_request_id`.

---

## 5. Architecture & Data Model Overview

- **Logical Flow:** Four actors (`Office`, `Member`, `Reviewer`, `Admin`) authenticate via HS256 JWTs (`iss=oxh-identity`, `aud=oxh-pa`), enter role-specific FastAPI routers (`/v1/office`, `/v1/member`, `/v1/reviewer`, `/v1/admin`), and execute transactional workflows in `app/service.py` against PostgreSQL 16 (`submitted → in_review → decided`).
- **Entity Relationships:** Eleven PostgreSQL tables (`practices`, `members`, `users`, `practice_members`, `coverages`, `reviewer_patients`, `prior_authorizations`, `authorization_procedures`, `procedure_decisions`, `audit_events`, `submission_attempts`) enforcing referential integrity, `1..30` unit bounds, `decided` state approver check constraints, denial reason rules, practice-scoped idempotency keys, and database triggers preventing `UPDATE`/`DELETE` on `procedure_decisions` and `audit_events`.

---

## 6. Requirement-to-Implementation Traceability

| Requirement | Implementation Location |
| :--- | :--- |
| Role-separated HTTP contracts, OpenAPI responses & projections | `app/routers/{office,member,reviewer,admin}.py`, `app/schemas.py`, `app/errors.py` |
| State machine (`submitted → in_review → decided`) | `app/models.py` (`AuthorizationStatus`), `app/service.py` |
| Idempotent submission & race serialization | `app/service.py` (`pg_advisory_xact_lock`, SHA-256 payload hash), `uq_prior_authorizations_practice_idempotency` |
| Nationwide practice-local service date & inclusive coverage | `app/models.py` (`Practice.validate_timezone`), `app/service.py` (`ZoneInfo(practice.timezone)`, `effective_start <= service_date <= effective_end`) |
| Atomic single-winner queue claim & conflict checks | `app/service.py` (`UPDATE ... WHERE status='submitted' AND assigned_reviewer_id IS NULL RETURNING id`, `reviewer_patients`) |
| Atomic all-procedures decision & append-only audit | `app/service.py` (`SELECT ... FOR UPDATE`), `alembic/versions/0001_initial.py` & `0002_submission_attempts_and_decided_check.py` |
| RFC 9457 `application/problem+json`, `X-Request-ID` & `X-Correlation-ID` | `app/errors.py`, `app/main.py` |
| PHI-safe logging & independent submission diagnostics | `app/main.py`, `app/errors.py`, `app/service.py` (`submission_attempts`) |

---

## 7. API Surface & Partner Error-Code Catalogue

| Method | Path | Role | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/v1/office/prior-authorizations` | `office` | Submit prior authorization (`201` created, `200` idempotent replay; requires `Idempotency-Key`) |
| `GET` | `/v1/office/prior-authorizations` | `office` | List practice's prior authorizations (paginated) |
| `GET` | `/v1/office/prior-authorizations/{id}` | `office` | Read practice's prior authorization (no internal denial reason) |
| `GET` | `/v1/member/prior-authorizations` | `member` | List member's prior authorizations (paginated) |
| `GET` | `/v1/member/prior-authorizations/{id}` | `member` | Read member's prior authorization (no clinical note or denial reason) |
| `GET` | `/v1/reviewer/queue` | `reviewer` | List `submitted` requests excluding treated patients (clinical notes masked until claimed) |
| `GET` | `/v1/reviewer/assigned` | `reviewer` | List prior authorizations assigned to the authenticated reviewer |
| `POST` | `/v1/reviewer/prior-authorizations/{id}/claim` | `reviewer` | Atomically claim a submitted authorization into `in_review` |
| `GET` | `/v1/reviewer/prior-authorizations/{id}` | `reviewer` | Read assigned authorization with clinical note & decisions |
| `POST` | `/v1/reviewer/prior-authorizations/{id}/decision` | `reviewer` | Atomically decide every procedure and transition to `decided` |
| `GET` | `/v1/admin/prior-authorizations` | `admin` | Search and list all prior authorizations (`status`, `practice_id`, `member_id`, `assigned_reviewer_id`) |
| `GET` | `/v1/admin/prior-authorizations/{id}` | `admin` | Read full operational record |
| `POST` | `/v1/admin/prior-authorizations/{id}/reassign` | `admin` | Reassign an `in_review` authorization to another active non-conflicted reviewer |
| `GET` | `/v1/admin/prior-authorizations/{id}/audit-events` | `admin` | Read ordered append-only audit trail |
| `GET` | `/v1/admin/submission-attempts` | `admin` | Search non-PHI submission diagnostics (`practice_id`, `idempotency_key`, `correlation_id`, `accepted`, `since`, `until`) |
| `GET` | `/v1/admin/submission-attempts/{request_id}` | `admin` | Look up a single submission diagnostic by server `request_id` or client `correlation_id` |
| `GET` | `/health/live` | public | Process liveness probe |
| `GET` | `/health/ready` | public | Database readiness probe |

### Partner Error-Code Catalogue (`application/problem+json`)

Every error response returns `Content-Type: application/problem+json` conforming to `ProblemDetail` (`type`, `title`, `status`, `detail`, `instance`, `code`, `request_id`, `correlation_id`, `errors[]`):

| HTTP | Top-Level `code` | Field `errors[].code` | Meaning |
| :--- | :--- | :--- | :--- |
| `400` | `MISSING_IDEMPOTENCY_KEY` | `MISSING_IDEMPOTENCY_KEY` | Missing or blank `Idempotency-Key` header (`1–128` chars) on submission. |
| `401` | `AUTH_MISSING_TOKEN` / `AUTH_TOKEN_EXPIRED` / `AUTH_INVALID_TOKEN` / `AUTH_INVALID_CLAIMS` / `AUTH_USER_INACTIVE` | — | Missing, expired, malformed, or inactive Bearer JWT. |
| `403` | `AUTH_FORBIDDEN_ROLE` / `AUTH_ROLE_MISMATCH` / `AUTH_ROLE_BINDING_INVALID` / `REVIEWER_PATIENT_CONFLICT` | — | Caller lacks endpoint role or has a treating conflict of interest. |
| `404` | `AUTHORIZATION_NOT_FOUND` / `SUBMISSION_ATTEMPT_NOT_FOUND` | — | Resource does not exist or is concealed due to tenant/conflict isolation. |
| `409` | `IDEMPOTENCY_KEY_CONFLICT` / `AUTHORIZATION_CLAIM_CONFLICT` / `INVALID_AUTHORIZATION_STATE` / `DECISION_ALREADY_EXISTS` | `IDEMPOTENCY_KEY_CONFLICT` | Idempotency key reused with a different payload, concurrent claim lost, or invalid state transition. |
| `422` | `VALIDATION_FAILED` | `SCHEMA_*` | Request JSON failed Pydantic schema validation (bad UUID, ICD-10, CPT/HCPCS, `units` outside `1..30`, duplicate procedure code, or missing `denial_reason`). |
| `422` | `BUSINESS_RULE_VIOLATION` | `SERVICE_DATE_IN_PAST`, `SERVICE_DATE_TOO_FAR`, `MEMBER_NOT_LINKED`, `NO_ACTIVE_COVERAGE` | Request failed clinical/eligibility validation (`NO_ACTIVE_COVERAGE` is only evaluated after `MEMBER_NOT_LINKED` passes). |
| `422` | `INCOMPLETE_PROCEDURE_DECISIONS` / `DUPLICATE_PROCEDURE_DECISION` / `INVALID_TARGET_REVIEWER` / `REVIEWER_PATIENT_CONFLICT` | `UNKNOWN_PROCEDURE_ID`, `MISSING_PROCEDURE_DECISION`, `DUPLICATE_PROCEDURE_DECISION`, `INVALID_TARGET_REVIEWER`, `REVIEWER_PATIENT_CONFLICT` | Reviewer decision set does not match the authorization's procedures 1:1, or admin reassignment target is invalid/conflicted. |

---

## 8. Artifact Structure

- `app/`: FastAPI application (`main.py`), settings (`config.py`), SQLAlchemy 2 session management (`database.py`), domain models (`models.py`), Pydantic v2 commands/projections (`schemas.py`), JWT/RBAC (`auth.py`), RFC 9457 error handlers (`errors.py`), transactional domain logic (`service.py`), deterministic seeding (`seed.py`), token generator (`tokens.py`), and role routers (`routers/`).
- `alembic/`: Alembic configuration and explicit migrations (`0001_initial.py`, `0002_submission_attempts_and_decided_check.py`).
- `sql/`: Database bootstrap script (`create_databases.sql`) creating `oxh_prior_auth` and `oxh_prior_auth_test`.
- `postman/`: Single-file Postman v2.1 / Bruno collection (`Oxbridge-PA.postman_collection.json`).
- `tests/`: Pytest suite (`conftest.py`, `test_api.py`) running all 11 behavioral tests against real PostgreSQL.

---

## 9. Decisions, Assumptions & Architectural Trade-offs

### 9.1 Resolving Unclear or Contradictory UM Requirements

| Ambiguity / Tension in Requirements | Decision & Rationale |
| :--- | :--- |
| **"Tell them exactly what to fix" vs. HIPAA / cross-tenant privacy** | Schema (`422`) and business rule (`422`) failures return structured RFC 9457 `application/problem+json` field errors (`errors[]` with `code`, `field`, and `message`) so partner EHRs can programmatically fix invalid fields. Coverage eligibility (`NO_ACTIVE_COVERAGE`) is only evaluated **after** `MEMBER_NOT_LINKED` succeeds so practices cannot probe coverage for unlinked members. Cross-tenant or conflict-restricted resource lookups return `404 Not Found` rather than `403 Forbidden`. |
| **">30 units is almost always a typo"** | Online API submissions enforce `1 <= units <= 30` in both Pydantic (`ProcedureSubmissionItem.validate_units`) and a PostgreSQL `CHECK` constraint (`ck_auth_procedures_units_1_30`), returning an explicit field error explaining the 30-unit online typo guard and directing legitimate high-unit cases to UM exception review. Duplicate `(code_system, code)` pairs in one request are also rejected (`422`) so quantities cannot be split across lines to bypass the guard. |
| **"Offices see the requests they submitted" vs. "Clinical notes and reviewer reasoning are internal"** | Office visibility is scoped to `practice_id` so any coordinator at the submitting practice can track and follow up on the office's cases. `OfficeAuthorizationResponse` returns the clinical note the practice itself submitted plus per-procedure `outcome` (`approved` / `denied` / `null`), while omitting internal reviewer `denial_reason` notes (`MemberAuthorizationResponse` omits both `clinical_note` and `denial_reason`). Reviewer and Admin projections expose full internal reasoning. |
| **"Nationwide, from Maine to Hawaii" vs. "Already happened" / "More than a year out"** | `service_date` is evaluated against `datetime.now(ZoneInfo(practice.timezone)).date()`. Same-day (`service_date == practice_today`) is accepted; dates strictly before `practice_today` fail with `SERVICE_DATE_IN_PAST`; dates strictly after `practice_today` + 1 calendar year (handling Feb 29 leap years gracefully) fail with `SERVICE_DATE_TOO_FAR`. Coverage is inclusive (`effective_start <= service_date <= effective_end`). |
| **Conflict of interest & minimum-necessary PHI in queue** | Conflicted members (`reviewer_patients`) are excluded from `GET /v1/reviewer/queue` and return `404` on `claim`/`get`. Unclaimed queue items omit `clinical_note` (`null`) until claimed (`GET /v1/reviewer/assigned` and `GET /v1/reviewer/prior-authorizations/{id}` return full clinical notes for assigned cases). `decide_authorization` re-checks `reviewer_patients` inside `FOR UPDATE` (`403 REVIEWER_PATIENT_CONFLICT`), and admins can reassign `in_review` cases via `POST /v1/admin/prior-authorizations/{id}/reassign`. |
| **Preventing unattributed approvals ("no record of who approved it")** | Every procedure decision requires non-null `decided_by_user_id` and `decided_at`, the parent `prior_authorizations` row enforces `(status <> 'decided') OR (decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)`, and an immutable `AuditEvent` (`authorization.decided`) is written in the same ACID transaction. PostgreSQL `BEFORE UPDATE OR DELETE` triggers enforce append-only immutability on `procedure_decisions` and `audit_events` (in production AWS RDS, migrations run under a dedicated owner role while the API runtime role has DDL and `TRUNCATE` privileges revoked). |
| **Support desk diagnostics ("my submission didn't go through")** | Every HTTP response carries a server-generated UUID `X-Request-ID` (never trusting client-supplied IDs for uniqueness) and echoes any client `X-Correlation-ID`. Every submission attempt—including `201` created, `200` idempotent replay, `409` idempotency conflict, `422` schema validation failure (codes only, no raw values), and `422` business rule failure—is committed in an independent transaction to `submission_attempts` and is searchable via `GET /v1/admin/submission-attempts` and `GET /v1/admin/submission-attempts/{request_id}`. |

### 9.2 Architectural Scope & Security Posture

- **Why Modular Monolith:** A single stateless deployable unit with strict module boundaries (`routers` → `service` → `models`) guarantees ACID transactions across authorization state changes, procedure decisions, and audit logging without distributed two-phase commit overhead, while keeping role routers isolated for upcoming roles (such as `auditor`).
- **Intentional Exclusions:** Microservices, generic repository interfaces, event sourcing, Kafka/message brokers, external rules engines, Kubernetes/Terraform IaC, binary document storage, appeals, retro-review workflows, and wishlist items (decision webhooks and reviewer workload summary counts) were excluded to keep every line focused on core transactional invariants.
- **Security & HIPAA Posture:** Enforces role-isolated projections, minimum-necessary PHI in queue views, `404` concealment on cross-tenant/conflicted lookups, PHI-free structured HTTP logs, minimal non-PHI submission diagnostics, non-root container execution, startup rejection of the dev JWT secret outside `development`/`test`, and append-only database audit triggers. Production deployment additionally requires BAAs, TLS in transit, KMS-backed encryption at rest, PHI read-access audit sinks, centralized SIEM log retention, separate migration vs. runtime DB roles, and external OIDC/JWKS secret rotation.

---

## 10. AI Collaboration Notes

- **What AI was asked to do:** Generate the initial FastAPI + SQLAlchemy 2 + Alembic project skeleton, Pydantic v2 role-specific schemas, RFC 9457 error formatting, Postman/Bruno collection, and PostgreSQL integration tests.
- **Where initial outputs fell short and what was changed:**
  1. *Token expiration time bomb:* Initial token helper defaulted `iat` to a static `2026-01-01` date with a 30-day TTL, causing tokens to expire after January 2026; changed `mint_token` to anchor `issued_at` to `datetime.now(UTC)` and added a startup guard rejecting the default dev secret in non-dev environments.
  2. *Support diagnostics & client `X-Request-ID` collisions:* Initial middleware trusted client `X-Request-ID` directly into a `UNIQUE` column (causing `500 IntegrityError` on reuse) and wrote `SubmissionAttempt` inside a savepoint that rolled back on `409` conflicts or skipped `200` replays and `422` schema errors; switched to server-generated UUID `request_id` + client `X-Correlation-ID`, committed `SubmissionAttempt` via an independent session across all outcomes, and added `GET /v1/admin/submission-attempts` search.
  3. *OpenAPI contract & privacy hardening:* Added explicit `responses=` with `ProblemDetail` across all routes so generated OpenAPI docs match `application/problem+json` behavior, short-circuited `NO_ACTIVE_COVERAGE` when `MEMBER_NOT_LINKED` fails to prevent cross-practice coverage probing, masked `clinical_note` on unclaimed reviewer queue entries, and added `GET /v1/reviewer/assigned` and `GET /v1/admin/prior-authorizations`.
