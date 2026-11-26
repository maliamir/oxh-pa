# Oxbridge Health Prior Authorization API — Technical Architecture Brief

## Purpose and diagram-generation guidance

This document describes the implemented system in this repository and is intended as source material for a separate session that creates draw.io diagrams. It supplements [README.md](./README.md); when an overview statement and implementation details differ, use the code and migrations referenced below as the diagram source of truth.

Recommended output: a multi-page draw.io document with the following views:

1. **System context and deployment** — actors, API, PostgreSQL, local/demo tooling, and trust boundaries.
2. **Application components** — FastAPI request path, role routers, shared auth/error handling, service layer, and persistence.
3. **Relational data model** — tables, foreign keys, cardinalities, uniqueness, and append-only trigger behavior.
4. **Submission sequence** — validation, tenant-local eligibility, idempotency, main transaction, and independent diagnostics.
5. **Reviewer workflow sequence and state machine** — queue, claim race, reassignment, complete decision set, and audit.
6. **Role/data visibility matrix** — endpoint groups, tenant scope, and field-level projection differences.
7. **Request diagnostics and error flow** — correlation identifiers, RFC 9457 error output, logs, and support lookup.

Keep PHI-bearing authorization data visually distinct from operational diagnostics. Do not depict services or integrations not implemented here as existing runtime components. The project is a stateless modular monolith, not a set of microservices.

## 1. System summary

| Concern | Implemented design |
|---|---|
| Product | Prior authorization submission, eligibility checks, conflict-aware clinical review, decisions, and operational support lookup |
| Application | Python 3.12, FastAPI, synchronous SQLAlchemy 2, Pydantic v2 |
| Persistence | PostgreSQL 16, psycopg 3, Alembic migrations |
| Runtime topology | One stateless API process/container and one PostgreSQL service in local Compose |
| Authentication | HS256 JWT bearer tokens; issuer `oxh-identity`, audience `oxh-pa`; caller is resolved to an active `users` row |
| Authorization | Four role-specific routers: office, member, reviewer, admin; service-layer tenant and assignment checks |
| Lifecycle | `submitted` → `in_review` → `decided`; no reopen or appeal path is implemented |
| API errors | RFC 9457-style `application/problem+json`, machine-readable codes and field errors |
| External services | None required by the application code; no external IdP/JWKS client, message broker, cache, object store, or frontend |

Primary implementation references: [app/main.py](./app/main.py), [app/auth.py](./app/auth.py), [app/service.py](./app/service.py), [app/models.py](./app/models.py), [compose.yaml](./compose.yaml), [Dockerfile](./Dockerfile).

## 2. System context and deployment

### Actors and trust boundaries

- **Office user** submits and tracks cases belonging to their bound practice.
- **Member user** reads their own authorization status and procedure outcomes.
- **Reviewer user** browses eligible queue entries, claims a case, and records its final procedure decisions.
- **Admin user** searches operational records, reassigns reviewers, reads audit events, and searches submission diagnostics.
- Each actor calls the same HTTPS-capable HTTP API boundary with a bearer JWT. TLS termination is not configured in this repository; it is a deployment responsibility.
- The API validates JWT signature and required `sub`, `role`, `iss`, `aud`, and `exp` claims, then loads the user from PostgreSQL, checks active status, verifies the claimed role against the stored role, and checks required office/member bindings.
- PostgreSQL is the authoritative store for identity-role bindings, workflow state, PHI, decisions, audit events, and submission diagnostics.

### Local deployment path

```text
Caller / Postman
      │ HTTP :8000
      ▼
API container (Python 3.12, Uvicorn, non-root oxh)
      │ SQLAlchemy + psycopg
      ▼
PostgreSQL 16 container (:5432)
```

- Compose starts PostgreSQL and waits for its health check before starting the API.
- PostgreSQL initialization runs `sql/create_databases.sql`, creating `oxh_prior_auth` and `oxh_prior_auth_test` on first-volume initialization.
- The API container runs `alembic upgrade head`; it runs deterministic synthetic seeding when `SEED_ON_STARTUP=true`, then executes Uvicorn.
- The Compose defaults enable seeding. The settings/Dockerfile default is disabled unless the environment enables it.
- The API image uses a builder/runtime Dockerfile, installs only project runtime dependencies, runs as UID/GID `10001` (`oxh`), exposes port 8000, and has a liveness health check. Compose separately uses `/health/ready` for API readiness.
- The test fixture applies Alembic to the configured test database and reseeds it between tests. Tests require a reachable PostgreSQL database; the suite is not an in-memory SQLite suite.
- `app.tokens` produces synthetic demo tokens for local evaluation. It is not a production identity provider.

