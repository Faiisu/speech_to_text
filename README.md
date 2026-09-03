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

**On Linux**, `sounddevice` also needs the PortAudio system library, which isn't installed by default (macOS gets it bundled with the wheel, Linux doesn't):

```bash
sudo apt-get update
sudo apt-get install -y libportaudio2
```

If you still hit `OSError: PortAudio library not found` after that, install the dev package instead (needed if the system has to build PortAudio bindings from source):

```bash
sudo apt-get install -y portaudio19-dev
```

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

Every filter is optional — omit a param, or leave it blank (e.g. `?from=&to=`), to not filter on it. `GET /events` and `GET /counts` with no params at all return everything.

### Accessing the backend from other devices on the same network

The backend already listens on all network interfaces, not just localhost, so other devices on the same LAN can reach it once the host firewall allows it:

```bash
hostname -I          # or `ip addr show` — find this machine's actual LAN IP
sudo ufw status       # if active, allow the port:
sudo ufw allow 8000/tcp
```

Then from another device (including another machine running `transcribe.py`):

```bash
curl http://<this-machine's-LAN-IP>:8000/health
uv run python transcribe.py --model turbo --keywords "สวัสดี" --backend-url http://<LAN-IP>:8000
```

**No authentication is required** — anything on the same network can post or read events. Fine for a demo on a trusted network; don't expose this beyond that without adding auth first.

## Running transcribe.py

### Transcribe a file

```bash
uv run python transcribe.py --model turbo --file audio.wav
```

`--model` is required: `turbo` or `large-v3`. The audio file must be 16kHz mono. Prints the transcript, transcription latency, and which device (`mps`/`cpu`) ran it.

### Selecting a microphone (Bluetooth, USB, etc.)

By default, recording uses the system's default input device. To use a specific one (e.g. a USB or Bluetooth mic):

```bash
uv run python transcribe.py --list-devices
```

Prints every input/output device with its index, e.g.:

```
  0 Built-in Microphone, ALSA (1 in, 0 out)
> 1 USB Microphone, ALSA (1 in, 0 out)
  2 My Bluetooth Headset, ALSA (1 in, 1 out)
```

Then pass that index (or a substring of the device name) via `--mic-device`:

```bash
uv run python transcribe.py --model turbo --mic-device 1
# or
uv run python transcribe.py --model turbo --mic-device "Bluetooth"
```

On Linux, a Bluetooth mic needs to already be paired and connected at the OS level before it'll show up here — pair it first with `bluetoothctl` (or your desktop's Bluetooth settings), and confirm it's recognized as an audio input with `arecord -l` (ALSA) or `pactl list sources short` (PulseAudio/PipeWire), before expecting `--list-devices` to show it.

### Live recording with real-time streaming transcription

```bash
uv run python transcribe.py --model turbo
```

Omitting `--file` starts a live microphone session:

1. Press Enter to start recording (or pass `--auto-start` to begin immediately once the model finishes loading, no keypress needed)
2. Speak — every 5 seconds of audio (with 1s overlap between chunks) is transcribed and printed live as `[chunk @ Xs] <text> (latency ..s, RTF ..)`. A chunk with no real audio energy (silence) is skipped and printed as `[chunk @ Xs] (silence, skipped)` instead of being sent to the model — Whisper-family models otherwise hallucinate plausible-sounding text from silence, since they have no "say nothing" output. Tune this with `--silence-threshold` (default `0.01`; lower it if quiet speech gets skipped, raise it if background noise still triggers hallucinated text — some microphones' ambient noise floor sits above the default, so don't assume `0.01` works everywhere without checking).
   - Separately, generation is configured to avoid Whisper's repetition-loop failure mode (regenerating the same phrase over and over when there's no clear speech) — see ADR 0004. This doesn't eliminate an occasional single hallucinated word from noise that clears the silence threshold; that's what `--silence-threshold` tuning is for.
3. Press Enter again to stop — a final full-clip batch transcription then prints as a `[reference transcript]` (or `(silence, skipped)` if the whole recording was silent), for comparing against the live chunked output

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
