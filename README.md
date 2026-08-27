# Open WebUI + Hermes Gateway Platform

Production-ready AI platform that **reuses official Open WebUI** as the frontend and routes all model traffic through a **FastAPI Gateway** to unlimited **Hermes Agents**.

> Open WebUI source is **not** modified.  
> Local and production use the **same code** — only `.env` (and TLS) change.

```
Browser → Open WebUI → Gateway (OpenAI-compatible) → Hermes Agent
```

| Environment | UI | API |
|-------------|----|-----|
| **Local** | http://localhost:3000 | http://localhost:8000 |
| **Production** | https://ai.example.com | via Nginx / Gateway |

---

## Architecture (short)

- **Open WebUI** — official Docker image; connects **only** to the Gateway via `OPENAI_API_BASE_URL`
- **Gateway** — OpenAI-compatible API (`/v1/models`, `/v1/chat/completions`), auth, streaming, rate limits, agent routing
- **Hermes Agents** — configured in `config/agents.yaml` (Qishloq xo'jaligi, GIS, Statistics, Press reliz, Programmer, Translator, …)
- **Redis** — optional rate-limit store
- **PostgreSQL** — optional Open WebUI database
- **Nginx** — production reverse proxy (HTTPS, WebSocket, SSE, large uploads)
- **Mock agent** — local OpenAI-compatible stand-in so the stack works before real Hermes services exist

See [docs/architecture.md](docs/architecture.md).

---

## Project structure

```
project/
├── docker-compose.yml          # Core stack
├── docker-compose.override.yml # Local ports / debug
├── docker-compose.prod.yml     # Production overlay
├── .env.example
├── .env                        # Your secrets (gitignored)
├── config/
│   └── agents.yaml             # Hermes agents (no hardcoding)
├── gateway/                    # FastAPI OpenAI-compatible gateway
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
├── open-webui/                 # Notes only (official image used)
├── nginx/                      # Reverse proxy configs
├── docker/
│   └── mock-agent/             # Dev Hermes stand-in
├── scripts/                    # setup, backup, restore, secrets
├── docs/
└── README.md
```

---

## Quick start (local)

### Prerequisites

- Docker Engine + Docker Compose v2
- Ports free: `3000`, `8000` (and `9010` for mock agent)

### Run

```bash
# 1. Configure
cp .env.example .env
# Defaults work for local development

# 2. Start
docker compose up --build -d

# Or with live reload (Compose Watch)
docker compose watch
```

### Open

1. UI: http://localhost:3000 — create the first admin account  
2. Gateway docs: http://localhost:8000/docs  
3. Model list:

```bash
curl -s http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-gateway-dev-key"
```

You should see **Qishloq xo'jaligi**, **GIS**, **Statistics**, **Press reliz**, **Programmer**, **Translator**.

4. In Open WebUI, pick a model (e.g. **GIS**) and chat.  
   The Gateway routes to that Hermes agent (mock agent by default).

### Stop

```bash
docker compose down
```

---

## Installation (Ubuntu 24.04 production)

Full guide: [docs/production.md](docs/production.md).

```bash
# After cloning and editing .env + TLS:
bash scripts/setup-production.sh

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile production up -d --build
```

Single VM start (once configured):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile production up -d
```

---

## Configuration

### Environment variables

Copy `.env.example` → `.env`. Important keys:

| Variable | Description |
|----------|-------------|
| `OPENWEBUI_PORT` | Host port for Open WebUI (local) |
| `GATEWAY_PORT` | Host port for Gateway (local) |
| `BASE_DOMAIN` | Public domain (production) |
| `WEBUI_URL` | Public UI URL (`http://localhost:3000` or `https://ai.example.com`) |
| `WEBUI_SECRET_KEY` | Open WebUI session secret |
| `JWT_SECRET` | Gateway JWT secret (future auth extensions) |
| `GATEWAY_API_KEYS` | Comma-separated keys accepted by Gateway |
| `OPENAI_API_KEY` | Key Open WebUI sends (must match a Gateway key) |
| `OPENAI_API_BASE_URL` | Must be `http://gateway:8000/v1` on Docker network |
| `ENABLE_OLLAMA_API` | `false` — models only from Gateway |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
| `RATE_LIMIT_RPM` | Requests per minute per API key (`0` = off) |
| `CORS_ORIGINS` | Allowed browser origins for Gateway |
| `HERMES_*_BASE_URL` | Real Hermes endpoints |

Generate production secrets:

```bash
bash scripts/gen-secrets.sh
```

### Open WebUI → Gateway only

Open WebUI is configured **not** to talk to OpenAI or Ollama directly:

```env
OPENAI_API_BASE_URL=http://gateway:8000/v1
OPENAI_API_KEY=sk-gateway-dev-key
ENABLE_OLLAMA_API=false
```

### Dynamic models

`GET /v1/models` returns each **enabled** agent as a model. Selecting **GIS** in the UI sets `model: "gis"` (or the display name resolved by the Gateway), which routes to the GIS agent.

---

## Gateway

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/health` | No | Liveness |
| `GET` | `/ready` | No | Readiness |
| `GET` | `/v1/models` | Yes | Model discovery |
| `POST` | `/v1/chat/completions` | Yes | Chat (+ SSE streaming) |
| `GET` | `/v1/agents` | Yes | List agents |
| `POST` | `/v1/agents/reload` | Yes | Hot-reload `agents.yaml` |

Auth: `Authorization: Bearer <key>` or `X-API-Key`.

### Streaming

Supports OpenAI-style **Server-Sent Events** (`text/event-stream`), chunked deltas, and `data: [DONE]` — compatible with Open WebUI / ChatGPT clients. Nginx is configured with `proxy_buffering off` and long read timeouts.

### Responsibilities implemented

- Route by model → agent  
- API key authentication  
- Structured logging  
- SSE streaming  
- Rate limiting (memory / Redis)  
- OpenAI-style errors  
- Model discovery  
- Health checks  
- Agent selection from config  
- Feature flags for future MCP / RAG  

---

## Adding a new Hermes Agent

See [docs/adding-agents.md](docs/adding-agents.md).

1. Append an entry to `config/agents.yaml`  
2. Optionally set `HERMES_*` env vars  
3. `docker compose restart gateway` or `./scripts/reload-agents.sh`  
4. Model appears in Open WebUI  

**No Open WebUI code changes. No Gateway code changes.**

---

## Changing agent APIs

Update `base_url`, `endpoint`, `headers`, `api_key`, or `model` in `agents.yaml` or via environment placeholders. Reload the gateway.

---

## Updating Open WebUI

```bash
# Pin a release in .env for production, e.g.:
# OPENWEBUI_IMAGE=ghcr.io/open-webui/open-webui:v0.6.15

docker compose pull open-webui
docker compose up -d open-webui
```

Data lives in the Docker volume `*_webui_data`.

---

## Docker services

```bash
docker compose ps
docker compose logs -f gateway
docker compose logs -f open-webui
```

| Feature | Implementation |
|---------|----------------|
| Networks | `platform` bridge network |
| Volumes | `open-webui-data`, `redis-data`, `postgres-data` |
| Healthchecks | Gateway, Open WebUI, Redis, mock-agent, nginx |
| Restart | `unless-stopped` |
| Logging | json-file rotation (`LOG_MAX_SIZE` / `LOG_MAX_FILE`) |

---

## Nginx (production)

- Reverse proxy to Open WebUI  
- Optional path `/api/gateway/` → Gateway  
- WebSocket upgrade headers  
- SSE-friendly (`proxy_buffering off`, long timeouts)  
- Large uploads (`client_max_body_size`)  
- TLS example: `nginx/conf.d/ssl.conf.example`  

---

## Backup & restore

```bash
./scripts/backup.sh
# → backups/platform-backup-<timestamp>.tar.gz

./scripts/restore.sh backups/platform-backup-<timestamp>.tar.gz
docker compose up -d
```

Backups include Open WebUI volume data, `config/`, and `.env`.

---

## Local development tips

```bash
# Watch mode (rebuild/sync gateway on change)
docker compose watch

# Hot-reload agents only
./scripts/reload-agents.sh

# Chat test (non-stream)
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"programmer","messages":[{"role":"user","content":"Hello"}],"stream":false}'