The repo does **not** configure production ingress, TLS, cloud infrastructure, runtime/migration database-role separation, secret rotation, encryption-at-rest, or centralized log/audit export. These are deployment assumptions/recommendations described in the README, not components to draw as implemented.

## 3. Application component view

```text
HTTP request
  └─ app.main: FastAPI app
       ├─ request middleware: request/correlation IDs + PHI-safe access log
       ├─ exception handlers: DomainError / request validation → Problem Detail
       ├─ app.auth: bearer JWT → active User → AuthenticatedActor → role check
       ├─ app.routers
       │    ├─ office.py
       │    ├─ member.py
       │    ├─ reviewer.py
       │    └─ admin.py
       ├─ app.schemas: request commands and role-specific response projections
       ├─ app.service: business rules, queries, transactions, audit and diagnostics
       ├─ app.models: SQLAlchemy entities and metadata
       └─ app.database: engine, session factory, request DB dependency
              └─ PostgreSQL
```

Responsibilities and edges:

- **`main.py`** creates the FastAPI application, registers handlers and middleware, exposes health endpoints, and mounts all four routers.
- **Middleware** assigns a new server UUID `X-Request-ID` on every request. It reads `X-Correlation-ID`, falling back to incoming `X-Request-ID`, trims it to 128 characters, and echoes it as `X-Correlation-ID` when present. It logs method, path, status, duration, request ID, and correlation ID only—not headers, body, or PHI.
- **`auth.py`** validates HS256 bearer tokens and loads the active user. `require_role(...)` is the route dependency; individual service functions also apply record-level ownership/assignment and conflict checks.
- **Routers** bind HTTP paths, query/header/body schemas, dependencies, and response projections to service functions. Routes are synchronous.
- **`schemas.py`** validates API commands and defines distinct Member, Office, Reviewer, Admin, AuditEvent, and SubmissionAttempt response types. CPT/HCPCS and ICD-10 format checks, unit bounds, duplicate procedure checks, and denied/approved reason rules are enforced here.
- **`service.py`** contains workflow operations and data access using the injected SQLAlchemy `Session`. It owns business-rule checks, locking, commit boundaries, projections, and audit/diagnostic writes.
- **`models.py`** defines PostgreSQL UUID, enum, JSONB, FK, check, unique, and index declarations. `Base.metadata` is consumed by Alembic.
- **`database.py`** creates a cached synchronous engine with `pool_pre_ping=True` and a cached session factory (`autocommit=False`, `autoflush=False`, `expire_on_commit=False`). FastAPI's DB dependency closes the request session after use.
- **`errors.py` / `exceptions.py`** translate domain and schema errors to a stable response envelope. Invalid office submission schemas also trigger a non-PHI diagnostic attempt write.

## 4. Data model for ERD

All primary identifiers are UUIDs. Times are timezone-aware timestamps; service dates and coverage boundaries are calendar dates. The following relationships are based on the model/migration foreign keys.

### Core and operational entities

| Table | Purpose and key attributes |
|---|---|
| `practices` | Practice tenant; unique `npi`, IANA `timezone`. |
| `members` | Insured member identity; unique `member_number`. |
| `users` | Authenticated principals; unique `email`, enum `role`, `is_active`, optional `member_id` and `practice_id` bindings. |
| `practice_members` | Practice/member association and `active` flag; unique `(practice_id, member_id)`. |
| `coverages` | Member plan and active inclusive interval `[effective_start, effective_end]`; check end ≥ start. |
| `reviewer_patients` | Reviewer/member treating relationship used for conflict-of-interest exclusion; unique `(reviewer_user_id, member_id)`. |
| `prior_authorizations` | Parent case; practice, member, submitting user, idempotency key and payload hash, diagnosis, service date, optional clinical note, lifecycle status, optional reviewer/claim timestamp, optional decision actor/time. |
| `authorization_procedures` | One requested CPT or HCPCS line and units under a parent authorization. |
| `procedure_decisions` | Final approved/denied outcome per procedure, optional denial reason, decision actor and timestamp. One row maximum per procedure. |
| `audit_events` | Lifecycle/action history tied to authorization and actor; includes actor role, action, request ID, JSONB details, timestamp. |
| `submission_attempts` | Support diagnostics for each submission attempt; request/correlation IDs, nullable actor/practice/idempotency key, accepted flag, error codes, optional authorization ID, timestamp. It intentionally excludes raw request/clinical-note content. |

### Cardinalities (for ERD connectors)

Use crow's-foot notation; `0..1` means optional FK.

