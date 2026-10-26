# syntax=docker/dockerfile:1
# Lightweight offline demo. No model weights or external services required.
FROM node:24-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    LOG_LEVEL=INFO \
    LOCAL_EMBEDDING_ENABLED=false \
    CROSS_ENCODER_ENABLED=false \
    UNSTRUCTURED_ENABLED=false \
    MCP_ENABLED=false
WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY knowledge ./knowledge
COPY --from=frontend /web/dist ./frontend/dist
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install . \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data/local \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.asgi:app --host 0.0.0.0 --port ${PORT:-8000}"]
