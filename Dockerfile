# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.7.12 AS uv

FROM ubuntu:24.04 AS python-deps
COPY --from=uv /uv /uvx /bin/
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
ARG GEMMA4_IMAGE=0
RUN --mount=type=cache,id=speech-to-text-uv-cache,target=/root/.cache/uv \
    if [ "$GEMMA4_IMAGE" = "1" ]; then \
      uv sync --python /usr/bin/python3.12 --locked --no-dev --no-install-project \
        --extra backend --extra microphone --extra ctranslate2 --extra model-management --extra mqtt --extra gemma4; \
    else \
      uv sync --python /usr/bin/python3.12 --locked --no-dev --no-install-project \
        --extra backend --extra microphone --extra openvino --extra ctranslate2 --extra model-management --extra mqtt; \
    fi

FROM node:22.14.0-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY speech_to_text/frontend/package.json speech_to_text/frontend/package-lock.json ./
RUN npm ci
COPY speech_to_text/frontend/ ./
RUN npm run build

FROM ubuntu:24.04 AS runtime
ARG GEMMA4_IMAGE=0
ARG DEFAULT_MODEL=turbo
ARG DEFAULT_RUNTIME=openvino-gpu
ARG DEFAULT_PRECISION=source
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    SPEECH_TO_TEXT_MODELS_DIR=/opt/models \
    SPEECH_TO_TEXT_MODEL=${DEFAULT_MODEL} \
    SPEECH_TO_TEXT_RUNTIME=${DEFAULT_RUNTIME} \
    SPEECH_TO_TEXT_PRECISION=${DEFAULT_PRECISION} \
    SPEECH_TO_TEXT_PROFILE_DB=/data/profiles.sqlite3 \
    SPEECH_TO_TEXT_RECORDINGS_DIR=/data/tuning-recordings \
    SPEECH_TO_TEXT_FRONTEND_DIR=/app/frontend \
    HF_HOME=/data/huggingface
RUN apt-get update \
    && if [ "$GEMMA4_IMAGE" = "1" ]; then \
       apt-get install -y --no-install-recommends \
         ca-certificates libasound2t64 libgomp1 libportaudio2 \
         ocl-icd-libopencl1 passwd python3.12; \
    else \
       apt-get install -y --no-install-recommends \
         ca-certificates intel-opencl-icd libasound2t64 libgomp1 \
         libportaudio2 ocl-icd-libopencl1 passwd python3.12; \
    fi \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app \
    && mkdir -p /app /opt/models /data \
    && chown -R app:app /app /data
COPY --from=python-deps /build/.venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY speech_to_text/ /app/speech_to_text/
COPY audio/test-audio.wav /app/audio/test-audio.wav
COPY --from=frontend-build /build/frontend/dist/ /app/frontend/
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["python", "-m", "uvicorn", "speech_to_text.backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
