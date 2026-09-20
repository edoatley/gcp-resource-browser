# Build stage: resolve and install dependencies with uv.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Build at the final path: a virtualenv's scripts carry an absolute shebang, so
# relocating .venv afterwards leaves every entry point pointing at a python
# that no longer exists.
WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source, so an edit to app/ does not re-resolve the whole tree.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY app ./app
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# Runtime stage: the virtualenv and the app, nothing else.
FROM python:3.13-slim

# Non-root. The tool only ever reads from GCP, and nothing in the image needs
# writing to, so there is no reason to run privileged.
RUN useradd --create-home --uid 10001 explorer

COPY --from=builder --chown=explorer:explorer /app/.venv /app/.venv
COPY --from=builder --chown=explorer:explorer /app/app /app/app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER explorer

EXPOSE 8000

# NO CREDENTIALS ARE BAKED IN. Application Default Credentials are supplied at
# runtime -- by the platform's metadata server on Cloud Run or GKE, or by
# mounting a local ADC file read-only. Copying a key into an image puts a
# long-lived credential into every layer and every registry that holds it.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

ENTRYPOINT ["gcp-explorer"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
