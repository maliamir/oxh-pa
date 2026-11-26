"""FastAPI application entrypoint, health endpoints, and safe request logging middleware."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import check_db_ready
from app.errors import register_exception_handlers
from app.routers import admin, member, office, reviewer

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("oxh.api")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Oxbridge Health Prior Authorization API",
        version="0.1.0",
        description=(
            "Stateless modular monolith supporting prior authorization submission, "
            "conflict-checked clinical review, role-scoped projections, and append-only audit. "
            "All error responses use RFC 9457 application/problem+json with machine-readable "
            "error codes and field-level diagnostics."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    register_exception_handlers(application)

    @application.middleware("http")
    async def request_correlation_and_safe_logging(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        req_id = str(uuid.uuid4())
        raw_corr = (
            request.headers.get("X-Correlation-ID", "").strip()
            or request.headers.get("X-Request-ID", "").strip()
        )
        corr_id = raw_corr[:128] if raw_corr else None

        request.state.request_id = req_id
        request.state.correlation_id = corr_id

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

        response.headers["X-Request-ID"] = req_id
        if corr_id:
            response.headers["X-Correlation-ID"] = corr_id

        # Log method, path, status, duration, and IDs only (never headers, bodies, or PHI)
        logger.info(
            "method=%s path=%s status=%s duration_ms=%s request_id=%s correlation_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            req_id,
            corr_id or "-",
        )
        return response

    @application.get(
        "/health/live",
        tags=["health"],
        summary="Process liveness probe",
        description="Returns HTTP 200 when the API process is running.",
    )
    def health_live() -> dict[str, str]:
        return {"status": "alive"}

    @application.get(
        "/health/ready",
        tags=["health"],
        summary="Database readiness probe",
        description="Executes SELECT 1 against PostgreSQL and returns 200 when ready or 503 otherwise.",
    )
    def health_ready() -> Response:
        if check_db_ready():
            return JSONResponse(status_code=200, content={"status": "ready", "database": "up"})
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unreachable"},
        )

    application.include_router(office.router)
    application.include_router(member.router)
    application.include_router(reviewer.router)
    application.include_router(admin.router)

    return application


app = create_app()
