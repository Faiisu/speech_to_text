# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.7.12 AS uv

FROM python:3.12-slim-bookworm AS python-deps
COPY --from=uv /uv /uvx /bin/
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --locked --no-dev --no-install-project \
    --extra backend --extra microphone --extra openvino

FROM python-deps AS model-export
COPY scripts/export_openvino_model.py /build/scripts/export_openvino_model.py
RUN mkdir -p /opt/models \
    && HF_HUB_DISABLE_TELEMETRY=1 uv run --no-sync python scripts/export_openvino_model.py \
       --destination /opt/models/openvino-turbo-source

FROM node:22.14.0-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY speech_to_text/frontend/package.json speech_to_text/frontend/package-lock.json ./
RUN npm ci
COPY speech_to_text/frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    SPEECH_TO_TEXT_MODELS_DIR=/opt/models \
    SPEECH_TO_TEXT_MODEL=turbo \
    SPEECH_TO_TEXT_RUNTIME=openvino-gpu \
    SPEECH_TO_TEXT_PRECISION=source \
    SPEECH_TO_TEXT_PROFILE_DB=/data/profiles.sqlite3 \
    SPEECH_TO_TEXT_FRONTEND_DIR=/app/frontend
RUN apt-get update \
    && apt-get install -y --no-install-recommends libasound2 libgomp1 libportaudio2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app \
    && mkdir -p /app /opt/models /data \
    && chown -R app:app /app /data
COPY --from=python-deps /build/.venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY speech_to_text/ /app/speech_to_text/
COPY --from=model-export /opt/models/openvino-turbo-source /opt/models/openvino-turbo-source
COPY --from=frontend-build /build/frontend/dist/ /app/frontend/
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["python", "-m", "uvicorn", "speech_to_text.backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
