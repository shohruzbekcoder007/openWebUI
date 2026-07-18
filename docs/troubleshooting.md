# Troubleshooting

## Open WebUI shows no models

1. Check Gateway health:
   ```bash
   curl -s http://localhost:8000/health
   curl -s http://localhost:8000/v1/models -H "Authorization: Bearer sk-gateway-dev-key"
   ```
2. Ensure `OPENAI_API_BASE_URL=http://gateway:8000/v1` (service name, not localhost, inside Docker).
3. Ensure `OPENAI_API_KEY` matches `GATEWAY_API_KEYS`.
4. Ensure `ENABLE_OLLAMA_API=false`.
5. In Open WebUI Admin → Settings → Connections, verify OpenAI connection if settings were changed in the UI (UI config can override env on some versions — reset connection to Gateway).

## Chat fails / 502

- Agent `base_url` unreachable from the gateway container.
- Test from inside the network:
  ```bash
  docker compose exec gateway curl -sS http://mock-agent:9010/health
  ```
- Check agent timeout vs long generations.
- Inspect logs:
  ```bash
  docker compose logs gateway --tail=100
  ```

## Streaming stuck or one big blob

- Nginx must have `proxy_buffering off` for SSE (already set in `nginx/conf.d/default.conf`).
- Header `X-Accel-Buffering: no` is set by the Gateway on stream responses.
- Confirm client receives `text/event-stream`.

## 401 Unauthorized from Gateway

- Missing or wrong `Authorization: Bearer` key.
- Align `OPENAI_API_KEY` and `GATEWAY_API_KEYS`.

## 429 Rate limit

- Increase `RATE_LIMIT_RPM` or set `0` to disable.
- Redis issues fall back to in-memory limits.

## Open WebUI healthcheck unhealthy

- Official image may take 1–2 minutes on first start (model list / migrations).
- Check `start_period` and logs:
  ```bash
  docker compose logs open-webui --tail=100
  ```

## CORS errors (browser → Gateway directly)

- Set `CORS_ORIGINS` to the exact UI origin (`https://ai.example.com`).
- Prefer browser → Open WebUI → Gateway (server-side), which avoids browser CORS to the Gateway.

## Port already in use

- Change `OPENWEBUI_PORT` / `GATEWAY_PORT` in `.env`.

## Production nginx SSL errors

- Cert paths must match mounts: `/etc/nginx/ssl/fullchain.pem`, `privkey.pem`.
- Validate config:
  ```bash
  docker compose exec nginx nginx -t
  ```

## Agents not reloading

- Hot-reload: `POST /v1/agents/reload` with API key.
- Env vars in `agents.yaml` expand at load time from the **gateway process environment** — update `.env` and recreate the container if env changed:
  ```bash
  docker compose up -d gateway --force-recreate
  ```

## Reset Open WebUI admin

Data is in Docker volume `*_webui_data`. Restoring or wiping:

```bash
docker compose down
docker volume rm openwebui-platform_webui_data   # destroys all UI data
docker compose up -d
```
