# Open WebUI customization

This platform **does not fork or modify** Open WebUI source code.

Customization is done exclusively via:

1. **Official Docker image** — `ghcr.io/open-webui/open-webui:main` (`OPENWEBUI_IMAGE` in `.env`)
2. **Environment variables** — see root `.env` / `.env.example`
3. **Gateway** — all AI traffic goes to the Hermes Gateway (`OPENAI_API_BASE_URL`)
4. **Frontend hooks** — `custom/loader.js` + `custom/custom.css` (see below)

## Custom sidebar page

Open WebUI ships `static/loader.js` and `static/custom.css` as **empty** files and links both
from every page (`<script src="/static/loader.js" defer>` in its `app.html`). That is the
official seam for customizing the UI — [plugin docs][docs] put it plainly: a plugin route is
"your own page… it does **not** inject into the Open WebUI chat UI", and to reach the real SPA
"the frontend has to reference your asset".

So [`custom/loader.js`](custom/loader.js) and [`custom/custom.css`](custom/custom.css) are
mounted read-only over the empty ones in `docker-compose.yml`:

```yaml
- ./open-webui/custom/loader.js:/app/backend/open_webui/static/loader.js:ro
- ./open-webui/custom/custom.css:/app/backend/open_webui/static/custom.css:ro
```

What they add:

- a **Custom** item in the sidebar, after the built-in menu items (both the expanded sidebar
  and the collapsed icon rail)
- an overlay page opened by that item, tracked with the `#custom` URL hash, so the browser
  back button and Escape close it

The page is an overlay, not a real `/custom` route: the SPA route table is compiled into the
bundle, so an unknown path would render Open WebUI's 404 page.

### Filling the page

`loader.js` exposes a small API — no rebuild, just edit the file and reload:

```js
// anywhere after the app has loaded
OpenWebUICustomPage.setContent('<h2>Agents</h2><p>…</p>');
OpenWebUICustomPage.content; // the page body element, for DOM building
OpenWebUICustomPage.open();
OpenWebUICustomPage.close();
```

For heavier features (Python, DB, auth) the cleanest path is to serve the content from the
backend and embed it: an Open WebUI **plugin route** (a Function that calls
`__request__.app.add_api_route(...)`, see [docs][docs]) or any service on the platform, then

```js
OpenWebUICustomPage.setContent('<iframe src="/my/tool"></iframe>');
```

`custom.css` already styles `.owui-custom-body iframe` for that.

### After changing the hook files

```bash
docker compose restart open-webui   # bind mount, so no rebuild
```

Then hard-reload the browser (Ctrl+Shift+R) — `/static/loader.js` is cached.

### Caveats

- The sidebar item is inserted into Open WebUI's rendered DOM. It clones an existing menu
  item, so it inherits current styling instead of hardcoding classes, but **check it after
  every Open WebUI upgrade** — an upstream markup change can silently drop it.
- Anchors it relies on: `#sidebar`, `#pinned-menu-items-list`, and the menu links
  (`a[href="/workspace"]`, `/notes`, `/automations`, `/calendar`, `/playground`).

[docs]: https://docs.openwebui.com/features/extensibility/plugin/development/under-the-hood/

## Critical environment variables

| Variable | Purpose |
|----------|---------|
| `OPENWEBUI_IMAGE` | Open WebUI image; `:main` follows upstream, a tag or digest freezes it |
| `OPENAI_API_BASE_URL` | Must be `http://gateway:8000/v1` on the Docker network |
| `OPENAI_API_KEY` | Must match one of `GATEWAY_API_KEYS` |
| `ENABLE_OLLAMA_API` | Set `false` so models come only from the Gateway |
| `WEBUI_SECRET_KEY` | Session signing secret |
| `WEBUI_AUTH` | Enable login UI |
| `WEBUI_URL` | Public URL users use to open the UI |
| `ENABLE_SIGNUP` | Allow first admin signup (`false` after bootstrap in prod) |

## Model discovery

Open WebUI calls `GET {OPENAI_API_BASE_URL}/models`. The Gateway returns each enabled Hermes Agent as a model (Qishloq xo'jaligi, GIS, Statistics, etc.).

## Updating Open WebUI

`OPENWEBUI_IMAGE` follows the floating `:main` tag, so a pull moves the platform to whatever
upstream had built at that moment:

```bash
docker compose pull open-webui
docker compose up -d open-webui
```

Two things to do around an update:

- **Back up the database first.** Open WebUI runs migrations on start and downgrades are not
  supported.
- **Check the Custom sidebar item afterwards.** It is inserted into Open WebUI's rendered DOM,
  so an upstream markup change can drop it — with a floating tag this can happen on any pull.

To freeze the version instead, put a release tag (`ghcr.io/open-webui/open-webui:v0.11.0`) or a
digest (`docker image inspect <image> --format '{{.RepoDigests}}'`) in `OPENWEBUI_IMAGE`.

Data persists in the `open-webui-data` Docker volume.
