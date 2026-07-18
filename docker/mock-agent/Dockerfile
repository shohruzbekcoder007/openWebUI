FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir fastapi uvicorn

COPY main.py .

EXPOSE 9010

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9010/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9010"]
