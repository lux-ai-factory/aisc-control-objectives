# Control objectives service (AISC step 2): FastAPI over the AI Act control objectives,
# with BAF in-process for the model. In the stack, compose mounts shared/identity
# (the aisc_identity package) at /app/shared/identity and adds it to PYTHONPATH.
FROM python:3.12-slim

# Pinned: with :latest the build toolchain floats between builds.
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
# Dependencies come from uv.lock, so two builds a month apart install the same
# versions. The project itself is installed editable, without deps, so it imports
# from /app/src and finds control-objectives.toml and .env at /app rather than in
# site-packages.
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
 && uv pip install --system --no-cache -r /tmp/requirements.txt \
 && uv pip install --system --no-cache --no-deps -e . \
 && rm /tmp/requirements.txt

# Runtime config resolved relative to the repo root (= /app). The objectives
# CSV and the page template ship inside the package (src/aisc_control_objectives/).
COPY control-objectives.toml ./

# The migrations, and the config that names them: the service and the
# control-objectives-migrate one-shot run them per project database from /app,
# so both have to be in the image.
COPY alembic.ini ./
COPY alembic ./alembic

ENV PYTHONPATH=/app/src \
    CONTROL_OBJECTIVES_PORT=8090

EXPOSE 8090

# Configuration comes from the environment (set by compose); see the README.
CMD ["python", "-m", "aisc_control_objectives.server"]
