import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

import shutil
import subprocess

import sounddevice as sd
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, field_validator, model_validator

import numpy as np
import statistics

from benchmark import chunk_offsets
from model_catalog import discover, model_repos
from runtimes import DEFAULT_DECODING, RUNTIME_NAMES, Decoding, load_runtime, probe
from transcribe import (
    DEFAULT_CHUNKING,
    MAX_CHUNK_SECONDS,
    Chunking,
    DEFAULT_BACKEND_URL,
    DEFAULT_LANGUAGE,
    DEFAULT_SILENCE_RMS,
    LANGUAGES,
    SAMPLE_RATE,
    is_silent,
    load_audio,
    run_recording_session,
    run_replay_session,
)

STATIC_DIR = Path(__file__).parent / "static"
AUDIO_DIR = Path(__file__).parent / "audio"

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/help", response_class=HTMLResponse)
def help_page() -> str:
    return (STATIC_DIR / "help.html").read_text(encoding="utf-8")

_lock = threading.Lock()
_runtimes: dict[tuple[str, str], object] = {}  # (runtime, model) -> loaded runtime; loading is slow
_state: dict = {
    "status": "idle",  # idle | loading | recording | stopping
    "config": None,
    "session_id": None,
    "stop_event": None,
    "error": None,
    "reference_transcript": None,
}


class StartRequest(BaseModel):
    # not a Literal: the set of models is discovered per machine, so pinning it
    # here would reject a model the panel legitimately offers
    model: str
    # also not a Literal: the runtime list lives in runtimes.RUNTIME_NAMES,
    # and duplicating it here is how openvino-npu was offered by the panel
    # while being rejected at the door
    runtime: str = "pytorch"
    source: Literal["mic", "file"] = "mic"
    file: str | None = None  # filename inside audio/, when source is "file"
    keywords: str | None = None
    mic_device: str | None = None
    silence_threshold: float = DEFAULT_SILENCE_RMS
    # Whisper pads every chunk to 30s, so a longer chunk spreads one fixed
    # encoder pass over more audio — at the cost of the speaker waiting a
    # whole chunk before seeing anything. Chunking validates the pair.
    chunk_seconds: float = DEFAULT_CHUNKING.chunk_seconds
    overlap_seconds: float = DEFAULT_CHUNKING.overlap_seconds
    # The ADR 0004 repetition guards, which whisper.cpp never gets. Settable
    # because they are the one runtime difference that is a decision, and the
    # first thing to turn off when a transcript reads worse than whisper.cpp's.
    repetition_penalty: float = DEFAULT_DECODING.repetition_penalty
    no_repeat_ngram_size: int = DEFAULT_DECODING.no_repeat_ngram_size
    backend_url: str = DEFAULT_BACKEND_URL
    # validated against LANGUAGES rather than a Literal so the list of offered
    # languages lives in exactly one place (transcribe.LANGUAGES)
    language: str = DEFAULT_LANGUAGE

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"Unknown language {value!r}; expected one of {', '.join(LANGUAGES)}")
        return value

    @field_validator("model")
    @classmethod
    def _known_model(cls, value: str) -> str:
        return _validate_model(value)

    @model_validator(mode="after")
    def _valid_chunking(self):
        try:
            Chunking(self.chunk_seconds, self.overlap_seconds)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self

    @property
    def chunking(self) -> Chunking:
        return Chunking(self.chunk_seconds, self.overlap_seconds)

    @model_validator(mode="after")
    def _valid_decoding(self):
        try:
            Decoding(self.no_repeat_ngram_size, self.repetition_penalty)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self

    @property
    def decoding(self) -> Decoding:
        return Decoding(self.no_repeat_ngram_size, self.repetition_penalty)

    @field_validator("runtime")
    @classmethod
    def _known_runtime(cls, value: str) -> str:
        if value not in RUNTIME_NAMES:
            raise ValueError(
                f"Unknown runtime {value!r}; expected one of {', '.join(RUNTIME_NAMES)}"
            )
        return value


