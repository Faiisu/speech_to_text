# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG APP_UID=1001
ARG APP_GID=1001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/speech \
    HF_HOME=/home/speech/.cache/huggingface \
    XDG_CACHE_HOME=/home/speech/.cache \
    XDG_RUNTIME_DIR=/run/user/1001 \
    SPEECH_TO_TEXT_MODELS_DIR=/app/models

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        intel-opencl-icd \
        libportaudio2 \
        ocl-icd-libopencl1 \
        pipewire-alsa \
        python3.12 \
        python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o "${APP_UID}" -g "${APP_GID}" /app /app/models /home/speech/.cache/huggingface /run/user/"${APP_UID}"

WORKDIR /app

RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY pyproject.toml ./
COPY deploy/container-constraints.txt ./deploy/container-constraints.txt
COPY speech_to_text ./speech_to_text
COPY .scratch/new-speech-to-text/spec.md ./.scratch/new-speech-to-text/spec.md

# Install the CPU-only PyTorch wheels first so the OpenVINO extras cannot pull CUDA wheels.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip \
    && python -m pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
        'torch==2.14.1+cpu' 'torchvision==0.29.1+cpu' \
    && python -m pip install --constraint deploy/container-constraints.txt -e '.[control-center,microphone,openvino]'

RUN chown "${APP_UID}:${APP_GID}" /home/speech

USER ${APP_UID}:${APP_GID}
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').getenv('CONTROL_CENTER_PORT', '8765') + '/api/system', timeout=3)" || exit 1

ENTRYPOINT ["python", "-m", "speech_to_text.control_center"]
CMD ["--host", "127.0.0.1", "--port", "8765"]
