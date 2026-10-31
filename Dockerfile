# CloudOps console image (built for linux/arm64 by the CodeBuild project in
# infra/foundation.yaml; scripts/deploy.py uploads the build context).

# ── 1. Build the React app ────────────────────────────────────────────────────
FROM public.ecr.aws/docker/library/node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ── 2. Python API server ──────────────────────────────────────────────────────
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements/console.txt requirements/console.txt
RUN pip install -r requirements/console.txt

COPY config.py ./
COPY src ./src
COPY console ./console
COPY infra/cross_account_role.yaml infra/cross_account_role.yaml
COPY --from=web /web/dist ./console/static

RUN useradd --uid 10001 --create-home app && chown -R app:app /app
USER app

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "console.server:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "75"]