def _check_language_supported(model_key: str, language: str) -> None:
    """Refuse a pinned language on an English-only model, with the reason.

    Whisper's `.en` builds reject a `language`/`task` argument outright. Now
    that any cached model can be picked from the panel, that is a reachable
    combination, and left alone it surfaces as a raw library error partway
    into a session rather than as a refused request.
    """
    entry = discover().get(model_key)
    if entry and entry.get("multilingual") is False and language != "auto":
        raise HTTPException(
            status_code=409,
            detail=(
                f"{model_key!r} is an English-only model, which cannot be given a language. "
                f"Set Language to 'Auto-detect', or choose a multilingual model."
            ),
        )


def _validate_model(value: str) -> str:
    """Check a model key against what this machine actually offers.

    Discovery runs per call rather than against a snapshot, so a model
    downloaded or converted while the server is running is accepted straight
    away — the whole point of the catalogue.
    """
    catalogue = discover()
    if value not in catalogue:
        raise ValueError(
            f"Unknown model {value!r}. Available here: {', '.join(catalogue) or 'none'}"
        )
    return value


# Live event fan-out: every connected browser gets its own queue, so the feed
# isn't tied to one connection and a slow/dead client can't stall the recording
# thread (events are dropped for that client instead of blocking).
_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()


def broadcast(event: dict) -> None:
    with _subscribers_lock:
        targets = list(_subscribers)
    for subscriber in targets:
        try:
            subscriber.put_nowait(event)
        except queue.Full:
            pass


@app.get("/stream")
def stream() -> StreamingResponse:
    subscriber: queue.Queue = queue.Queue(maxsize=1000)
    with _subscribers_lock:
        _subscribers.append(subscriber)

    def event_source():
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            with _subscribers_lock:
                if subscriber in _subscribers:
                    _subscribers.remove(subscriber)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve_audio(name: str | None) -> Path:
    """Resolve a filename to a clip inside audio/, refusing anything outside it."""
    if not name:
        raise HTTPException(status_code=422, detail="No audio file chosen")
    path = (AUDIO_DIR / name).resolve()
    if not path.is_relative_to(AUDIO_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file name")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No such audio file: {name}")
    return path


def _safe_audio_target(name: str) -> Path:
    """Where a *new* clip may be written, refusing anything outside audio/.

    _resolve_audio can't be used for this because it requires the file to
    already exist. Without this check a caller-supplied filename like
    "../../x.wav" (or an absolute path) escapes audio/ entirely — and this
    server listens on the LAN with no authentication.
    """
    target = (AUDIO_DIR / name).resolve()
    if not target.is_relative_to(AUDIO_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file name")
    return target


def _to_16k_mono(path: Path) -> None:
    """Rewrite a clip in place as 16kHz mono, which is all the models accept."""
    import soundfile as sf

    info = sf.info(str(path))
    if info.samplerate == SAMPLE_RATE and info.channels == 1:
        return

    if shutil.which("ffmpeg") is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{path.name} is {info.samplerate}Hz/{info.channels}ch but 16kHz mono is "
                "required, and ffmpeg isn't installed to convert it. Convert it yourself: "
                f"ffmpeg -i in.wav -ar 16000 -ac 1 {path.name}"
            ),
        )

    converted = path.with_suffix(".converted.wav")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ar", str(SAMPLE_RATE), "-ac", "1", str(converted)],
        capture_output=True,
    )
    if result.returncode != 0:
        converted.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not convert {path.name} to 16kHz mono")
    converted.replace(path)


@app.get("/audio-files")
def audio_files() -> dict:
    import soundfile as sf

    AUDIO_DIR.mkdir(exist_ok=True)
    files = []
    for path in sorted(AUDIO_DIR.glob("*.wav")):
        try:
            info = sf.info(str(path))
            files.append(
                {"name": path.name, "seconds": round(info.duration, 1), "samplerate": info.samplerate}
            )
        except Exception:
            continue  # unreadable file — just leave it out of the list
    return {"files": files}


