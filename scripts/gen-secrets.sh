#!/usr/bin/env bash
# Generate strong secrets for production .env
set -euo pipefail

gen() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

echo "WEBUI_SECRET_KEY=$(gen)"
echo "JWT_SECRET=$(gen)"
echo "GATEWAY_API_KEYS=sk-$(gen)"
echo "OPENAI_API_KEY=<set equal to one of GATEWAY_API_KEYS>"
echo "POSTGRES_PASSWORD=$(gen)"
