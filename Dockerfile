FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/app/data \
    DATABASE_URL=sqlite+aiosqlite:////app/data/ccujailer.db \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    HOST=0.0.0.0 \
    PORT=8794

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY backend/ /app/backend/
COPY --from=frontend /frontend/dist /app/frontend/dist

RUN sed -i 's/\r$//' /app/backend/entrypoint.sh \
    && chmod +x /app/backend/entrypoint.sh \
    && useradd -m -u 1000 ccujailer \
    && mkdir -p /app/data \
    && chown -R ccujailer:ccujailer /app

USER 1000:1000
EXPOSE 8794

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8794') + '/healthz')"

ENTRYPOINT ["/app/backend/entrypoint.sh"]
