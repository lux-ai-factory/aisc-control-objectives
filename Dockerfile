# Control Objectives service — FastAPI service over the AI Act control objectives, with
# BAF in-process for the model. Follows the aisc app pattern (see
# apps/qualification): a self-contained container joined to the platform networks.
FROM python:3.12-slim

# Pinned: with :latest the build toolchain floats between builds.
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /bin/

WORKDIR /app

# Install the package editable so it imports from /app/src — this keeps
# aisc_control_objectives.server's repo-root resolution (control-objectives.toml, .env) pointing at /app
# rather than site-packages.
COPY pyproject.toml uv.lock ./
COPY src ./src
# Dependencies come from uv.lock, so two builds a month apart install the same
# versions. The project itself is installed without deps, editable, so its
# repo-root resolution keeps pointing at /app.
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
 && uv pip install --system --no-cache -r /tmp/requirements.txt \
 && uv pip install --system --no-cache --no-deps -e . \
 && rm /tmp/requirements.txt

# Runtime config resolved relative to the repo root (= /app). The objectives
# CSV and the page template ship inside the package (src/aisc_control_objectives/).
COPY control-objectives.toml ./

# The migrations, and the config that names them: a deployment runs
# `alembic upgrade head` from /app before the service starts, so both have to
# be in the image. Without them alembic fails with "No 'script_location' key".
COPY alembic.ini ./
COPY alembic ./alembic

ENV PYTHONPATH=/app/src \
    CONTROL_OBJECTIVES_PORT=8090

EXPOSE 8090

# aisc_control_objectives.server reads CONTROL_OBJECTIVES_PORT / CONTROL_OBJECTIVES_ROOT_PATH / CONTROL_OBJECTIVES_FILE,
# BAF_LLM_PROVIDER / BAF_LLM_MODEL and the provider's key from the environment
# (injected by the platform / compose).
CMD ["python", "-m", "aisc_control_objectives.server"]