- `practices 1 ── 0..* practice_members`; `members 1 ── 0..* practice_members`.
- `members 1 ── 0..* coverages`.
- `users 1 ── 0..* reviewer_patients`; `members 1 ── 0..* reviewer_patients`.
- `practices 1 ── 0..* prior_authorizations`; `members 1 ── 0..* prior_authorizations`.
- `users 1 ── 0..* prior_authorizations` as submitter; a separate optional user relationship is reviewer assignment; another optional user relationship is parent decision actor.
- `prior_authorizations 1 ── 1..* authorization_procedures` for accepted API-created cases (API schema requires at least one; DB FK itself does not enforce minimum child count).
- `authorization_procedures 1 ── 0..1 procedure_decisions`; each decision belongs to one authorization and references its deciding user.
- `prior_authorizations 1 ── 0..* audit_events`; `users 1 ── 0..* audit_events` as event actor.
- `practices 1 ── 0..* submission_attempts` and `users 1 ── 0..* submission_attempts` when corresponding nullable FKs are populated; `prior_authorizations 1 ── 0..* submission_attempts` when the attempt links to a known case.
- `users.member_id` and `users.practice_id` are independently nullable FKs. Role-specific validity (member users have a member binding, office users a practice binding) is enforced in application auth, not by a cross-column DB check.

### Important keys, constraints, and indexes

- `prior_authorizations`: unique `(practice_id, idempotency_key)`; status restricted to `submitted|in_review|decided`; if status is `decided`, `decided_by_user_id` and `decided_at` are required.
- `authorization_procedures`: units `1..30`; procedure system restricted to `CPT|HCPCS`.
- `procedure_decisions`: unique `procedure_id`; outcomes `approved|denied`; denial requires nonblank `denial_reason`, approval requires it to be null.
- `coverages`: end date cannot predate start date.
- Indexes support practice/member authorization history, queue status/date, assigned reviewer, procedure lookup by authorization, coverage lookup by member/date, audit lookup by authorization/time and request ID, and submission attempt search by practice/time, correlation ID, and idempotency key.
- PostgreSQL `BEFORE UPDATE OR DELETE` triggers reject mutation of both `procedure_decisions` and `audit_events`. These tables are append-only at the DB trigger level; other tables are not covered by these triggers.
- Migrations are `0001_initial` then `0002_support_and_decided`; the latter adds the parent decided-actor constraint and nullable/additional correlation fields and indexes for diagnostics.

## 5. Lifecycle / state machine

```text
                  reviewer atomic claim
  ┌───────────┐  ───────────────────────►  ┌───────────┐
  │ submitted │                            │ in_review │
  └───────────┘                            └───────────┘
                                                │
                            complete, valid per-procedure decision set
                                                ▼
                                          ┌─────────┐
                                          │ decided │
                                          └─────────┘
```

- **Create:** accepted office submission creates `submitted` parent and procedure rows.
- **Claim:** a reviewer changes `submitted` to `in_review`, becomes assigned reviewer, and receives `claimed_at`.
- **Reassign:** admin may change the assigned reviewer only while `in_review`; this does not change the status. The previous reviewer immediately loses assigned-record access.
- **Decide:** assigned reviewer must provide exactly one decision for every procedure. The parent moves to `decided`; every procedure receives an immutable decision and the parent records deciding user/time.
- There is no implemented transition from `in_review` back to `submitted`, from `decided` to another state, partial decision, appeal, or retro-review process.
- Database status checks restrict allowed labels and the decided check requires a parent approver/time; application service enforces valid transitions and full decision completeness.

## 6. API surface and data access

All authorization endpoints require bearer authentication and the matching role. Paths below are mounted under `/v1`.

