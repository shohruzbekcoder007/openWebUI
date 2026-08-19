# Production Deployment (Ubuntu 24.04 VM)

## Prerequisites

- Ubuntu 24.04 LTS
- Docker Engine 24+ and Docker Compose plugin
- DNS A/AAAA record for `BASE_DOMAIN` (e.g. `ai.example.com`)
- Firewall: open 80/443 (and optionally SSH only)

### Install Docker (if needed)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
# log out/in
```

## Deploy

```bash
git clone <your-repo> openwebui-platform
cd openwebui-platform

# Generate secrets
bash scripts/gen-secrets.sh

cp .env.example .env
# Edit .env carefully (see checklist below)

# Disable local override
mv docker-compose.override.yml docker-compose.override.yml.local-disabled 2>/dev/null || true

# TLS (example: copy Let's Encrypt certs)
# sudo cp /etc/letsencrypt/live/ai.example.com/fullchain.pem nginx/ssl/
# sudo cp /etc/letsencrypt/live/ai.example.com/privkey.pem nginx/ssl/
# cp nginx/conf.d/ssl.conf.example nginx/conf.d/ssl.conf
# edit server_name in ssl.conf

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile production up -d --build
```

## Production `.env` checklist

```env
ENVIRONMENT=production
BASE_DOMAIN=ai.example.com
WEBUI_URL=https://ai.example.com
PUBLIC_API_URL=https://ai.example.com/api/gateway

WEBUI_SECRET_KEY=<from gen-secrets>
JWT_SECRET=<from gen-secrets>
GATEWAY_API_KEYS=sk-<from gen-secrets>
OPENAI_API_KEY=sk-<same as GATEWAY_API_KEYS>

# First boot only:
ENABLE_SIGNUP=true
# After creating admin:
# ENABLE_SIGNUP=false

# Real Hermes agents (not mock)
HERMES_AGRICULTURE_BASE_URL=https://...
HERMES_GIS_BASE_URL=https://...
# ...

OPENAI_API_BASE_URL=http://gateway:8000/v1
ENABLE_OLLAMA_API=false
CORS_ORIGINS=https://ai.example.com

NGINX_SSL_ENABLED=true
```

## Single-command start (after configured)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile production up -d
```

## Health checks

```bash
docker compose ps
curl -fsS https://ai.example.com/nginx-health
curl -fsS http://localhost:8000/health   # if gateway port published
docker compose logs -f gateway open-webui nginx
```

## Automatic restart

All services use `restart: unless-stopped`. After VM reboot:

```bash
# Docker starts on boot; containers restart automatically
sudo systemctl enable docker
```

## Optional PostgreSQL

```bash
# In .env:
POSTGRES_ENABLED=true
DATABASE_URL=postgresql://openwebui:PASSWORD@postgres:5432/openwebui

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile production --profile postgres up -d
```

## Logging

JSON-file driver with rotation (`LOG_MAX_SIZE`, `LOG_MAX_FILE`).

```bash
docker compose logs -f --tail=200 gateway
```

## Backup / restore

```bash
./scripts/backup.sh
./scripts/restore.sh backups/platform-backup-....tar.gz
```

## Scaling notes

This design targets a **single VM**. The Gateway is stateless (agents config + Redis for rate limits), so you can later place multiple gateway replicas behind nginx if needed without changing Open WebUI.
