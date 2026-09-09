# Typhoon Whisper Testing

Testing and benchmarking [`typhoon-ai/typhoon-whisper-turbo`](https://huggingface.co/typhoon-ai/typhoon-whisper-turbo) and [`typhoon-ai/typhoon-whisper-large-v3`](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3) — Thai-language Whisper fine-tunes — for transcription quality, keyword spotting, and hardware performance.

The **production service** is [`stations/` + `production_server.py`](#station-service-production): several labelled microphones running continuously on one machine against a single shared model, with keyword detections stored per station. That is what gets deployed. Everything else here is the testing and measurement toolkit it was built from:

- **`transcribe.py`** — a CLI that transcribes a file or live microphone audio, optionally spotting target keywords in real time
- **`server.py`** — a local web server (same machine as the microphone) exposing a browser GUI and an HTTP API to start/stop recording and configure it remotely, instead of using the CLI directly. One session at a time; this is the panel for comparing runtimes, not the product
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
   - Separately, Whisper's repetition-loop failure mode (regenerating the same phrase over and over when there's no clear speech) has its own mitigation — see ADR 0004 — which is available per session but **off by default**; raise `--repetition-penalty` if the model genuinely loops on noise — check first that the repeats aren't simply what was said. Neither that nor the silence gate eliminates an occasional single hallucinated word from noise that clears the threshold; that's what `--silence-threshold` tuning is for.
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

- `POST /start` — body: `{"model": "turbo|large-v3", "keywords": "...", "mic_device": "...", "silence_threshold": 0.02, "language": "th", "backend_url": "..."}` (all but `model` optional). Returns immediately; the actual model loading and recording happen in the background. Returns `409` if a session is already active.
- `GET /languages` — the languages the panel offers, and the default. The list lives only in `transcribe.py`; the panel reads it from here.
- `GET /models` — the models this machine can run, rediscovered on every call (see [Models](#models)), with what is known about each: repo, where it was found, which runtimes have converted weights, and whether it is English-only.
- `POST /stop` — ends the active session. Returns `409` if nothing is recording, or if the model is still loading (wait a moment and retry).
- `GET /status` — `{"status": "idle|loading|recording|stopping", "session_id": ..., "config": ..., "reference_transcript": ..., "error": ...}`. The GUI polls this every 3 seconds to stay in sync, including from other devices/tabs.
- `GET /stream` — Server-Sent Events feed of everything happening during a session, as it happens: `chunk` events (text, `time`, `latency`, `rtf`, or `silent: true` for skipped silence), `keyword` events, `warning` events, and `session` lifecycle events (`loading`/`recording`/`stopped`/`error`). Every connected client gets its own queue, so multiple browsers/devices can watch the same session simultaneously.
- `GET /devices` — the input devices available for recording (index, name, channel count, which one is the system default). Add `?rescan=true` to re-initialise PortAudio and pick up a mic connected *after* the server started; refused with `409` mid-session, since tearing PortAudio down would kill the running stream.
- `GET /runtimes?model=` — which execution backends are usable for that model, and the reason each unavailable one isn't.
- `GET /audio-files` — clips in `audio/`, with duration. `POST /audio-files` uploads one (converted to 16kHz mono on the way in); `DELETE /audio-files/{name}` removes one.
- `POST /record-server/start|stop`, `GET /record-server/status` — record a clip using the server's own microphone and save it to `audio/`, for use as replay/benchmark input. Refused while a live session is active.
- `POST /benchmark` — replay one clip through several runtimes and stream the comparison over SSE (per-chunk timings, a summary per runtime, and the winner). One benchmark at a time; refused with `409` while another is running or while a live session is active.

Filenames are validated against `audio/` on every endpoint that touches the filesystem — the server listens on the LAN with no authentication, so a caller-supplied name must never be able to escape that directory.

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

## Models

The Model field is **discovered per machine**, not a fixed list. `GET /models` rebuilds it on every call, so a model installed while the server is running appears on the panel's **Rescan** without a restart. Four sources:

| Source | What it finds |
|---|---|
| builtin | `typhoon-ai/typhoon-whisper-turbo` and `-large-v3`, the models this project was built to test. Always offered, downloaded on first use. |
| `models.local.json` | A `{"key": "org/repo"}` map you write yourself. The way to add a model without editing Python. Gitignored — it's your machine's list, not the project's. |
| Hugging Face cache | Any Whisper model already downloaded. `hf download openai/whisper-small` and it shows up. |
| `models/` | Keys whose weights have been converted for openvino / ctranslate2 / whispercpp — including one whose original repo isn't known here (it then runs on that runtime, but not on `pytorch`). |

See what this machine offers:

```bash
uv run python model_catalog.py
```

A cached repo that a builtin or local key already points at is merged into that key rather than listed twice. Model keys become directory names under `models/`, so they're validated as path-safe.

**Two things a longer list doesn't change.** `whispercpp` can only run a model that has GGML weights (only `turbo` has a published build). And an English-only Whisper (`whisper-*.en`) rejects a pinned language outright — `/models` reports `multilingual: false` for those, the panel shows it, and `POST /start` refuses the combination with an explanation rather than letting it fail mid-session.

## Runtimes

The model is always a Whisper-family model; what changes per machine is the machinery that executes it. Pick one in the panel's **Runtime** field, or with `--runtime` on the CLI and benchmark.

| Runtime | Runs on | Notes |
|---|---|---|
| `pytorch` | anywhere | Apple MPS if present, else CPU float32. Always works; the slowest option on an Intel box. |
| `openvino-gpu` | Intel integrated GPU | The interesting one on a Core Ultra machine, and the fastest measured so far — 2.2x `ctranslate2` int8. Needs an OpenCL runtime and `render` group access, see below. |
| `openvino-cpu` | CPU | Same weights as the GPU path; the device is picked at load time. |
| `openvino-npu` | Intel NPU ("AI Boost") | Offered wherever OpenVINO reports an NPU. Not usable on the UBX-330M as shipped — no `/dev/accel/accel0`, so the kernel driver isn't loaded. |
| `ctranslate2` | CPU, int8 | Wants AVX-VNNI to be worth it — check with `check_hardware.py`. |
| `whispercpp` | CPU / GPU (GGML) | Uses whisper.cpp via pywhispercpp; benefits from AVX-VNNI / SIMD or GPU acceleration. |

All five are given the same language (panel **Language** field, or `--language`; default `th`). They used to disagree — `ctranslate2` and `whispercpp` pinned Thai while `pytorch` and both OpenVINO paths auto-detected per chunk — which made their output, and any benchmark comparing them, incomparable. Choosing `auto` now opts every runtime into detection together.

See what's usable here and why the rest aren't:

```bash
uv run python runtimes.py
```

Everything except `pytorch` needs the weights converted once per machine. `faster-whisper` and `pywhispercpp` are already project dependencies, so `uv sync` installs them; OpenVINO is not, because it only makes sense on an Intel machine:

```bash
uv sync                                # installs ctranslate2 + whispercpp libraries
uv sync --extra openvino               # Intel machines only

uv run python convert_model.py --runtime ctranslate2 --model turbo
uv run python convert_model.py --runtime whispercpp  --model turbo
uv run python convert_model.py --runtime openvino    --model turbo
```

`openvino-gpu` needs two things `uv sync` can't provide, and both fail the same silent way — `/dev/dri/renderD*` still exists, OpenVINO just lists `CPU` and the runtime panel greys the GPU out:

```bash
sudo apt install intel-opencl-icd        # the OpenCL runtime for the iGPU
sudo usermod -aG render $USER            # then log in again — the node is root:render 0660
uv run python -c "import openvino; print(openvino.Core().available_devices)"   # want ['CPU', 'GPU']
```

The group step is easy to miss on a headless box: the render node carries an ACL for whoever is logged in at the console, so it works for the desktop user and not for the service account running the app over SSH. `openvino-cpu` is unaffected by both.

`--runtime openvino` also takes `--precision int8` or `--precision int4`, which compresses the weights as they are exported. A compressed build is written as its own model (`models/openvino-turbo-int8`, discovered as the model `turbo-int8`) rather than replacing the uncompressed one, so the two can be benchmarked against each other. Default is `source`: whatever dtype the checkpoint holds, which for the Typhoon fine-tunes is bf16, not fp32.

`--runtime openvino` converts once into `models/openvino-<model>`, shared by every `openvino-*` runtime — the IR is identical and the device is chosen at load time. `--runtime whispercpp` downloads a **community** GGML build (`korakotlee/typhoon-whisper-turbo-ggml`), not one published by typhoon-ai, and only `turbo` has one; for `large-v3` you'd convert it yourself with whisper.cpp's `models/convert-h5-to-ggml.py`.

Converted weights go in `models/` (gitignored) and are reused after that. The panel lists unavailable runtimes greyed out with the reason, and `POST /start` refuses one that isn't ready rather than failing mid-session.

> **Verified:** `pytorch`, `ctranslate2` and `whispercpp` all convert and transcribe Thai correctly on macOS. The CTranslate2 conversion was the step most likely to fail, since Typhoon is a fine-tune rather than stock Whisper.
>
> **Measured on the UBX-330M (Core Ultra 5 125H, Ubuntu 24.04),** one fixed 21.1s clip, 5s chunks, Thai:
>
> | Runtime | Weights | mean RTF | Keeps up? |
> |---|---|---|---|
> | `openvino-gpu` | int8 | **0.53** | yes |
> | `openvino-gpu` | int4 | 0.56 | yes |
> | `openvino-gpu` | bf16 (source) | 0.60 | yes |
> | `ctranslate2` | int8 | 1.34 | no |
> | `openvino-cpu` | int8 | 1.39 | no |
> | `openvino-cpu` | bf16 (source) | 2.53 | no |
> | `pytorch` | fp32 | 4.89 | no |
> | `whispercpp` | q5_0 | 4.27 (at 8 threads) | no |
>
> Two things worth carrying forward. **The device mattered more than the format:** the same weights move from
> 2.53 to 0.60 just by running on the iGPU — a bigger gap than any amount of quantisation produced.
> **Fewer bits is not automatically faster:** int8 bought a lot on the CPU (2.53 → 1.39) and next to nothing on
> the GPU. The three GPU rows are within run-to-run variance of each other (a repeat run put them at 0.59 /
> 0.59 / 0.50), so treat them as tied on speed and decide on the transcript instead — where each step down in
> precision was visibly worse on the noisiest chunk. `whispercpp` remains unexplained: q5_0 weights, the
> lightest of the lot, and still the slowest after its thread count was fixed.
>
> Numbers this close need repeating before they mean anything. One pass over three 5s chunks is enough to rank
> `openvino-gpu` against `ctranslate2`; it is not enough to rank int8 against int4.

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

### Choosing the chunk length

Whisper pads **every** input to a 30-second mel window and truncates anything longer, so the encoder — the expensive half — costs the same whether you hand it 5 seconds or 30. At the 5s default the model does a 30s pass to transcribe 5s of audio, and the fixed cost is spread over six times less speech than it could be.

`--chunk` takes one value or a sweep:

```bash
uv run python benchmark.py --model turbo --file clip.wav --runtime ctranslate2 --chunk 5,10,20
```

Measured on a Mac (`ctranslate2`, 21s clip): 5s chunks gave RTF 1.18 at 5.90s per chunk, 10s gave **0.61** at 6.08s, 20s gave **0.33** at 6.65s. Latency per chunk barely moved — that is the fixed encoder pass, made visible.

Which is why the table has a **`you wait`** column: chunk length plus latency, what the person speaking actually sits through before their words appear. It goes the other way (10.9s → 16.1s → 26.7s in that run), and it is the real price of a low RTF. RTF answers "can this machine keep up"; `you wait` answers "is this usable live".

30 is the ceiling and is enforced — beyond it the audio past the window is dropped silently, so both the CLI and the API refuse it rather than transcribing part of a chunk. In the Web GUI the same control is **Chunk length** in the session panel and in Benchmark Studio.

A longer chunk also tends to transcribe *better*: 5 seconds is far less context than the 30-second windows Whisper was trained on. The trade is responsiveness, not accuracy.

### The repetition guards (off by default)

Whisper fed noise it cannot resolve will regenerate one phrase over and over. `no_repeat_ngram_size` and `repetition_penalty` are the standard mitigation, added in ADR 0004 against exactly that failure — but they are **off by default** (`0` and `1.0`), because `whispercpp` cannot receive them at all (pywhispercpp exposes no generation knobs) and defaulting them on meant the runtimes were never being compared on the same terms. Plain greedy is the neutral baseline; the guards are something to reach for when the model genuinely loops on noise — not merely when the transcript contains repeats:

```bash
uv run python transcribe.py --model turbo --runtime openvino-gpu --repetition-penalty 1.3 --no-repeat-ngram 3
uv run python benchmark.py --model turbo --file clip.wav --runtime openvino-gpu --repetition-penalty 1.0,1.3,1.5
```

`--repetition-penalty` sweeps like `--chunk` does, and prints what each setting heard. In the Web GUI they are the **Repetition penalty** and **No-repeat n-gram** fields, in both the session panel and Benchmark Studio, and the hint under each says what the value you typed will do. The results table names the decoding used and calls out any runtime that ignored it.

**Repeated output is not automatically a loop, and this is easy to get backwards.** On this project's own clip the speaker says `สวัสดีครับ` three times in a row. At 1.0 `ctranslate2` transcribes it that way — correctly. At 1.3 the penalty, having already emitted those tokens, substitutes invented words for the repeats: `สุขสวนต์ครัป การเซ็กซ์ สวยค่ะ ฤๅจิม`. The setting that looks tidier in a diff is the one destroying real speech. `whispercpp`, which never receives the penalty at any value, reproduces the repeats like 1.0 does.

Thai makes this sharp — `ครับ`, `ค่ะ` and greetings repeat constantly in ordinary speech — so before raising the penalty, confirm the repetition in the transcript is not simply repetition in the audio. Check against the full-clip reference transcript printed when a session stops, and sweep with the transcripts side by side rather than trusting either end.

### Comparing models on one runtime

The runtime is only half the question — the other half is which model, and whether a compressed build is worth what it costs in accuracy. Pass several keys to `--model` and they are replayed through the same clip on the same runtime:

```bash
uv run python benchmark.py --model turbo,turbo-int8,turbo-int4 --file clip.wav --runtime openvino-gpu
```

The table gains a model column, and underneath it prints what each model actually heard, because a model that is faster and wrong is not faster. A combination that can't run (a model never converted for that runtime) is reported and skipped rather than ending the sweep.

In the Web GUI the same thing lives in **Benchmark Studio**: models and runtimes are both checkbox lists, and every ticked model is run on every ticked runtime. Availability is checked per model — `ctranslate2` can be ready for `turbo` and missing for `turbo-int8` — so unrunnable pairs are skipped with the reason instead of failing the run.

### Finding the best thread count

On hybrid Intel CPUs (P-cores + E-cores + low-power E-cores) using every core is often *slower* than using only the fast ones, because the slowest core holds up each synchronised operation:

```bash
uv run python benchmark.py --model turbo --file clip.wav --threads 4,8,14
```

It prints a row per setting and names the winner, reloading the model for each one: CTranslate2, whisper.cpp and OpenVINO all fix their thread pool when the model is built, so the count has to be chosen before loading, not after. (This flag used to call `torch.set_num_threads()` and nothing else, which meant it silently measured the same thing five times for every runtime except `pytorch`.)

The effect is real and not monotonic — `whispercpp` on the UBX-330M: 5.24 RTF at 4 threads, **4.27 at 8**, 4.94 at 14, 8.36 at 18. `ctranslate2` on the same box barely moves, so measure rather than assume.

To pin to performance cores specifically, combine with `taskset` (on a Core Ultra 5 125H the P-core threads are usually CPUs 0–7):

```bash
taskset -c 0-7 uv run python benchmark.py --model turbo --file clip.wav --threads 8
```

### What this machine is actually capable of

```bash
uv run python check_hardware.py
```

Reports the CPU, core count, the instruction sets that matter for inference (notably **AVX-VNNI**, which makes int8 models much faster), which accelerators are present (Intel NPU, integrated GPU, Hailo module), how many threads torch is using, and which optimised runtimes are installed.

On the deployment target (Advantech UBX-330M, Intel Core Ultra 5 125H) this reports 14 cores, **AVX-VNNI present** (so int8 models get real hardware acceleration), an Arc integrated GPU, and an NPU whose `intel_vpu` driver isn't loaded — so the NPU is unusable until that's installed.

The default `pytorch` runtime uses none of that: CPU, float32, no iGPU, no int8. Switching runtime is what unlocks it — see [Runtimes](#runtimes) above, and ADR 0005. Measure a baseline with `benchmark.py` before and after, on the same clip, or the comparison means nothing (ADR 0006).

## Station service (production)

The deployment target is an Advantech UBX-330M running `typhoon-whisper-turbo` on the **OpenVINO iGPU** runtime, with three microphones at once. Each microphone is a **station**: a label, a device, its keywords, and its language. Keyword detections are stored in TimescaleDB tagged with the station's label.

```
Station A ─┐
Station B ─┼─► chunk queue ─► inference worker ─► keyword spotting ─► TimescaleDB
Station C ─┘  (bounded,       (ONE shared
               drop-oldest)    OpenVINO iGPU model)
                    │                 │
                    └──── SSE ────────┴──► operator UI at :8080
```

**One model, not three.** A single iGPU runs one decode at a time, so three capture threads each calling the model would contend and all three would fall behind. The stations produce chunks into a bounded queue and one worker consumes them. More stations mean more queueing, not more throughput — which is exactly what `benchmark_parallel.py` measures.

**Bounded everywhere.** Capture keeps only the audio still needed to cut the next chunk, so memory is a function of chunk length rather than uptime. When the model can't keep up, the queue drops its *oldest* chunk and counts it per station rather than growing a backlog — a monitoring system that silently lags is worse than one that says it dropped four chunks.

### Deploying

```bash
./deploy/preflight.sh          # refuses to continue if the iGPU isn't actually usable
sudo ./deploy/install.sh       # deps, model conversion, DB migrations, systemd unit
```

`preflight.sh` checks the render node exists, the user is in the `render` group, OpenVINO reports a `GPU` device, the model is converted, and every configured microphone is present and unambiguous. Each of those is a way an iGPU deploy fails *after* it looks like it succeeded.

The STT service runs on the host as a systemd unit rather than in a container: the OpenVINO GPU plugin has to match the host's i915 driver, and an image that drifts from the host is the usual way this breaks. Only TimescaleDB and its query API are containerised.

```
logs      journalctl -u stt-stations -f
restart   systemctl restart stt-stations
```

### Configuring

A station's source is either a local microphone or a network stream. For a camera, choose *Network stream (RTSP / CCTV)* in the source dropdown and enter the URL:

```
rtsp://user:password@192.168.1.50:554/stream1
```

ffmpeg reads it, so it needs to be installed (`preflight.sh` checks). From the engine's side a camera is an ordinary station — same chunking, silence gate, queue and watchdog, and the same automatic reconnect when it drops off the network.

**Before planning around cameras, measure one.** CCTV audio is usually 8kHz G.711 from a far-field microphone; upsampling to 16kHz does not bring back what the codec discarded, and accuracy suffers — most of all for a tonal language. Record a minute from a real camera, drop it in `audio/`, and run it through the Benchmark tab, reading the *output* panel rather than the timings. Note also that the camera's password sits in `stations.json` in plain text.

Stations live in `stations.json` (start from `deploy/stations.example.json`) or are edited in the web UI. Microphones are identified **by device name, never by index** — PortAudio indices shift when a USB mic is replugged, which would silently rebind a station and mislabel everything it detected. An ambiguous name is rejected rather than guessed at.

```json
{
  "model": "turbo",
  "runtime": "openvino-gpu",
  "queue_size": 6,
  "workers": 1,
  "stations": [
    {"id": "line1", "label": "Line 1", "device": "USB Audio Device",
     "keywords": ["สวัสดี"], "language": "th", "enabled": true,
     "chunk_seconds": 5.0, "overlap_seconds": 1.0, "silence_threshold": 0.02}
  ]
}
```

`workers` is pinned to 1 for `openvino-gpu` and `openvino-npu`, and the UI disables the field for them — a second worker on a single exclusive accelerator contends for the same execution units instead of adding throughput, and because every worker shares one loaded model it would also call that model from two threads. CPU runtimes still accept more.

`queue_size` is how many chunks may wait for the shared model before the oldest is dropped. Bigger is not better: it does not make the machine faster, it trades lost audio for late alerts, and a keyword reported two minutes after it was spoken is no use. If drops are climbing, raise the chunk length or run fewer stations.

### Operating

The UI at `http://<host>:8080` has three tabs:

- **Monitor** — a column per station with live transcript, keyword hits highlighted, and per-station RTF, chunk count, drops and errors. A station whose microphone is unplugged goes red and is restarted automatically when it comes back; the other stations are unaffected.
- **Configuration** — edit stations, pick microphones from a list of what's actually connected. Saving validates every device before writing, so you don't discover a typo at the next restart.
- **History** — query stored detections by station and keyword.

### Capacity: how many microphones fit?

`benchmark.py` answers "which runtime is fastest on one clip". `benchmark_parallel.py` answers the question the product turns on — how many concurrent stations one machine sustains — by running the real engine, the real queue, and the real backpressure against N clips at microphone pace.

```bash
uv run python benchmark_parallel.py --clips recordings/ --sweep 1,2,3,4 --duration 120
uv run python benchmark_parallel.py --clips a.mp4 b.mp4 c.mp4 --streams 3
```

Video files work as input — audio is extracted with ffmpeg — so a folder of recordings is usable test material directly. A stream count only counts as sustained if **nothing was dropped and nothing was left queued**: mean RTF below 1 is not sufficient on its own, since a run can average under 1 and still lose audio in bursts. If three streams fall behind, raising `--chunk-seconds` is the first thing to try — Whisper pads every chunk to 30s regardless, so a longer chunk spreads one fixed encoder pass over more audio.

### Tests

```bash
uv run --group dev pytest
```

Covers the failures that would be expensive to find in production: capture memory growing with uptime, audio silently lost under load, a station bound to the wrong microphone, and what actually reaches the database.

## Project tracking

Tickets for this project's work are tracked as local markdown files under [`.scratch/typhoon-whisper-test/issues/`](./.scratch/typhoon-whisper-test/issues/).
