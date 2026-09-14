# Integrating the wizard as an aisc submodule

This repo is **prepared** to drop into the `aisc` platform
(`/home/listuser/aisc`) as a submodule under `apps/wizard`, matching the pattern
the other apps follow (`apps/qualification`, `apps/controls`). **None of the
steps below have been applied to `aisc` yet** — they are the recipe for when you
choose to wire it in.

## What's already in place here (the "prepare" part)

- **`Dockerfile`** — self-contained image: Python 3.12 + uv, the WeasyPrint
  native stack (Pango/Cairo/GDK-Pixbuf + DejaVu fonts, same set as
  `apps/qualification/services/system_card_renderer`), editable install so
  `wizard.server` resolves `wizard.toml` / `knowledge/` / `skills/` from `/app`.
- **`.dockerignore`** — keeps the build context small and excludes `.env`.
- **`env.development`** — aisc-style env file (compose service names on the
  shared `backend` network; `WIZARD_ROOT_PATH=/wizard`).
- **Reverse-proxy readiness** — `create_app(..., root_path=...)` +
  `WIZARD_ROOT_PATH` so the service can sit behind Caddy under `/wizard*`
  without its `/api/*` routes colliding with `aisc-backend`'s `/api/*`.

## Steps to apply to aisc (later — do NOT run now)

### 1. Add the submodule

```bash
cd /home/listuser/aisc
git submodule add <wizard-repo-url> apps/wizard
```

Then add the matching block to `aisc/.gitmodules`:

```ini
[submodule "apps/wizard"]
	path = apps/wizard
	url = <wizard-repo-url>
	branch = master
```

### 2. Add the compose service

In `aisc/docker-compose.development.yml`:

```yaml
  # ── Test/control selection wizard (apps/wizard submodule) ──
  wizard:
    build: apps/wizard/
    image: aisc-wizard:latest
    pull_policy: never
    container_name: aisc-wizard
    env_file: apps/wizard/env.development
    environment:
      # Provider key for LiteLLM (matches the model in wizard.toml).
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
    ports:
      - "${WIZARD_PORT:-8090}:8090"
    networks:
      - backend
      - frontend
    restart: unless-stopped
```

No `depends_on` for the catalogue: the wizard reads it lazily and the catalogue
is (for now) a separate compose project — make sure `WIZARD_CATALOGUE_URL` in
`env.development` points at a reachable host on the network.

### 3. Add the Caddy route

In `aisc/Caddyfile`, alongside the `/controls*` and `/qualification*` handlers.
Note this uses **`handle_path`** (which strips the `/wizard` prefix), not
`handle` like the Next apps: those are built with `NEXT_BASE_PATH` and serve
under `/controls` themselves, whereas the FastAPI wizard serves its routes at
`/api/*`. `root_path=/wizard` (via `WIZARD_ROOT_PATH`) only fixes the URLs in
generated docs/OpenAPI — it does **not** change route matching — so the proxy
must strip the prefix:

```caddyfile
  # Test/control selection wizard (apps/wizard), served under /wizard
  handle_path /wizard* {
    reverse_proxy wizard:8090 {
      header_up X-Real-IP {remote_host}
    }
  }
```

The API is then reachable at `/wizard/api/plans/...` on the platform domain:
Caddy strips `/wizard` so the container sees `/api/plans/...`, and OpenAPI at
`/wizard/openapi.json` advertises `servers: [{"url": "/wizard"}]` (verified).

## Open items before this is fully "aisc-native"

- **Catalogue dependency.** The wizard needs the catalogue backend. The
  catalogue is still its own compose project (`/home/listuser/catalogue`), not an
  aisc submodule. Either join it to the aisc `backend` network or bring it in as
  its own submodule too.
- **Optional sidecar split.** `apps/qualification` isolates its provider key in a
  dedicated `qualification-llm` LiteLLM sidecar. The wizard currently calls
  LiteLLM in-process. If you want the same isolation, split the LLM call into a
  sidecar later; not required for a working container.
- **Persistence.** Plans are still in `InMemoryPlanStore` (lost on restart). The
  shared platform Postgres is the natural home (SPEC's post-demo direction).
