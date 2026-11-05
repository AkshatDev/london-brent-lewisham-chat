# API-only image: no database, no dev tools. Tests and linting run outside it (uv / CI).
FROM python:3.14-slim

# uv binary, pinned to a version
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

# Compile .py to .pyc at build time (faster start-up); copy instead of hard-link
# from the cache mount (the cache isn't part of the final image).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 1. Dependencies only: this layer is cached until pyproject.toml or uv.lock change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# 2. The project itself (secrets and caches are excluded by .dockerignore).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Don't run as root: a compromised app can't modify the image's files.
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000

# Liveness check against /health (the slim image has no curl, so use Python).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# Production server; the app is found via [tool.fastapi] entrypoint in pyproject.toml.
CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "8000"]