| Role | Method and path | Behavior and data scope |
|---|---|---|
| Public | `GET /health/live` | Process liveness; no DB dependency. |
| Public | `GET /health/ready` | Executes `SELECT 1`; returns 503 when DB is unreachable. |
| Office | `POST /office/prior-authorizations` | Submit; practice derived from actor; requires `Idempotency-Key`; returns 201 new / 200 identical replay. |
| Office | `GET /office/prior-authorizations` | Practice-scoped list, optional status, newest first. |
| Office | `GET /office/prior-authorizations/{id}` | Practice-scoped detail; hidden cross-practice as 404. |
| Member | `GET /member/prior-authorizations` | Authenticated member's list, newest first. |
| Member | `GET /member/prior-authorizations/{id}` | Own-member detail; other member as 404. |
| Reviewer | `GET /reviewer/queue` | FIFO submitted requests excluding treating conflicts; clinical note withheld. |
| Reviewer | `GET /reviewer/assigned` | Cases assigned to this reviewer; defaults to `in_review`; includes notes. |
| Reviewer | `POST /reviewer/prior-authorizations/{id}/claim` | Conditional atomic claim; treating conflict concealed as 404; competing claim receives 409. |
| Reviewer | `GET /reviewer/prior-authorizations/{id}` | Only current assignee may read; otherwise 404. |
| Reviewer | `POST /reviewer/prior-authorizations/{id}/decision` | Complete final procedure decision set for an assigned `in_review` case. |
| Admin | `GET /admin/prior-authorizations` | Cross-tenant search by status, practice, member, reviewer. |
| Admin | `GET /admin/prior-authorizations/{id}` | Full operational record. |
| Admin | `POST /admin/prior-authorizations/{id}/reassign` | Reassign in-review case to active, non-conflicted reviewer. |
| Admin | `GET /admin/prior-authorizations/{id}/audit-events` | Ordered lifecycle audit. |
| Admin | `GET /admin/submission-attempts` | Search diagnostics by practice, idempotency/correlation ID, accepted flag, time range. |
| Admin | `GET /admin/submission-attempts/{request_id}` | Lookup by server request ID or client correlation ID. |

List routes use bounded pagination (`limit` 1–200, default 50; nonnegative `offset`, default 0). API request/response definitions and route metadata are in [app/routers/](./app/routers/) and [app/schemas.py](./app/schemas.py).

### Role projection differences

| Data field | Office | Member | Reviewer queue (unclaimed) | Reviewer assigned | Admin |
|---|---:|---:|---:|---:|---:|
| Authorization metadata / procedure codes / status | Yes | Yes (own only) | Yes | Yes (assigned only) | Yes |
| `clinical_note` | Yes | No | No (`null`) | Yes | Yes |
| Procedure outcome | Yes | Yes | Yes, normally null | Yes | Yes |
| Internal `denial_reason` | No | No | Yes by projection shape (normally null before decision) | Yes | Yes |
| Submitter, idempotency key, full operational timestamps | Limited | No | No | No | Yes |
| Audit event stream | No | No | No | No | Yes, separate endpoint |

Reviewer queue records still include procedure details, but no clinical note until claimed. For member/office, internal reviewer reasoning is omitted by response-model construction, rather than merely hidden by UI. Admin authorization data access is global across practices.

## 7. Workflow sequence details

### A. Office submission and idempotency

Suggested lifelines: Office client → API middleware → auth dependency → office router/schema → service → PostgreSQL; service → independent diagnostic session → PostgreSQL.

1. Middleware generates server `request_id`, derives optional correlation ID, and later emits response headers and a PHI-safe access log.
2. Pydantic validates UUID/date/code formats, procedure units `1..30`, nonempty procedures, and uniqueness of `(code_system, code)`. Schema failure becomes 422 `VALIDATION_FAILED`. For this specific office POST, the error handler attempts to persist diagnostic error codes without raw values.
3. Auth validates bearer JWT and persisted active user/role; route requires `office` and valid practice binding.
4. Service checks `Idempotency-Key` (trimmed, nonblank, max 128). Missing/invalid key creates a rejected attempt and returns 400.
5. Service hashes canonical request JSON with SHA-256. It acquires PostgreSQL transaction advisory lock derived from `practice_id:key`, then looks up that practice/key:
   - Same key + same hash: persist accepted replay diagnostic independently; return existing case with 200.
   - Same key + different hash: persist rejected conflict diagnostic independently; return 409 `IDEMPOTENCY_KEY_CONFLICT`.
   - No prior key: continue eligibility checks while holding the transaction lock.
6. Eligibility: determine today's date in the practice's IANA timezone; reject earlier dates or dates later than one calendar year ahead (Feb 29 limit becomes Feb 28). Verify active practice-member link. Only after link passes, verify active coverage includes service date inclusively. Business failures return 422 with field errors and attempt diagnostics.
7. On success, insert parent and procedures and append `authorization.submitted` audit event inside the same main DB transaction; commit.
8. After commit, persist accepted `SubmissionAttempt` in an independent session; reload parent/procedures and return 201.

**Boundary note:** submission diagnostic writes use an independent DB session to survive the main request transaction's rollbacks and idempotent paths. Normal successful authorization plus its submission audit event are committed together; the diagnostic record is committed separately. Schema-validation diagnostic persistence is best-effort: a failure logs a warning and does not replace the client validation response.

### B. Reviewer queue, claim, and reassignment

