# 03: Verify microphone-to-model pipeline

**What to build:** Tests and live evidence proving that microphone audio passes through capture, overlapping chunks, the bounded queue, and the selected runtime to produce station-tagged transcript events. Use fixed-clip replay to make failures reproducible through the same engine. Completion includes the real capture and inference integration verified by tickets 01 and 02.

**Blocked by:** 01: Verify microphone connection; 02: Verify model deployment and inference.

Status: ready-for-agent

**Audit protocol:** [Feature verification audit](../spec.md)

- [ ] Known audio samples reach the runtime as 16 kHz mono chunks with the configured length, step, overlap, and language.
- [ ] The runtime output reaches transcript events with the correct station identity and health accounting.
- [ ] Audio below the configured silence threshold skips inference and emits the expected silent-chunk event.
- [ ] Queue overload remains bounded, drops the oldest queued chunk, and attributes drops to the correct station; reuse existing regression coverage.
- [ ] Runtime processing failures remain observable, and station shutdown terminates capture and workers cleanly.
- [ ] A deterministic pipeline test exercises capture delivery through the queue and worker to transcript events with controlled audio and a fake runtime.
- [ ] Fixed-clip replay and a live microphone run both produce transcript evidence using the real selected runtime. Record input identity, chunk settings, station, language, processed/silent/dropped counts, and output.
- [ ] Record PASS, FAIL, or SKIP per case with reasons. Keep simulated, replay, and live results distinct; prerequisites not available do not constitute successful integration proof.

## Comments

### 2026-10-09 current-state audit

- **PASS, deterministic capture→queue→worker:** a controlled callback supplied 1,200 known samples. The production capture and engine emitted two 800-sample chunks 400 samples apart; their 400-sample overlap matched exactly, the runtime received language `th`, and both transcript events carried station `line1` / `Line 1`.
- **PASS, deterministic failure/accounting:** silence skipped runtime inference and emitted a silent chunk; an inference exception appeared in station health and an error event; queue overflow dropped the oldest item and attributed it to the evicted station. Worker and capture thread references were retained and verified stopped.
- **PASS, production replay mechanics; target criterion FAIL:** `tests/proof/real_pipeline.py --source replay` exercised `ReplayCapture → Engine → CTranslate2Runtime`. One chunk completed with no runtime error, no drop, an empty queue, and capture/worker threads exited. Transcript: `เกม สิน สิน …`; expected `สวัสดี` was absent, so no keyword event/report was emitted. Clip identity and provenance are recorded in ticket 02.
- **SKIP, configured runtime and live model path:** OpenVINO GPU is unavailable in this environment. The live microphone runner used a capture-only runtime; no person spoke a target phrase, so real microphone-to-model evidence is pending.

This ticket remains open until the configured runtime and a live spoken target produce the required pipeline evidence.
