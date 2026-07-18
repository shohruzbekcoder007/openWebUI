# =============================================================================
# Hermes Gateway — production multi-stage image
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps (certs for HTTPS outbound to agents)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
FROM base AS deps

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# -----------------------------------------------------------------------------
FROM base AS runtime

# Non-root user
RUN groupadd --gid 1000 gateway \
    && useradd --uid 1000 --gid gateway --shell /bin/bash --create-home gateway

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY --chown=gateway:gateway app ./app

# Config is mounted at runtime; keep a placeholder dir
RUN mkdir -p /app/config && chown -R gateway:gateway /app

USER gateway

EXPOSE 8000

# Healthcheck uses the unauthenticated /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
