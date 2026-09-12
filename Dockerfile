# Wizard service — FastAPI service over the AI Act control objectives
# (LiteLLM in-process). Follows the aisc app pattern (see apps/qualification):
# a self-contained container joined to the platform networks.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install the package editable so it imports from /app/src — this keeps
# wizard.server's repo-root resolution (wizard.toml, .env) pointing at /app
# rather than site-packages.
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache -e .

# Runtime config resolved relative to the repo root (= /app). The objectives
# CSV and the page template ship inside the package (src/wizard/).
COPY wizard.toml ./

ENV PYTHONPATH=/app/src \
    WIZARD_PORT=8090

EXPOSE 8090

# wizard.server reads WIZARD_PORT / WIZARD_ROOT_PATH / WIZARD_OBJECTIVES_FILE
# and the provider key from the environment (injected by the platform/compose).
CMD ["python", "-m", "wizard.server"]
