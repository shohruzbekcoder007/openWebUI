# Open WebUI customization

This platform **does not fork or modify** Open WebUI source code.

Customization is done exclusively via:

1. **Official Docker image** — `ghcr.io/open-webui/open-webui:main` (pin a version tag in production)
2. **Environment variables** — see root `.env` / `.env.example`
3. **Gateway** — all AI traffic goes to the Hermes Gateway (`OPENAI_API_BASE_URL`)

## Critical environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_BASE_URL` | Must be `http://gateway:8000/v1` on the Docker network |
| `OPENAI_API_KEY` | Must match one of `GATEWAY_API_KEYS` |
| `ENABLE_OLLAMA_API` | Set `false` so models come only from the Gateway |
| `WEBUI_SECRET_KEY` | Session signing secret |
| `WEBUI_AUTH` | Enable login UI |
| `WEBUI_URL` | Public URL users use to open the UI |
| `ENABLE_SIGNUP` | Allow first admin signup (`false` after bootstrap in prod) |

## Model discovery

Open WebUI calls `GET {OPENAI_API_BASE_URL}/models`. The Gateway returns each enabled Hermes Agent as a model (Accounting, GIS, Statistics, etc.).

## Updating Open WebUI

```bash
# Pin a specific release for production
# OPENWEBUI_IMAGE=ghcr.io/open-webui/open-webui:v0.6.5

docker compose pull open-webui
docker compose up -d open-webui
```

Data persists in the `open-webui-data` Docker volume.
