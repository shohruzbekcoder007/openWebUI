#!/usr/bin/env bash
# Restore from a backup created by backup.sh
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <platform-backup-YYYYMMDDTHHMMSSZ.tar.gz>"
  exit 1
fi

ARCHIVE="$(realpath "$1")"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "Archive not found: $ARCHIVE"
  exit 1
fi

PROJECT="${COMPOSE_PROJECT_NAME:-openwebui-platform}"
VOLUME="${PROJECT}_webui_data"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> Extracting $ARCHIVE"
tar xzf "$ARCHIVE" -C "$TMP"

if [[ -d "$TMP/config" ]]; then
  echo "==> Restoring config/"
  mkdir -p config
  cp -a "$TMP/config/." config/
fi

if [[ -f "$TMP/env.backup" ]]; then
  echo "==> Restoring .env (existing .env backed up to .env.before-restore)"
  if [[ -f .env ]]; then
    cp .env .env.before-restore
  fi
  cp "$TMP/env.backup" .env
fi

if [[ -f "$TMP/webui-data.tar.gz" ]]; then
  echo "==> Restoring volume $VOLUME"
  docker volume create "$VOLUME" >/dev/null 2>&1 || true
  docker run --rm \
    -v "$VOLUME":/data \
    -v "$TMP":/backup \
    alpine:3.20 \
    sh -c "rm -rf /data/* /data/.[!.]* 2>/dev/null; tar xzf /backup/webui-data.tar.gz -C /data"
else
  echo "WARNING: no webui-data.tar.gz in archive"
fi

echo "Restore complete. Start stack with: docker compose up -d"
