#!/usr/bin/env bash
# Prepare an Ubuntu 24.04 VM for production deployment
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Production prep (Ubuntu 24.04 + Docker Compose)"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — EDIT SECRETS BEFORE CONTINUING"
fi

# Disable local override so ports are not all published
if [[ -f docker-compose.override.yml ]]; then
  mv docker-compose.override.yml docker-compose.override.yml.local-disabled
  echo "Renamed docker-compose.override.yml → docker-compose.override.yml.local-disabled"
fi

echo ""
echo "Next steps:"
echo "  1. Edit .env:"
echo "       ENVIRONMENT=production"
echo "       BASE_DOMAIN=ai.example.com"
echo "       WEBUI_URL=https://ai.example.com"
echo "       WEBUI_SECRET_KEY=<strong random>"
echo "       JWT_SECRET=<strong random>"
echo "       GATEWAY_API_KEYS=<strong random>"
echo "       OPENAI_API_KEY=<same as one GATEWAY_API_KEYS entry>"
echo "       ENABLE_SIGNUP=true  # first boot only, then false"
echo "       HERMES_*_BASE_URL=... for real agents"
echo "  2. Point agents away from mock-agent in config/agents.yaml or .env"
echo "  3. Place TLS certs in nginx/ssl/ (fullchain.pem, privkey.pem)"
echo "     and enable ssl.conf from ssl.conf.example"
echo "  4. Start:"
echo "       docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile production up -d --build"
echo ""
