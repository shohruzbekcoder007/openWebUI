# Adding a New Hermes Agent

## 1. Add the agent to `config/agents.yaml`

```yaml
  - id: hr
    name: HR
    description: "Human resources policies and employee lifecycle assistant."
    avatar: "👥"
    system_prompt: |
      You are HR Hermes...
    base_url: "${HERMES_HR_BASE_URL:-http://mock-agent:9010}"
    endpoint: "/v1/chat/completions"
    headers: {}
    api_key: "${HERMES_HR_API_KEY:-}"
    model: "${HERMES_HR_MODEL:-hr-default}"
    enabled: true
    timeout: 120
    stream: true
    temperature: 0.3
```

## 2. (Optional) Add env overrides to `.env` and `.env.example`

```env
HERMES_HR_BASE_URL=https://hr-agent.internal.example.com
HERMES_HR_API_KEY=sk-...
HERMES_HR_MODEL=hr-v1
```

Also add the same variables under `gateway.environment` in `docker-compose.yml` if you want Compose to pass them explicitly (or rely on `env_file: .env`).

## 3. Reload agents

Without restart:

```bash
./scripts/reload-agents.sh
# or
curl -X POST http://localhost:8000/v1/agents/reload \
  -H "Authorization: Bearer sk-gateway-dev-key"
```

Or restart:

```bash
docker compose restart gateway
```

## 4. Verify in Open WebUI

1. Open the UI → model picker
2. You should see **HR**
3. Send a chat; Gateway routes to the HR agent

## 5. Requirements for the Hermes agent API

The agent should expose an **OpenAI-compatible** chat endpoint:

- `POST {base_url}{endpoint}` with JSON body:
  - `model`, `messages`, `stream`, `temperature`, …
- Streaming: `text/event-stream` with `data: {...}` chunks and `data: [DONE]`
- Non-stream: standard `chat.completion` JSON

If your agent uses a different path, set `endpoint` accordingly.

## Disabling an agent

```yaml
enabled: false
```

Reload the gateway. The model disappears from `/v1/models` and Open WebUI.