@app.post("/audio-files")
async def upload_audio(file: UploadFile) -> dict:
    if not file.filename:
        raise HTTPException(status_code=422, detail="No file provided")

    AUDIO_DIR.mkdir(exist_ok=True)
    # preserve wav extension or force it if converting from other audio format
    raw_name = Path(file.filename).name
    stem = Path(raw_name).stem
    safe_name = f"{stem}.wav" if not raw_name.lower().endswith(".wav") else raw_name
    target = (AUDIO_DIR / safe_name).resolve()
    if not target.is_relative_to(AUDIO_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid file name")

    # Save temporary upload
    temp_target = target.with_suffix(".upload.tmp")
    with temp_target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        # Convert any uploaded format (wav, webm, ogg, mp3, m4a) to 16kHz mono WAV
        if shutil.which("ffmpeg") is not None:
            res = subprocess.run(
                ["ffmpeg", "-y", "-i", str(temp_target), "-ar", str(SAMPLE_RATE), "-ac", "1", str(target)],
                capture_output=True,
            )
            temp_target.unlink(missing_ok=True)
            if res.returncode != 0:
                raise HTTPException(status_code=422, detail="Failed to convert audio with ffmpeg")
        else:
            temp_target.replace(target)
            _to_16k_mono(target)
    except Exception as e:
        temp_target.unlink(missing_ok=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=422, detail=f"Audio processing failed: {e}")

    import soundfile as sf
    duration = 0.0
    try:
        duration = round(sf.info(str(target)).duration, 1)
    except Exception:
        pass

    return {"name": target.name, "seconds": duration}


@app.delete("/audio-files/{name}")
def delete_audio(name: str) -> dict:
    path = _resolve_audio(name)
    path.unlink(missing_ok=True)
    return {"deleted": name}


_rec_lock = threading.Lock()
_recorder: dict = {
    "active": False,
    "filename": None,
    "frames": [],
    "stream": None,
    "start_time": 0.0,
}


class ServerRecordRequest(BaseModel):
    filename: str | None = None
    mic_device: str | None = None


@app.post("/record-server/start")
@app.post("/record-server/start/")
def record_server_start(req: ServerRecordRequest) -> dict:
    with _lock:
        if _state["status"] in ("loading", "recording"):
            raise HTTPException(status_code=409, detail="A live session is currently active")

    with _rec_lock:
        if _recorder["active"]:
            raise HTTPException(status_code=409, detail="Server recording already in progress")

        dev_index = None
        if req.mic_device is not None:
            try:
                dev_index = int(req.mic_device)
            except ValueError:
                for idx, d in enumerate(sd.query_devices()):
                    if d["max_input_channels"] > 0 and str(req.mic_device).lower() in d["name"].lower():
                        dev_index = idx
                        break

        frames = []

        def callback(indata, frame_count, time_info, status):
            frames.append(indata.copy())

        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=dev_index,
                callback=callback,
            )
            stream.start()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not open input device: {exc}")

        filename = req.filename or f"clip_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        if not filename.lower().endswith(".wav"):
            filename = f"{filename}.wav"
        # validate before recording, so a bad name fails immediately rather
        # than after the operator has spoken a whole clip
        try:
            _safe_audio_target(filename)
        except HTTPException:
            stream.stop()
            stream.close()
            raise

        _recorder["active"] = True
        _recorder["filename"] = filename
        _recorder["frames"] = frames
        _recorder["stream"] = stream
        _recorder["start_time"] = time.perf_counter()

    return {"status": "recording", "filename": filename}


@app.post("/record-server/stop")
@app.post("/record-server/stop/")
def record_server_stop() -> dict:
    import soundfile as sf

    with _rec_lock:
        if not _recorder["active"]:
            raise HTTPException(status_code=409, detail="No server recording in progress")

        stream = _recorder["stream"]
        frames = _recorder["frames"]
        filename = _recorder["filename"]
        _recorder["active"] = False
        _recorder["stream"] = None

    if stream:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    if not frames:
        raise HTTPException(status_code=400, detail="No audio captured")

    AUDIO_DIR.mkdir(exist_ok=True)
    target = _safe_audio_target(filename)  # re-checked here: this is the write
    audio = np.concatenate(frames, axis=0).flatten()
    sf.write(str(target), audio, SAMPLE_RATE)
    duration = round(len(audio) / SAMPLE_RATE, 1)

    return {"status": "saved", "name": filename, "seconds": duration}


@app.get("/record-server/status")
@app.get("/record-server/status/")
def record_server_status() -> dict:
    with _rec_lock:
        elapsed = round(time.perf_counter() - _recorder["start_time"], 1) if _recorder["active"] else 0.0
        return {
            "active": _recorder["active"],
            "filename": _recorder["filename"],
            "elapsed": elapsed,
        }


