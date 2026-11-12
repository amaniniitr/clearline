# glibc-based image required by zen-engine and onnxruntime/rapidocr
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

WORKDIR /app

COPY services/core/pyproject.toml services/core/uv.lock services/core/.python-version ./services/core/
RUN cd services/core && uv sync --frozen --no-dev --no-install-project

COPY migrations ./migrations
COPY scenarios/out ./scenarios/out
COPY eval ./eval
COPY services/core/app ./services/core/app

WORKDIR /app/services/core
ENV PYTHONPATH=.

EXPOSE 8000

CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
