# 02: Verify model deployment and inference

**What to build:** Tests proving that the selected model and runtime can be loaded on the target machine and produce a transcript from a fixed reference clip. Model discovery or dependency availability alone must not qualify as successful deployment. Follow the existing runtime and replay ADRs rather than introducing another deployment path.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

**Audit protocol:** [Feature verification audit](../spec.md)

- [ ] The configured model and runtime load successfully and perform inference on a fixed clip with documented target keywords.
- [ ] Real inference returns a non-empty transcript containing the clip's specified target keywords; retain the actual transcript as evidence rather than asserting an exact full transcript.
- [ ] Unknown model keys, absent or corrupt required weights, load failures, and unavailable runtime devices produce observable failures without reporting a ready service.
- [ ] Deterministic tests isolate runtime loading and failure propagation without downloading models or requiring accelerators.
- [ ] Existing single-worker restrictions for GPU/NPU are verified before loading; reuse existing coverage where appropriate.
- [ ] Real verification records model key, runtime, device, clip identity, language, transcript, and outcome. Test the configured deployment combination; unsupported combinations are reported explicitly.
- [ ] Record PASS, FAIL, or SKIP for each case, with evidence and reasons. Missing hardware or weights are SKIP; mock success is reported separately from real inference.

## Comments

### 2026-10-09 current-state audit

- **PASS, deterministic:** unknown model keys fail before runtime construction; the production CTranslate2 loader reports missing converted weights; engine load failures propagate without setting a ready runtime. The PyTorch runtime boundary test confirms the requested language reaches generation. Existing accelerator worker-count restrictions remain covered by `tests/test_stations.py`.
- **SKIP, configured deployment:** runtime probe reports `openvino-gpu` unavailable because `openvino + optimum-intel not installed`; the configured Intel GPU/model combination could not be exercised on this Mac.
- **PASS, real load/inference only:** the installed `turbo` CTranslate2 runtime loaded and processed one 5-second chunk from the local six-second clip. The transcript was non-empty. No multi-gigabyte model was downloaded.
- **FAIL, declared-target comparison:** using target `สวัสดี` documented for `audio/test_clip.wav`, the production replay path returned `เกม สิน สิน …` and no target match. Evidence input: `/Volumes/Mac_storage/projects.nosync/speech_to_text/audio/test_clip.wav`, SHA-256 `6ee9440a83ce94a6768dce80b0591e2252cee7f646f4774cf9e0dc06730760ae`, 96,000 samples at 16 kHz. The file is local and its provenance against the historically documented clip has not been established, so this is a failed target check for this input, not a conclusion that the runtime is malfunctioning.
- **SKIP, weights/runtime:** Hugging Face reports no cached PyTorch snapshot with local-only lookup; no download was attempted. OpenVINO real hardware proof remains unavailable.

Keep this ticket open: the configured OpenVINO combination has no real proof, and the local clip did not meet the declared-target criterion.

The complete transcript, per-check results, and clip identity are retained in [the real-model replay evidence](../evidence/replay.txt). Shared run commands and other artifacts are indexed by the audit protocol above.
