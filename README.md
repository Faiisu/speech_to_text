# Typhoon Whisper Testing

Testing and benchmarking [`typhoon-ai/typhoon-whisper-turbo`](https://huggingface.co/typhoon-ai/typhoon-whisper-turbo) and [`typhoon-ai/typhoon-whisper-large-v3`](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3) — Thai-language Whisper fine-tunes — for transcription quality, keyword spotting, and hardware performance.

Three parts:

- **`transcribe.py`** — a CLI that transcribes a file or live microphone audio, optionally spotting target keywords in real time
- **`server.py`** — a local web server (same machine as the microphone) exposing a browser GUI and an HTTP API to start/stop recording and configure it remotely, instead of using the CLI directly
- **`backend/`** — a FastAPI + TimescaleDB service that stores keyword detections reported during a recording session and exposes an API to query them

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

## Web GUI (server.py)

Instead of driving `transcribe.py` from the terminal, `server.py` runs a small local web server on the same machine as the microphone (this has to stay native, not containerized, for the same reason as `transcribe.py` itself — see ADR 0003) and exposes a browser-based control panel plus an HTTP API.

```bash
uv run python server.py
```

Starts a web server bound to `0.0.0.0` on port `5001` — reachable from other devices on the same LAN, same as the backend (see [Accessing the backend from other devices](#accessing-the-backend-from-other-devices-on-the-same-network) above; the same firewall/no-auth caveats apply here too). Open `http://localhost:5001` (or `http://<this machine's LAN IP>:5001` from another device) in a browser.

The page is laid out as a monitoring panel: setup on the left, the live transcript feed as the main stage, and a status rail across the top carrying the readings that matter mid-session — record lamp, **real-time factor meter** (with a redline at 1.0, past which the model is falling behind live speech), position, chunk count, and keyword count. It's responsive down to phone width, so you can start a session and watch the feed from another device across the room.

Typography loads IBM Plex (Mono / Sans Condensed / Sans Thai) from Google Fonts. If the machine is offline or firewalled the fonts simply fall back to system faces — the layout is unaffected.

### HTTP API (used by the GUI, or scriptable directly)

- `POST /start` — body: `{"model": "turbo|large-v3", "keywords": "...", "mic_device": "...", "silence_threshold": 0.01, "backend_url": "..."}` (all but `model` optional). Returns immediately; the actual model loading and recording happen in the background. Returns `409` if a session is already active.
- `POST /stop` — ends the active session. Returns `409` if nothing is recording, or if the model is still loading (wait a moment and retry).
- `GET /status` — `{"status": "idle|loading|recording|stopping", "session_id": ..., "config": ..., "reference_transcript": ..., "error": ...}`. The GUI polls this every 3 seconds to stay in sync, including from other devices/tabs.
- `GET /stream` — Server-Sent Events feed of everything happening during a session, as it happens: `chunk` events (text, `time`, `latency`, `rtf`, or `silent: true` for skipped silence), `keyword` events, `warning` events, and `session` lifecycle events (`loading`/`recording`/`stopped`/`error`). Every connected client gets its own queue, so multiple browsers/devices can watch the same session simultaneously.
- `GET /devices` — the input devices available for recording (index, name, channel count, which one is the system default). Add `?rescan=true` to re-initialise PortAudio and pick up a mic connected *after* the server started; refused with `409` mid-session, since tearing PortAudio down would kill the running stream.

### Reference page

`GET /help` (linked from the panel's top rail as "What do these mean?") explains every control and reading in plain language: how the 5s/1s-overlap chunking works and why words sometimes repeat across lines, what real-time factor / position / chunks / keywords are each telling you, what every setup field changes, and a short "when something looks wrong" table covering the failure modes this project actually hit — hallucinated words in silence, RTF above 1.0, keywords that didn't fire, and unreachable backends.

### Choosing an input in the GUI

The Input field is a dropdown listing every microphone the server can see, so you don't need the CLI's `--list-devices` to find an index. Plugged in a USB mic or paired a Bluetooth headset while the page was open? Press **Rescan** — PortAudio caches its device list at startup, so a plain page refresh won't reveal a device connected since then. The picker (and Rescan) are disabled while a session is running.

Where more than one host API is present (typical on Linux: ALSA alongside PulseAudio/PipeWire), each entry is labelled with the one it comes from, since that changes what selecting it actually does.

#### Does hot-plug detection work on your machine?

**Verified on macOS/CoreAudio only.** Rescan re-initialises PortAudio, which does force a real re-enumeration from the OS (a cached device query takes ~0.07ms; the re-init path ~2.75ms), but whether a newly-connected device then *appears* depends on the audio stack. On Linux it may not: when audio goes through PulseAudio/PipeWire, PortAudio often sees one aggregate input (`pulse` / `default`) rather than each piece of hardware, so plugging in a USB mic changes nothing in this list — you select the aggregate device here and pick the actual microphone at the OS level (`pactl set-default-source`, or `pavucontrol`).

To find out what your machine does, run:

```bash
uv run python check_devices.py
```

It lists the inputs, waits for you to plug or unplug something, then re-scans exactly the way the Rescan button does and reports what changed.

Related: ALSA can reassign device indices when hardware is added or removed, so the GUI tracks your selected device **by name** rather than by index. If the device you picked is gone after a rescan, it falls back to the system default and says so, rather than silently recording from whatever now occupies that index.

### Live feed

The GUI's "Live feed" panel subscribes to `/stream` and shows chunk transcripts and keyword hits **while you're still speaking** — this is genuine streaming transcription, not record-then-process. Each 5-second chunk (1s overlap) is transcribed and pushed to the page as soon as it's ready:

```
● recording started (d4de610d-…)
[0.0s] เราไม่บังคับ   (latency 2.59s, RTF 0.52)
[4.0s] สวัสดีครับ   (latency 2.72s, RTF 0.54)
★ keyword detected: "สวัสดี" at 4.0s
[8.0s] แล้ว   (latency 2.38s, RTF 0.48)
■ session stopped — reference transcript: …
```

The `session stopped` line carries the full-clip reference transcript — the one pass that *is* done after recording ends, for comparison against the live chunked output. Watch `RTF`: below 1.0 means the model is keeping up with live speech; at or above 1.0 it's falling behind (see `CONTEXT.md`).

Only one recording session runs at a time (one microphone). A model, once loaded, stays cached in memory for reuse by later sessions in the same server run — only the first session per model pays the loading cost.

## Project tracking

Tickets for this project's work are tracked as local markdown files under [`.scratch/typhoon-whisper-test/issues/`](./.scratch/typhoon-whisper-test/issues/).
