#!/usr/bin/env bash
# Bootstrap local development environment
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Open WebUI Platform — local setup"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
else
  echo ".env already exists — leaving unchanged"
fi

# Ensure secrets look non-default on first run (soft warning only)
if grep -q "change-me" .env 2>/dev/null; then
  echo "WARNING: .env still contains 'change-me' placeholders. Fine for local; change before production."
fi

echo "==> Building and starting stack..."
docker compose build
docker compose up -d

echo ""
echo "Waiting for Gateway health..."
for i in $(seq 1 30); do
  if curl -fsS "http://localhost:${GATEWAY_PORT:-8000}/health" >/dev/null 2>&1; then
    echo "Gateway is healthy."
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    echo "Gateway did not become healthy in time. Check: docker compose logs gateway"
    exit 1
  fi
done

echo ""
echo "Open WebUI:  http://localhost:${OPENWEBUI_PORT:-3000}"
echo "Gateway API: http://localhost:${GATEWAY_PORT:-8000}"
echo "Gateway docs: http://localhost:${GATEWAY_PORT:-8000}/docs"
echo "Models test:"
echo "  curl -s http://localhost:${GATEWAY_PORT:-8000}/v1/models -H \"Authorization: Bearer sk-gateway-dev-key\" | jq ."
echo ""
echo "Done. Create the first admin account in the Open WebUI signup form."
