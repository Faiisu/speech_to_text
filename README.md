# Typhoon Whisper Testing

Testing and benchmarking [`typhoon-ai/typhoon-whisper-turbo`](https://huggingface.co/typhoon-ai/typhoon-whisper-turbo) and [`typhoon-ai/typhoon-whisper-large-v3`](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3) — Thai-language Whisper fine-tunes — for transcription quality, keyword spotting, and hardware performance.

Two parts:

- **`transcribe.py`** — a CLI that transcribes a file or live microphone audio, optionally spotting target keywords in real time
- **`backend/`** — a FastAPI + TimescaleDB service that stores keyword detections reported by `transcribe.py` and exposes an API to query them

See [`CONTEXT-MAP.md`](./CONTEXT-MAP.md), [`CONTEXT.md`](./CONTEXT.md), and [`docs/adr/`](./docs/adr/) for the domain vocabulary and the design decisions behind how this works.

## Prerequisites

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- Docker and Docker Compose (for the backend stack)
- A working microphone, for live recording modes

## Setup

```bash
uv sync
```

This installs `transcribe.py`'s dependencies (torch, transformers, sounddevice, etc.) into a local virtual environment managed by `uv`.

## Running the backend stack

```bash
docker-compose up -d
```

Starts TimescaleDB and the backend API. Verify it's up:

```bash
curl http://localhost:8000/health
```

Postgres/TimescaleDB is reachable from the host on port `5433` (not the default `5432` — that port may already be in use by another project on your machine). The backend talks to it internally on the docker network at `5432`, unaffected by the host port.

Stop everything with `docker-compose down` (add `-v` to also wipe the stored data).

### Backend API

- `POST /events` — store a detection event: `{"word": "...", "detected_at": "<ISO 8601>", "model": "turbo|large-v3", "session_id": "..."}`
- `GET /counts?word=&from=&to=` — count matching events, optionally filtered by word and/or time range
- `GET /events?word=&from=&to=&session_id=` — list matching events, optionally filtered by word, time range, and/or session

## Running transcribe.py

### Transcribe a file

```bash
uv run python transcribe.py --model turbo --file audio.wav
```

`--model` is required: `turbo` or `large-v3`. The audio file must be 16kHz mono. Prints the transcript, transcription latency, and which device (`mps`/`cpu`) ran it.

### Live recording with real-time streaming transcription

```bash
uv run python transcribe.py --model turbo
```

Omitting `--file` starts a live microphone session:

1. Press Enter to start recording
2. Speak — every 5 seconds of audio (with 1s overlap between chunks) is transcribed and printed live as `[chunk @ Xs] <text> (latency ..s, RTF ..)`
3. Press Enter again to stop — a final full-clip batch transcription then prints as a `[reference transcript]`, for comparing against the live chunked output

### Keyword spotting, reported to the backend

```bash
uv run python transcribe.py --model turbo --keywords "สวัสดี,ขอบคุณครับ"
```

During a live session, each chunk's transcript is checked for the given keywords/phrases (comma-separated, exact case-insensitive substring match). Each detection prints `[keyword detected] "<word>" at Xs` and is reported to the backend (default `http://localhost:8000`, override with `--backend-url`) so it can be queried later:

```bash
curl "http://localhost:8000/events?session_id=<the session_id printed at recording start>"
curl "http://localhost:8000/counts?word=สวัสดี"
```

If the backend is unreachable, `transcribe.py` logs a warning and keeps recording rather than crashing.

## Project tracking

Tickets for this project's work are tracked as local markdown files under [`.scratch/typhoon-whisper-test/issues/`](./.scratch/typhoon-whisper-test/issues/).