class BenchmarkRequest(BaseModel):
    file: str
    # `models` compares several models on the same runtime; `model` is the
    # single-model form the panel used to send and still the fallback. A run
    # is every combination of the two lists, so one request can answer both
    # "which runtime is fastest" and "which model is fastest" — and, with two
    # of each, whether those two answers interact.
    model: str = "turbo"
    models: list[str] = []
    runtimes: list[str]

    @field_validator("model")
    @classmethod
    def _known_model(cls, value: str) -> str:
        return _validate_model(value)

    @field_validator("models")
    @classmethod
    def _known_models(cls, value: list[str]) -> list[str]:
        return [_validate_model(v) for v in value]

    silence_threshold: float = DEFAULT_SILENCE_RMS
    chunk_seconds: float = DEFAULT_CHUNKING.chunk_seconds
    overlap_seconds: float = DEFAULT_CHUNKING.overlap_seconds
    repetition_penalty: float = DEFAULT_DECODING.repetition_penalty
    no_repeat_ngram_size: int = DEFAULT_DECODING.no_repeat_ngram_size
    language: str = DEFAULT_LANGUAGE

    @model_validator(mode="after")
    def _valid_chunking(self):
        try:
            Chunking(self.chunk_seconds, self.overlap_seconds)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self

    @property
    def chunking(self) -> Chunking:
        return Chunking(self.chunk_seconds, self.overlap_seconds)

    @model_validator(mode="after")
    def _valid_decoding(self):
        try:
            Decoding(self.no_repeat_ngram_size, self.repetition_penalty)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self

    @property
    def decoding(self) -> Decoding:
        return Decoding(self.no_repeat_ngram_size, self.repetition_penalty)

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"Unknown language {value!r}; expected one of {', '.join(LANGUAGES)}")
        return value


_bench_lock = threading.Lock()
_benchmark: dict = {"active": False}


