# Wizard service — FastAPI service over the AI Act control objectives, with
# BAF in-process for the model. Follows the aisc app pattern (see
# apps/qualification): a self-contained container joined to the platform networks.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install the package editable so it imports from /app/src — this keeps
# aisc_control_objectives.server's repo-root resolution (wizard.toml, .env) pointing at /app
# rather than site-packages.
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache -e .

# Runtime config resolved relative to the repo root (= /app). The objectives
# CSV and the page template ship inside the package (src/aisc_control_objectives/).
COPY wizard.toml ./

# The migrations, and the config that names them: a deployment runs
# `alembic upgrade head` from /app before the service starts, so both have to
# be in the image. Without them alembic fails with "No 'script_location' key".
COPY alembic.ini ./
COPY alembic ./alembic

ENV PYTHONPATH=/app/src \
    WIZARD_PORT=8090

EXPOSE 8090

# aisc_control_objectives.server reads WIZARD_PORT / WIZARD_ROOT_PATH / WIZARD_OBJECTIVES_FILE,
# BAF_LLM_PROVIDER / BAF_LLM_MODEL and the provider's key from the environment
# (injected by the platform / compose).
CMD ["python", "-m", "aisc_control_objectives.server"]
