#!/usr/bin/env bash
# Backup Open WebUI data volume, agents config, and env (without secrets print)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${1:-$ROOT/backups}"
mkdir -p "$OUT_DIR"
ARCHIVE="$OUT_DIR/platform-backup-$STAMP.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PROJECT="${COMPOSE_PROJECT_NAME:-openwebui-platform}"
VOLUME="${PROJECT}_webui_data"

echo "==> Backing up volume: $VOLUME"
if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  docker run --rm \
    -v "$VOLUME":/data:ro \
    -v "$TMP":/backup \
    alpine:3.20 \
    tar czf /backup/webui-data.tar.gz -C /data .
else
  echo "WARNING: volume $VOLUME not found — skipping data volume"
fi

echo "==> Copying config"
mkdir -p "$TMP/config"
cp -a config/. "$TMP/config/" 2>/dev/null || true
cp .env.example "$TMP/env.example" 2>/dev/null || true
# Backup .env into archive (store securely!)
if [[ -f .env ]]; then
  cp .env "$TMP/env.backup"
fi

echo "==> Creating archive $ARCHIVE"
tar czf "$ARCHIVE" -C "$TMP" .
echo "Backup complete: $ARCHIVE"
ls -lh "$ARCHIVE"
