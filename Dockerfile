FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend
COPY frontend/ ./frontend/
COPY locales/ ./locales/
RUN npm run build --prefix frontend

FROM python:3.11-slim-bookworm AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5001 \
    FLASK_DEBUG=false

# Runtime libraries used by document processing and simulation dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 libcairo2 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 mirofish \
    && useradd --uid 10001 --gid mirofish --create-home --shell /usr/sbin/nologin mirofish

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev \
    && mkdir -p uploads logs \
    && chown -R mirofish:mirofish uploads logs
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

USER mirofish
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/backend/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/health', timeout=3).close()"]
CMD ["/app/backend/.venv/bin/python", "serve.py"]