1. Queue query selects `submitted` cases in ascending creation time and excludes members present in `reviewer_patients` for the calling reviewer.
2. Claim first checks existence and conflict, then executes a conditional SQL update requiring target ID, `submitted`, and unassigned reviewer. Exactly one concurrent caller can update/return the ID; loser receives 409 `AUTHORIZATION_CLAIM_CONFLICT`.
3. Successful claim sets `in_review`, `assigned_reviewer_id`, `claimed_at`, `updated_at`; appends `authorization.claimed`; commits; reloads and returns full reviewer projection.
4. Admin reassign takes a row lock on parent, requires `in_review`, validates target active reviewer and no treating conflict, replaces assignee and claim time, appends `authorization.reassigned`, then commits. No reassignment to a member-conflicted target.

### C. Final decision

1. Route authenticates reviewer. Service loads parent/procedure decisions with `SELECT ... FOR UPDATE`.
2. Require case exists and current actor is assigned; otherwise 404. Require status `in_review`; otherwise 409.
3. Re-check reviewer/member conflict; conflict at decision time returns 403 `REVIEWER_PATIENT_CONFLICT`.
4. Compare submitted decision IDs against all child procedure IDs. Duplicate, unknown, or missing procedure IDs fail before writes.
5. Reject if a child decision already exists. Pydantic and DB also enforce denied reason required/nonblank and approved reason null.
6. Insert a decision for each procedure, set parent status/decider/time, append `authorization.decided`; all in one transaction. Commit yields either complete state change + audit, or none.
7. PostgreSQL uniqueness and append-only triggers provide additional persistence-level protection. The immutable per-procedure decision and audit rows are not updated/deleted through normal DB operations.

## 8. Request IDs, errors, and diagnostics

- Every response includes server-generated `X-Request-ID`; caller-provided `X-Request-ID` is not used as the server's uniqueness key. `X-Correlation-ID` is optional, echoed, included in Problem Detail, and searchable. Incoming `X-Request-ID` is used as a fallback correlation value only if `X-Correlation-ID` is absent.
- Logs include method/path/status/duration/IDs only. Do not draw request/response payloads as log contents.
- Domain and validation failures use `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, `code`, `request_id`, `correlation_id`, and `errors[]` (`code`, `field`, `message`).
- Submission diagnostics represent each office submission attempt, including accepted create/replay and rejected schema, business-rule, missing-key, or idempotency-conflict outcomes. They include only IDs/flags/error codes and omit raw payload values and clinical notes.
- Admin is the only role with search/get routes for diagnostic attempts. `request_id` is unique; the lookup route also accepts correlation ID and returns the newest matching attempt.
- Database readiness is separately represented by `/health/ready`; `/health/live` only indicates that the process is running.

## 9. Validation and reference artifacts

- `tests/test_api.py` contains 11 PostgreSQL behavioral tests covering idempotent retries and concurrent submissions, schema/business errors and diagnostic lookups, conflict masking and note minimization, atomic decisions and role projections, partial decision rejection, concurrent claims, admin reassignment, append-only audit triggers, timezone/duplicate procedure behavior, expired JWTs, and production-secret validation.
- `tests/conftest.py` migrates and reseeds the real test database, then overrides FastAPI's request-session dependency.
- `postman/Oxbridge-PA.postman_collection.json` includes a sequential demo flow for health, office submission, reviewer queue/claim/decision, member read, admin audit, invalid submission, and support lookup.
- Quality commands documented in the README: `ruff format --check .`, `ruff check .`, `mypy app`, `pytest`, and `alembic check`.

### Source map

- Application entrypoint and observability: [app/main.py](./app/main.py), [app/errors.py](./app/errors.py), [app/exceptions.py](./app/exceptions.py)
- Domain and persistence: [app/models.py](./app/models.py), [app/database.py](./app/database.py), [app/service.py](./app/service.py)
- Identity/configuration/schemas: [app/auth.py](./app/auth.py), [app/config.py](./app/config.py), [app/schemas.py](./app/schemas.py)
- HTTP contracts: [app/routers/](./app/routers/)
- Schema history: [alembic/versions/0001_initial.py](./alembic/versions/0001_initial.py), [alembic/versions/0002_submission_attempts_and_decided_check.py](./alembic/versions/0002_submission_attempts_and_decided_check.py)
- Runtime/test/development: [Dockerfile](./Dockerfile), [compose.yaml](./compose.yaml), [app/seed.py](./app/seed.py), [app/tokens.py](./app/tokens.py), [tests/](./tests/), [postman/Oxbridge-PA.postman_collection.json](./postman/Oxbridge-PA.postman_collection.json)
