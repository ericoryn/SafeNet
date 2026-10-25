FROM ghcr.io/astral-sh/uv:0.11.1 AS uv
FROM python:3.11.15-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra ocr
COPY config ./config
COPY cloud/assets ./cloud/assets
COPY tools ./tools
COPY tests/fixtures/ocr ./tests/fixtures/ocr
RUN python tools/fetch_ocr_models.py
RUN useradd --uid 10001 --create-home safenet && mkdir -p /app/data && chown safenet /app/data
USER safenet
CMD ["python", "-m", "uvicorn", "safenet.site:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "32", "--no-access-log"]
