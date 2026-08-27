# Architecture

## High-level flow

```
Browser
   │
   ▼
Open WebUI  (official image)
   │  OPENAI_API_BASE_URL=http://gateway:8000/v1
   │  Authorization: Bearer <OPENAI_API_KEY>
   ▼
Hermes Gateway (FastAPI)
   │  GET  /v1/models          → list agents as models
   │  POST /v1/chat/completions → route by model id
   ▼
Hermes Agent (Qishloq xo'jaligi | GIS | Statistics | Press reliz | Programmer | Translator | …)
```

Open WebUI **never** calls Hermes agents directly. The Gateway is the only AI backend Open WebUI knows about.

## Components

| Service | Role | Image / build |
|---------|------|----------------|
| `open-webui` | Chat UI, auth, history, RAG UI | `ghcr.io/open-webui/open-webui` |
| `gateway` | OpenAI-compatible router | Built from `./gateway` |
| `redis` | Rate-limit counters (optional) | `redis:7-alpine` |
| `postgres` | Optional Open WebUI DB | `postgres:16-alpine` (profile) |

### Open WebUI data persistence

| Item | Detail |
|------|--------|
| Env | `DATABASE_URL` (passed into the `open-webui` container) |
| Default | PostgreSQL: `postgresql://…@postgres:5432/openwebui` |
| Who uses it | **Open WebUI only** (users, chats, settings) — not Hermes agents |
| Volume (PG) | `postgres-data` → `/var/lib/postgresql/data` |
| Volume (files) | `open-webui-data` → `/app/backend/data` (uploads, cache) |
| SQLite alt | `DATABASE_URL=sqlite:////app/backend/data/webui.db` |

Never set `DATABASE_URL=` (empty) — Open WebUI will crash on SQLAlchemy URL parse.
| `nginx` | TLS, reverse proxy | `nginx:1.27-alpine` (production) |
| `mock-agent` | Dev stand-in for Hermes | Built from `./docker/mock-agent` |

## Gateway responsibilities

- **Routing** — `model` field selects the Hermes agent
- **Authentication** — API keys (`GATEWAY_API_KEYS`)
- **Logging** — structured JSON logs
- **Streaming** — SSE / OpenAI chunk format
- **Rate limiting** — per API key (memory or Redis)
- **Error handling** — OpenAI-style error JSON
- **Model discovery** — agents → `/v1/models`
- **Health checks** — `/health`, `/ready`
- **Agent selection** — config-driven, hot-reload
- **Future MCP / RAG** — feature flags `ENABLE_MCP`, `ENABLE_RAG`

## Agent configuration

Agents live in `config/agents.yaml`. Environment placeholders:

```yaml
base_url: "${HERMES_GIS_BASE_URL:-http://mock-agent:9010}"
api_key: "${HERMES_GIS_API_KEY:-}"
model: "${HERMES_GIS_MODEL:-gis-default}"
```

No agent is hardcoded in Python.

## Local vs production

| Concern | Local | Production |
|---------|-------|------------|
| Entry | Host ports 3000 / 8000 | Nginx 80/443 |
| Compose | `docker compose up` + override | `docker-compose.prod.yml` + profile |
| Agents | mock-agent | Real Hermes URLs in `.env` |
| Secrets | Dev defaults | Strong secrets |
| Code | **Identical** | **Identical** |

Only `.env` (and TLS certs / nginx SSL config) change.
