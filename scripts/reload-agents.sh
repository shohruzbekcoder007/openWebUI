#!/usr/bin/env bash
# Hot-reload agents.yaml via Gateway API
set -euo pipefail

PORT="${GATEWAY_PORT:-8000}"
KEY="${OPENAI_API_KEY:-sk-gateway-dev-key}"
BASE="${PUBLIC_API_URL:-http://localhost:$PORT}"

curl -fsS -X POST "$BASE/v1/agents/reload" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" | (command -v jq >/dev/null && jq . || cat)

echo ""
