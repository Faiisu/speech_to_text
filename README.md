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
2. Speak — every 5 seconds of audio (with 1s overlap between chunks) is transcribed and printed live as `[chunk @ Xs] <text> (latency ..s, RTF ..)`. A chunk with no real audio energy (silence) is skipped and printed as `[chunk @ Xs] (silence, skipped)` instead of being sent to the model — Whisper-family models otherwise hallucinate plausible-sounding text from silence, since they have no "say nothing" output. Tune this with `--silence-threshold` (default `0.02`; lower it if quiet speech gets skipped, raise it if background noise still triggers hallucinated text — some microphones' ambient noise floor sits above the default, so don't assume `0.02` works everywhere without checking).
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

- `POST /start` — body: `{"model": "turbo|large-v3", "keywords": "...", "mic_device": "...", "silence_threshold": 0.02, "backend_url": "..."}` (all but `model` optional). Returns immediately; the actual model loading and recording happen in the background. Returns `409` if a session is already active.
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

### Replaying a clip instead of recording

Set **Source** to *Audio file* and the session reads from a clip instead of the microphone. Everything else is identical — the same 5s/1s-overlap chunking, the same live feed, the same keyword spotting and backend reporting — so it's the easy way to see output without recording anything, and the only way to get numbers you can actually compare (a live session is different every time).

Clips live in `audio/` on the server. Use **Upload** in the panel to add one from whatever machine you're browsing from, or drop files into `audio/` directly. Uploads that aren't 16kHz mono are converted automatically with ffmpeg; without ffmpeg installed you get a message telling you the conversion command to run yourself.

Chunks are processed as fast as the model manages rather than paced to real time, so a 60s clip doesn't take 60s to replay — the reported latency and RTF per chunk are still the real figures.

This is the quickest way to compare runtimes or models: replay the same clip, change one thing, replay again.

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

## Runtimes

The model is always Typhoon Whisper; what changes per machine is the machinery that executes it. Pick one in the panel's **Runtime** field, or with `--runtime` on the CLI and benchmark.

| Runtime | Runs on | Notes |
|---|---|---|
| `pytorch` | anywhere | Apple MPS if present, else CPU float32. Always works; the slowest option on an Intel box. |
| `openvino-gpu` | Intel integrated GPU | The interesting one on a Core Ultra machine. |
| `openvino-cpu` | CPU | |
| `ctranslate2` | CPU, int8 | Wants AVX-VNNI to be worth it — check with `check_hardware.py`. |
| `whispercpp` | CPU / GPU (GGML) | Uses whisper.cpp via pywhispercpp; benefits from AVX-VNNI / SIMD or GPU acceleration. |

See what's usable here and why the rest aren't:

```bash
uv run python runtimes.py
```

Everything except `pytorch` needs the weights converted once per machine, and the libraries installed:

```bash
uv add faster-whisper                  # for ctranslate2
uv add "optimum-intel[openvino]"       # for openvino
uv add pywhispercpp                    # for whispercpp

uv run python convert_model.py --runtime ctranslate2 --model turbo
uv run python convert_model.py --runtime openvino    --model turbo
uv run python convert_model.py --runtime whispercpp  --model turbo
```

Converted weights go in `models/` (gitignored) and are reused after that. The panel lists unavailable runtimes greyed out with the reason, and `POST /start` refuses one that isn't ready rather than failing mid-session.

> **Not verified by the author.** The `pytorch` path is tested. The OpenVINO and CTranslate2 paths were written against hardware that wasn't available for testing (no Intel GPU, no AVX-VNNI), so treat the first run on the target machine as the real test — the conversion step in particular may need adjusting for a fine-tuned model.

## Measuring performance & Benchmark Studio

Live microphone sessions can't be compared against each other — every run says something different, so a faster number might just mean you spoke less. To compare models, machines, or optimisation attempts, replay a **fixed clip** through the same 5s/1s-overlap chunking the live path uses.

### 1. Benchmark Studio on Web GUI (`http://localhost:5001`)

Open the Web GUI and click **⚡ Benchmark** in the top navigation:
- **Record reference audio directly**: Click **Record** to record a reference clip from your browser microphone (laptop/phone over LAN) or the device's hardware microphone (e.g. Jabra Speak 510 or Logitech Brio). It automatically converts to 16kHz mono WAV and selects it.
- **Select model & execution runtimes**: Check the runtimes you want to compare side-by-side (`pytorch`, `ctranslate2`, `whispercpp`, `openvino-gpu`, `openvino-cpu`).
- **Run Benchmark**: Watch real-time chunk transcription progress.
- **Visual Results**:
  - 🏆 **Fastest runtime banner** with speedup factor over baseline PyTorch.
  - **Color-coded RTF bar chart** with the 1.0 real-time threshold red line (green = keeps up, red = lags behind).
  - **Comparison table** with latency, mean RTF, worst RTF, real-time capability, and Thai transcript previews.

### 2. CLI Benchmarking (`benchmark.py`)

#### Record audio directly from CLI
Record a test clip from the microphone, save it to `audio/my_clip.wav`, and immediately benchmark it:

```bash
# Record for 10 seconds and benchmark with whispercpp:
uv run python benchmark.py --model turbo --record my_clip.wav --duration 10 --runtime whispercpp

# Or record until pressing Enter:
uv run python benchmark.py --model turbo --record my_clip.wav
```

#### Replay an existing clip

```bash
uv run python benchmark.py --model turbo --file audio/test_clip.wav --runtime whispercpp
```

Reports per-chunk latency and real-time factor, plus mean/median/worst across the clip, and whether it would keep up with live speech (mean RTF below 1.0). The first inference is excluded as warm-up so the numbers reflect steady state.

Use a clip of realistic continuous speech, not a short test phrase — a chunk packed with words takes far longer than one with a single utterance, so short clips flatter the result.

### Finding the best thread count

On hybrid Intel CPUs (P-cores + E-cores + low-power E-cores) using every core is often *slower* than using only the fast ones, because the slowest core holds up each synchronised operation:

```bash
uv run python benchmark.py --model turbo --file clip.wav --threads 4,8,14
```

It prints a row per setting and names the winner. To pin to performance cores specifically, combine with `taskset` (on a Core Ultra 5 125H the P-core threads are usually CPUs 0–7):

```bash
taskset -c 0-7 uv run python benchmark.py --model turbo --file clip.wav --threads 8
```

### What this machine is actually capable of

```bash
uv run python check_hardware.py
```

Reports the CPU, core count, the instruction sets that matter for inference (notably **AVX-VNNI**, which makes int8 models much faster), which accelerators are present (Intel NPU, integrated GPU, Hailo module), how many threads torch is using, and which optimised runtimes are installed.

Worth being clear about the current state: the transcription path is plain PyTorch on CPU in float32, which on an Intel Core Ultra box leaves the integrated GPU, the NPU, and int8 acceleration completely unused. That was a deliberate deferral (see ADR 0003's sibling decision in the session notes) — measure a baseline with `benchmark.py` before deciding whether the added complexity of OpenVINO or CTranslate2 is worth it.

## Project tracking

Tickets for this project's work are tracked as local markdown files under [`.scratch/typhoon-whisper-test/issues/`](./.scratch/typhoon-whisper-test/issues/).
