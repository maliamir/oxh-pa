FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml ./
RUN pip install --prefix=/install .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:$PATH"

WORKDIR /app

RUN groupadd --system --gid 10001 oxh \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin oxh

COPY --from=builder /install /usr/local

COPY --chown=oxh:oxh alembic.ini ./
COPY --chown=oxh:oxh alembic ./alembic
COPY --chown=oxh:oxh app ./app

USER oxh:oxh

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).read()"

CMD ["sh", "-c", "alembic upgrade head && if [ \"${SEED_ON_STARTUP:-false}\" = \"true\" ]; then python -m app.seed; fi && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
