# Hermes Gateway

OpenAI-compatible FastAPI service that sits between **Open WebUI** and **Hermes Agents**.

## Run locally (without Docker)

```bash
cd gateway
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt

export AGENTS_CONFIG_PATH=../config/agents.yaml
export GATEWAY_API_KEYS=sk-gateway-dev-key
export REDIS_ENABLED=false
export CORS_ORIGINS=http://localhost:3000

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Layout

```
app/
  main.py              # FastAPI app + lifespan
  config.py            # Settings from env
  api/v1/              # HTTP routes
  middleware/          # Auth
  models/              # Pydantic schemas + HermesAgent
  services/            # Loader, proxy, rate limit
  utils/               # Logging
```

## Extending for new providers

Add agents in `config/agents.yaml` only. For non–OpenAI-compatible backends, extend `app/services/proxy.py` with an adapter while keeping `/v1/*` stable so Open WebUI never changes.
