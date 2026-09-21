# Integrating the service as an aisc submodule

This repo is **prepared** to drop into the `aisc` platform
(`/home/listuser/aisc`) as a submodule under `apps/control-objectives`, matching the pattern
the other apps follow (`apps/qualification`, `apps/controls`). **None of the
steps below have been applied to `aisc` yet** — they are the recipe for when you
choose to wire it in.

## What's already in place here (the "prepare" part)

- **`Dockerfile`** — self-contained image: Python 3.12 + uv, the WeasyPrint
  native stack (Pango/Cairo/GDK-Pixbuf + DejaVu fonts, same set as
  `apps/qualification/services/system_card_renderer`), editable install so
  `aisc_control_objectives.server` resolves `control-objectives.toml` / `knowledge/` / `skills/` from `/app`.
- **`.dockerignore`** — keeps the build context small and excludes `.env`.
- **`env.development`** — aisc-style env file (compose service names on the
  shared `backend` network; `CONTROL_OBJECTIVES_ROOT_PATH=/control-objectives`).
- **Reverse-proxy readiness** — `create_app(..., root_path=...)` +
  `CONTROL_OBJECTIVES_ROOT_PATH` so the service can sit behind Caddy under `/control-objectives*`
  without its `/api/*` routes colliding with `aisc-backend`'s `/api/*`.

## Steps to apply to aisc (later — do NOT run now)

### 1. Add the submodule

```bash
cd /home/listuser/aisc
git submodule add <control objectives-repo-url> apps/control-objectives
```

Then add the matching block to `aisc/.gitmodules`:

```ini
[submodule "apps/control-objectives"]
	path = apps/control-objectives
	url = <control objectives-repo-url>
	branch = master
```

### 2. Add the compose service

In `aisc/docker-compose.development.yml`:

```yaml
  # ── Test/control selection control objectives (apps/control-objectives submodule) ──
  control objectives:
    build: apps/control-objectives/
    image: aisc-control objectives:latest
    pull_policy: never
    container_name: aisc-control objectives
    env_file: apps/control-objectives/env.development
    environment:
      # Provider key for LiteLLM (matches the model in control-objectives.toml).
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
    ports:
      - "${CONTROL_OBJECTIVES_PORT:-8090}:8090"
    networks:
      - backend
      - frontend
    restart: unless-stopped
```

No `depends_on` for the catalogue: the service reads it lazily and the catalogue
is (for now) a separate compose project — make sure `CONTROL_OBJECTIVES_CATALOGUE_URL` in
`env.development` points at a reachable host on the network.

### 3. Add the Caddy route

In `aisc/Caddyfile`, alongside the `/controls*` and `/qualification*` handlers.
Note this uses **`handle_path`** (which strips the `/control-objectives` prefix), not
`handle` like the Next apps: those are built with `NEXT_BASE_PATH` and serve
under `/controls` themselves, whereas the FastAPI control objectives serves its routes at
`/api/*`. `root_path=/control-objectives` (via `CONTROL_OBJECTIVES_ROOT_PATH`) only fixes the URLs in
generated docs/OpenAPI — it does **not** change route matching — so the proxy
must strip the prefix:

```caddyfile
  # Test/control selection control objectives (apps/control-objectives), served under /control-objectives
  handle_path /control-objectives* {
    reverse_proxy control objectives:8090 {
      header_up X-Real-IP {remote_host}
    }
  }
```

The API is then reachable at `/control-objectives/api/plans/...` on the platform domain:
Caddy strips `/control-objectives` so the container sees `/api/plans/...`, and OpenAPI at
`/control-objectives/openapi.json` advertises `servers: [{"url": "/control-objectives"}]` (verified).

## Open items before this is fully "aisc-native"

- **Catalogue dependency.** The service needs the catalogue backend. The
  catalogue is still its own compose project (`/home/listuser/catalogue`), not an
  aisc submodule. Either join it to the aisc `backend` network or bring it in as
  its own submodule too.
- **Optional sidecar split.** `apps/qualification` isolates its provider key in a
  dedicated `qualification-llm` LiteLLM sidecar. The service currently calls
  LiteLLM in-process. If you want the same isolation, split the LLM call into a
  sidecar later; not required for a working container.
- **Persistence.** Plans are still in `InMemoryPlanStore` (lost on restart). The
  shared platform Postgres is the natural home (SPEC's post-demo direction).