@app.post("/benchmark")
@app.post("/benchmark/")
def run_benchmark(req: BenchmarkRequest) -> StreamingResponse:
    with _lock:
        if _state["status"] in ("loading", "recording"):
            raise HTTPException(
                status_code=409, detail="Cannot run benchmark while a recording session is active"
            )

    # Validate the clip before claiming the slot below — anything that raises
    # after the flag is set would leak it, since the generator that clears it
    # never gets to run.
    models = req.models or [req.model]
    for model_key in models:
        _check_language_supported(model_key, req.language)
    clip_path = _resolve_audio(req.file)
    audio = load_audio(str(clip_path))
    duration = audio.size / SAMPLE_RATE
    chunking = req.chunking
    if not chunk_offsets(audio.size, chunking):
        raise HTTPException(
            status_code=400,
            detail=f"Clip is {duration:.1f}s — shorter than one "
            f"{chunking.chunk_seconds:g}s chunk. Pick a longer clip or a smaller chunk length.",
        )

    # One benchmark at a time. This server is reachable from every device on
    # the LAN, so two people could otherwise benchmark at once and contend for
    # the same CPU — which silently corrupts the timings the benchmark exists
    # to produce.
    with _bench_lock:
        if _benchmark["active"]:
            raise HTTPException(status_code=409, detail="A benchmark is already running")
        _benchmark["active"] = True

    # Availability is per model, not per machine: ctranslate2 can be ready for
    # turbo and missing for turbo-int8, since each model is converted
    # separately. Probing once for the first model would mark the second one's
    # combinations available and then fail at load.
    available_by_model = {m: {e["name"]: e for e in probe(m)} for m in models}

    def event_stream():
        yield f"data: {json.dumps({'type': 'init', 'file': clip_path.name, 'duration': round(duration, 1), 'chunk_seconds': chunking.chunk_seconds, 'decoding': req.decoding.label(), 'overlap_seconds': chunking.overlap_seconds, 'models': models, 'runtimes': req.runtimes, 'language': req.language})}\n\n"

        results = []
        for model_key, r_name in ((m, r) for m in models for r in req.runtimes):
            combo = f"{model_key}::{r_name}"
            entry = available_by_model[model_key].get(r_name)
            if not entry or not entry.get("available"):
                reason = entry.get("reason") if entry else "unknown runtime"
                yield f"data: {json.dumps({'type': 'runtime_skip', 'id': combo, 'model': model_key, 'runtime': r_name, 'reason': reason})}\n\n"
                continue

            label = entry["label"] if len(models) == 1 else f"{model_key} · {entry['label']}"
            yield f"data: {json.dumps({'type': 'runtime_start', 'id': combo, 'model': model_key, 'runtime': r_name, 'label': label})}\n\n"

            try:
                runtime = _get_runtime(r_name, model_key, req.decoding)
                # warmup
                runtime.transcribe(audio[: chunking.chunk_samples], req.language)

                offsets = chunk_offsets(audio.size, chunking)
                chunk_samples = chunking.chunk_samples
                latencies = []
                texts = []
                skipped = 0

                for i, offset in enumerate(offsets):
                    chunk = audio[offset : offset + chunk_samples]
                    if is_silent(chunk, req.silence_threshold):
                        skipped += 1
                        ev = {
                            "type": "chunk",
                            "id": combo,
                            "model": model_key,
                            "runtime": r_name,
                            "chunk": i + 1,
                            "total": len(offsets),
                            "offset": round(offset / SAMPLE_RATE, 1),
                            "latency": 0.0,
                            "rtf": 0.0,
                            "text": "(silence, skipped)",
                            "silent": True,
                        }
                        yield f"data: {json.dumps(ev)}\n\n"
                        continue

                    t0 = time.perf_counter()
                    t_chunk = runtime.transcribe(chunk, req.language)
                    lat = time.perf_counter() - t0
                    rtf = lat / chunking.chunk_seconds
                    latencies.append(lat)
                    texts.append(t_chunk)
                    ev = {
                        "type": "chunk",
                        "id": combo,
                        "model": model_key,
                        "runtime": r_name,
                        "chunk": i + 1,
                        "total": len(offsets),
                        "offset": round(offset / SAMPLE_RATE, 1),
                        "latency": round(lat, 2),
                        "rtf": round(rtf, 2),
                        "text": t_chunk,
                        "silent": False,
                    }
                    yield f"data: {json.dumps(ev)}\n\n"

                if latencies:
                    rtfs = [l / chunking.chunk_seconds for l in latencies]
                    stats = {
                        "id": combo,
                        "model": model_key,
                        "runtime": r_name,
                        "label": label,
                        "chunks": len(latencies),
                        "skipped": skipped,
                        "mean_rtf": round(statistics.mean(rtfs), 2),
                        "median_rtf": round(statistics.median(rtfs), 2),
                        "worst_rtf": round(max(rtfs), 2),
                        "mean_latency": round(statistics.mean(latencies), 2),
                        "total_latency": round(sum(latencies), 2),
                        # longer than it looks in the table: comparing models
                        # is a comparison of words, not only of latency, so the
                        # full-ish transcript has to survive to the client
                        "sample_text": " ".join(t for t in texts if t).strip()[:400],
                        "keeps_up": statistics.mean(rtfs) < 1.0,
                        "honours_decoding": runtime.honours_decoding,
                        # what the speaker sits through: the chunk has to be
                        # spoken before it can be transcribed
                        "wait": round(chunking.chunk_seconds + statistics.mean(latencies), 2),
                    }
                    results.append(stats)
                    yield f"data: {json.dumps({'type': 'runtime_done', 'id': combo, 'model': model_key, 'runtime': r_name, 'stats': stats})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'runtime_done', 'id': combo, 'model': model_key, 'runtime': r_name, 'error': 'All chunks were silence'})}\n\n"

            except Exception as exc:
                yield f"data: {json.dumps({'type': 'runtime_error', 'id': combo, 'model': model_key, 'runtime': r_name, 'error': str(exc)})}\n\n"

        valid = [r for r in results if r.get("mean_rtf") is not None]
        winner = min(valid, key=lambda x: x["mean_rtf"]) if valid else None
        yield f"data: {json.dumps({'type': 'complete', 'results': results, 'winner': winner})}\n\n"

    def guarded_stream():
        # Must release the flag however this ends — including the client simply
        # closing the tab mid-run, which raises GeneratorExit. Without this a
        # single abandoned benchmark would block every later one.
        try:
            yield from event_stream()
        finally:
            with _bench_lock:
                _benchmark["active"] = False

    return StreamingResponse(
        guarded_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _get_runtime(runtime_name: str, model_key: str, decoding: Decoding | None = None):
    # The decoding settings are part of the identity of a loaded runtime:
    # caching on (runtime, model) alone would hand back the instance built
    # with the previous settings and quietly answer the wrong question.
    decoding = decoding or DEFAULT_DECODING
    key = (runtime_name, model_key, decoding)
    if key not in _runtimes:
        _runtimes[key] = load_runtime(runtime_name, model_key, decoding=decoding)
    return _runtimes[key]


def _run_session(req: StartRequest) -> None:
    session_id = _state["session_id"]
    try:
        broadcast({"type": "session", "state": "loading", "session_id": session_id})
        runtime = _get_runtime(req.runtime, req.model, req.decoding)

        with _lock:
            if _state["status"] != "loading":
                return  # a stop (or another start) raced us before loading finished
            _state["status"] = "recording"

        keywords = [k.strip() for k in req.keywords.split(",") if k.strip()] if req.keywords else []
        mic_device: int | str | None = req.mic_device
        if mic_device is not None:
            try:
                mic_device = int(mic_device)
            except ValueError:
                pass

        broadcast(
            {
                "type": "session",
                "state": "recording",
                "session_id": session_id,
                "source": req.source,
                "file": req.file,
            }
        )

        def on_event(event: dict) -> None:
            if event["type"] == "chunk":
                if event["silent"]:
                    print(f"[chunk @ {event['time']:.1f}s] (silence, skipped)")
                else:
                    print(
                        f"[chunk @ {event['time']:.1f}s] {event['text']} "
                        f"(latency {event['latency']:.2f}s, RTF {event['rtf']:.2f})"
                    )
            elif event["type"] == "keyword":
                print(f'[keyword detected] "{event["keyword"]}" at {event["time"]:.1f}s')
            elif event["type"] == "warning":
                print(f"[warning] {event['message']}")
            broadcast(event)

        if req.source == "file":
            audio = run_replay_session(
                runtime,
                load_audio(str(_resolve_audio(req.file))),
                keywords,
                req.model,
                req.backend_url,
                req.silence_threshold,
                _state["stop_event"],
                session_id,
                on_event=on_event,
                language=req.language,
                chunking=req.chunking,
            )
        else:
            audio = run_recording_session(
                runtime,
                keywords,
                req.model,
                req.backend_url,
                mic_device,
                req.silence_threshold,
                _state["stop_event"],
                session_id,
                on_event=on_event,
                language=req.language,
                chunking=req.chunking,
            )

        if is_silent(audio, req.silence_threshold):
            reference_transcript = "(silence, skipped)"
        else:
            reference_transcript = runtime.transcribe(audio, req.language)

        with _lock:
            _state["reference_transcript"] = reference_transcript
            _state["status"] = "idle"

        broadcast(
            {
                "type": "session",
                "state": "stopped",
                "session_id": session_id,
                "reference_transcript": reference_transcript,
            }
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure via /status instead of losing it in a thread
        with _lock:
            _state["error"] = str(exc)
            _state["status"] = "idle"
        broadcast(
            {"type": "session", "state": "error", "session_id": session_id, "message": str(exc)}
        )


@app.post("/start")
def start(req: StartRequest) -> dict:
    # Check the runtime is usable before accepting the request, so an
    # unconverted or uninstalled backend fails immediately with the reason
    # rather than after the session has already gone into "loading".
    if (req.runtime, req.model) not in _runtimes:
        entry = next((e for e in probe(req.model) if e["name"] == req.runtime), None)
        if entry and not entry["available"]:
            raise HTTPException(
                status_code=409, detail=f"Runtime {req.runtime!r} unavailable: {entry['reason']}"
            )

    _check_language_supported(req.model, req.language)

    # same reasoning for the clip: fail now, not after the session starts
    if req.source == "file":
        _resolve_audio(req.file)

    with _lock:
        if _state["status"] in ("loading", "recording"):
            raise HTTPException(status_code=409, detail="A recording session is already active")
        _state["status"] = "loading"
        _state["config"] = req.model_dump()
        _state["session_id"] = str(uuid.uuid4())
        _state["stop_event"] = threading.Event()
        _state["error"] = None
        _state["reference_transcript"] = None

    threading.Thread(target=_run_session, args=(req,), daemon=True).start()
    return {"status": "starting", "session_id": _state["session_id"]}


@app.post("/stop")
def stop() -> dict:
    with _lock:
        if _state["status"] == "idle":
            raise HTTPException(status_code=409, detail="No active recording session")
        if _state["status"] == "loading":
            raise HTTPException(
                status_code=409, detail="Still loading the model, try again shortly"
            )
        _state["stop_event"].set()
        _state["status"] = "stopping"
    return {"status": "stopping"}


@app.get("/runtimes")
def runtimes(model: str = "turbo") -> dict:
    """Which execution backends are usable here, and why the others aren't.

    Availability depends on the machine (Intel GPU present?), what's installed
    (openvino, faster-whisper), and whether the weights have been converted for
    that runtime — so it's reported per model.
    """
    if model not in discover():
        raise HTTPException(status_code=422, detail=f"Unknown model {model!r}")
    loaded = {name for name, key in _runtimes if key == model}
    entries = probe(model)
    for entry in entries:
        entry["loaded"] = entry["name"] in loaded
    return {"runtimes": entries}


@app.get("/models")
def models() -> dict:
    """Models this machine can offer, rediscovered on every call.

    Rediscovering rather than caching is what makes a newly downloaded or
    converted model appear in the panel without a restart.
    """
    entries = []
    for key, entry in discover().items():
        note = []
        if not entry["repo"]:
            note.append("converted weights only — pytorch can't load it")
        if entry["multilingual"] is False:
            note.append("English-only: the Language field won't apply")
        if entry["converted"]:
            note.append(f"converted for {', '.join(entry['converted'])}")
        entries.append(
            {
                **entry,
                "label": key if not entry["repo"] else f"{key} ({entry['repo']})",
                "note": "; ".join(note),
                # pytorch loads from the Hugging Face repo; without one, only the
                # runtimes with converted weights on disk can run this model
                "runnable_on_pytorch": bool(entry["repo"]),
            }
        )
    return {"default": "turbo" if "turbo" in discover() else (entries[0]["key"] if entries else None),
            "models": entries}


@app.get("/languages")
def languages() -> dict:
    """Languages the panel offers, so the list lives only in transcribe.py."""
    return {
        "default": DEFAULT_LANGUAGE,
        "languages": [{"code": code, "label": label} for code, label in LANGUAGES.items()],
    }


@app.get("/devices")
def devices(rescan: bool = False) -> dict:
    """Input devices available for recording.

    PortAudio snapshots the device list when it initialises, so a mic plugged
    in (or a headset paired) after the server started won't appear until it's
    re-initialised — hence `rescan`, which is refused mid-session because
    tearing PortAudio down would kill the running stream.
    """
    if rescan:
        with _lock:
            if _state["status"] != "idle":
                raise HTTPException(
                    status_code=409, detail="Can't rescan devices while a session is active"
                )
        try:
            sd._terminate()
            sd._initialize()
        except Exception as exc:  # noqa: BLE001 - report instead of 500ing the picker
            raise HTTPException(status_code=500, detail=f"Device rescan failed: {exc}") from exc

    # sd.default.device is a _InputOutputPair (indexable, but not a tuple and
    # not JSON-serialisable), so pull the input slot out by index.
    try:
        default_index = int(sd.default.device[0])
    except (TypeError, ValueError, IndexError):
        default_index = None

    # On Linux several host APIs coexist (ALSA, and PulseAudio/PipeWire
    # surfacing through it), and which one a device comes from changes what
    # selecting it actually does — so report it. macOS only has Core Audio.
    hostapis = sd.query_hostapis()

    inputs = [
        {
            "index": index,
            "name": device["name"],
            "channels": device["max_input_channels"],
            "default": index == default_index,
            "hostapi": hostapis[device["hostapi"]]["name"]
            if device["hostapi"] < len(hostapis)
            else "unknown",
        }
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] > 0
    ]
    return {
        "devices": inputs,
        "default_index": default_index,
        "hostapi_count": len({d["hostapi"] for d in inputs}),
    }


@app.get("/status")
def status() -> dict:
    with _lock:
        return {
            "status": _state["status"],
            "session_id": _state["session_id"],
            "config": _state["config"],
            "error": _state["error"],
            "reference_transcript": _state["reference_transcript"],
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
