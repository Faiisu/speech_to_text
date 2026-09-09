# 15: Production station service — three microphones, one iGPU

**What to build:** The real deployment, as opposed to the PoC: `typhoon-whisper-turbo` on OpenVINO iGPU on the UBX-330M, several labelled microphones running continuously, a web application to configure and monitor them, keyword detections stored per station, and a parallel benchmark that answers how many microphones one machine sustains.

**Blocked by:** 12, 14

**Status:** done — pending an iGPU run on the UBX-330M

## Why the PoC couldn't be extended

Four things blocked directly, none of them a parameter change:

1. **One session at a time.** `server.py` holds a single `_state` dict and `/start` returns 409 while a session is active.
2. **Unbounded capture.** `run_recording_session` appends every sample of the session to `frames` and never discards — three microphones is ~690 MB/hour, growing forever.
3. **Quadratic chunking.** `np.concatenate(frames)` runs per chunk, so cost grows with session length. A PoC records for minutes; a station runs for weeks.
4. **Contention.** `chunk_loop` calls the model inline, so three capture threads would each decode against one iGPU and all three would fall behind.

The PoC modules are untouched and still work; the production service is a separate package and a separate app.

## Decisions

- **Keyword hits only in the DB**, not transcripts. Live output is monitor-only.
- **STT bare-metal, DB in Docker.** The OpenVINO GPU plugin must match the host i915 driver; an image that drifts from the host is the usual way an iGPU deploy breaks.
- **One shared model behind a bounded queue.** A second copy on one iGPU contends rather than parallelises.
- **Drop-oldest under overload**, counted per station and shown in the UI. The alternative — an unbounded backlog — looks like a working system until memory runs out.
- **Devices bound by name, never index.** Indices shift on replug, which would mislabel every detection from that station.

## Delivered

- [x] `stations/config.py` — station and shared settings, JSON-persisted, validated (duplicate labels and ids, unknown fields, chunking, language, threshold)
- [x] `stations/capture.py` — constant-memory ring buffer per station; device resolution by name with ambiguity treated as an error
- [x] `stations/engine.py` — one shared runtime, bounded drop-oldest queue, per-station health and rolling RTF
- [x] `stations/supervisor.py` — lifecycle, per-station isolation, watchdog that restarts a station whose microphone returns
- [x] `stations/replay.py` — file/video-driven stations for verification and benchmarking, via ffmpeg
- [x] `production_server.py` + `static/monitor.html` — config, live monitor, history; SSE feed with recent-event replay for a page opened mid-run
- [x] `station` column: `backend/db/init.sql`, idempotent migration in `backend/db/migrations/`, `backend/app/main.py` ingest + filter, `transcribe.report_event`
- [x] `deploy/preflight.sh`, `deploy/install.sh`, `deploy/stt-stations.service`, `deploy/stations.example.json`
- [x] `benchmark_parallel.py` — concurrent-stream capacity sweep through the real engine and queue
- [x] `tests/` — 22 tests covering the leak, the backpressure, the config, the device binding, and what reaches the DB

## Verified on the Mac (ctranslate2 standing in for the iGPU)

- Ring buffer holds 4.0s of audio after 2 simulated hours; the PoC would have held 115M samples
- Queue bound held under 1000 concurrent puts across two producers, with every chunk accounted for (996 dropped + 4 held)
- Three labelled stations ran concurrently through one shared model; each posted correctly-labelled detections with its own session id
- Live two-station microphone run: bounded buffers, isolated per-station stop, clean shutdown
- Migration applied to an **existing** volume that still had the old schema — which is exactly the case `init.sql` does not cover
- Capacity sweep correctly reported "fell behind" at 1 and 2 streams on this Mac's CPU runtime (RTF 1.54), with drops appearing at 2

## Still to do

- Run `deploy/preflight.sh` and a real three-microphone run on the UBX-330M's iGPU
- Run the capacity sweep there to confirm three stations fit, or find the chunk length that makes them fit
- **No authentication.** The UI binds `0.0.0.0` like the PoC panel. Fine on a trusted LAN, not beyond one.