# Streaming test
curl -N http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gis","messages":[{"role":"user","content":"What is EPSG:4326?"}],"stream":true}'
```

Point agents at real Hermes services (host or remote):

```env
HERMES_GIS_BASE_URL=http://host.docker.internal:9002
HERMES_GIS_API_KEY=sk-real-key
HERMES_GIS_MODEL=gis-prod
```

Then recreate the gateway:

```bash
docker compose up -d gateway --force-recreate
```

---

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md).

Common checks:

```bash
curl -s http://localhost:8000/health
docker compose exec gateway curl -sS http://mock-agent:9010/health
docker compose logs gateway --tail=50
```

---

## Security notes

- Never commit `.env` or TLS private keys  
- Rotate `WEBUI_SECRET_KEY`, `JWT_SECRET`, `GATEWAY_API_KEYS` in production  
- Set `ENABLE_SIGNUP=false` after creating the admin user  
- Prefer TLS termination at Nginx  
- Keep Gateway unpublished on the public interface when Nginx fronts the stack (`docker-compose.prod.yml`)  

---

## License / third-party

- [Open WebUI](https://github.com/open-webui/open-webui) — use per its license  
- This repository’s Gateway and compose scaffolding — your project  

---

## Next steps

1. Replace mock agent URLs with real Hermes endpoints  
2. Pin `OPENWEBUI_IMAGE` to a version tag  
3. Enable Nginx TLS for production  
4. Turn off signup after bootstrap  
5. Schedule `scripts/backup.sh` via cron  
