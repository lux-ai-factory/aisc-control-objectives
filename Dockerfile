# syntax=docker/dockerfile:1.19
# Control objectives service (AISC step 2): FastAPI over the AI Act control objectives,
# with BAF in-process for the model. The shared code (the aisc_identity package) goes into the image from the
# `shared` build context, at /app/shared/identity; compose puts it on PYTHONPATH.
# Stand-in for the `shared` build context. The baked stack (docker-compose.baked.yml) passes the checkout's shared/
# folder under that name and its code goes into the image; the development stack passes none and mounts shared/identity
# over the stand-in packages instead, so this build works for both. Each stand-in raises ImportError,
# so an image built without the shared context fails loudly.
FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS shared
RUN for p in aisc_identity aisc_redact aisc_service_client; do mkdir -p /identity/$p \
 && echo "raise ImportError('$p is a stand-in: build this image with docker-compose.baked.yml, which passes the aisc shared folder as the shared build context')" \
      > /identity/$p/__init__.py; done

FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

# Pinned: with :latest the build toolchain floats between builds.
COPY --from=ghcr.io/astral-sh/uv:0.9.9@sha256:f6e3549ed287fee0ddde2460a2a74a2d74366f84b04aaa34c1f19fec40da8652 /uv /uvx /bin/

WORKDIR /app

# the shared identity folder the development stack mounts (aisc_identity, the secret rule aisc_redact, the service
# client aisc_service_client), without local environments and caches, read-only
COPY --chmod=a=rX --exclude=.venv --exclude=*.egg-info --exclude=**/__pycache__ --exclude=.pytest_cache \
     --exclude=.ruff_cache --from=shared /identity /app/shared/identity

COPY pyproject.toml uv.lock ./
COPY src ./src
# Dependencies come from uv.lock, so two builds a month apart install the same
# versions. The project itself is installed editable, without deps, so it imports
# from /app/src and finds control-objectives.toml at /app rather than in
# site-packages (.env is not copied: .dockerignore leaves it out).
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

# runs as its own user, not root
# BAF opens application.log in the working folder when it is imported, so that one file is the user's, and
# downloads its NLTK data into the home folder, so the user has one
RUN useradd --system --uid 10045 --create-home objectives \
 && touch /app/application.log && chown objectives /app/application.log
USER objectives
EXPOSE 8090

# Configuration comes from the environment (set by compose); see the README.
CMD ["python", "-m", "aisc_control_objectives.server"]
