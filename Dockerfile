# CloudOps console image (built for linux/arm64 by the CodeBuild project in
# infra/foundation.yaml; scripts/deploy.py uploads the build context).
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
COPY ui ./ui
COPY .streamlit/config.toml .streamlit/config.toml
COPY infra/cross_account_role.yaml infra/cross_account_role.yaml

RUN useradd --uid 10001 --create-home app && chown -R app:app /app
USER app

EXPOSE 8501
CMD ["python", "-m", "ui.serve"]
