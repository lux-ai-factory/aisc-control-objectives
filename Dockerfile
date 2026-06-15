# Wizard service — FastAPI matcher (LiteLLM in-process) + PDF export.
# Follows the aisc app pattern (see apps/qualification): a self-contained
# container joined to the platform networks. WeasyPrint needs Pango/Cairo/
# GDK-Pixbuf + a font at runtime (mirrors apps/qualification/services/
# system_card_renderer).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 \
      libpangocairo-1.0-0 \
      libgdk-pixbuf-2.0-0 \
      libcairo2 \
      libffi8 \
      fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install the package editable so it imports from /app/src — this keeps
# wizard.server's repo-root resolution (wizard.toml, knowledge/, skills/)
# pointing at /app rather than site-packages.
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache -e .

# Runtime data resolved relative to the repo root (= /app).
COPY wizard.toml ./
COPY knowledge ./knowledge
COPY skills ./skills

ENV PYTHONPATH=/app/src \
    WIZARD_PORT=8090

EXPOSE 8090

# wizard.server reads WIZARD_PORT / WIZARD_ROOT_PATH / WIZARD_CATALOGUE_URL and
# the provider key from the environment (injected by the platform / compose).
CMD ["python", "-m", "wizard.server"]
