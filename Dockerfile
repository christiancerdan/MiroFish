FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend
COPY frontend/ ./frontend/
COPY locales/ ./locales/
RUN npm run build --prefix frontend

FROM python:3.11-slim-bookworm AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5001 \
    FLASK_DEBUG=false \
    SIMULATION_PYTHON=/app/backend/.venv-simulation/bin/python

# Runtime libraries used by document processing and simulation dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 libcairo2 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 mirofish \
    && useradd --uid 10001 --gid mirofish --create-home --shell /usr/sbin/nologin mirofish

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/vendor/ ./vendor/
# The API interpreter never installs the simulation/ML dependency tree.
RUN uv sync --frozen --no-dev --no-install-project
# CAMEL's psutil 5.x has no Linux ARM64 wheel. Build its extension here,
# then remove the compiler/headers in the same image layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && UV_PROJECT_ENVIRONMENT=/app/backend/.venv-simulation uv sync --frozen --no-dev --extra simulation --no-install-project \
    && apt-get purge -y --auto-remove gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/* /root/.cache/uv
COPY backend/ ./
RUN uv sync --frozen --no-dev \
    && UV_PROJECT_ENVIRONMENT=/app/backend/.venv-simulation uv sync --frozen --no-dev --extra simulation \
    && mkdir -p uploads logs \
    && chown -R mirofish:mirofish uploads logs
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist
COPY locales/ /app/locales/

USER mirofish
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/backend/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/health', timeout=3).close()"]
CMD ["/app/backend/.venv/bin/python", "serve.py"]
