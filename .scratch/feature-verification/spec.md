# Feature verification audit

## Purpose

This effort records the current behavior of the existing speech-to-text program before any new program is built. Work in this directory adds audit tests and proof runners, captures evidence, and records defects. It does not change production behavior. The domain terms and intended behavior remain defined by the [legacy glossary](../../legacies-poc/CONTEXT.md) and [legacy ADRs](../../legacies-poc/docs/adr/).

## Archive location

The audited PoC now lives in [`legacies-poc/`](../../legacies-poc/). Its authoritative glossary and ADRs moved with it. Run the proof commands below from that directory; the tests and runners retain their paths relative to the PoC root. Previously retained outputs and ticket comments preserve their original run paths as historical evidence. For the existing repository-root virtual environment, substitute `../.venv/bin/python` for `uv run --no-sync python`.

## Evidence rules

- **PASS** means the named check ran against the named subject and met its criterion.
- **FAIL** means an executed check did not meet its criterion. An observed failure is reported as evidence, not reinterpreted as an implementation defect unless the criterion and input identity are established.
- **SKIP** means a required device, model/runtime, permission, or isolated database prerequisite was unavailable. A mock cannot qualify as live proof.
- Deterministic tests use production modules with controlled device callbacks, model boundaries, time, or a dedicated database. Strict expected failures preserve reproducible current defects until a separate implementation task is authorized.
- Real-model replay records the clip path, SHA-256, duration, model key, runtime, language, transcript, and detections. Target phrases must come from evidence independent of the current run. A filename alone does not prove clip provenance.
- Live microphone capture records the exact device name, station identity, chunks and sample count. Silence is not a connection failure. Unplug/reconnect evidence requires a human to perform the physical action.
- Database proof requires an explicit database whose name ends in `_test`, applies the production schema and migration, creates unique evidence rows through the API, and removes only its own session rows. It never uses application data or project compose volumes.

## Proof runners

- `tests/proof/microphone_smoke.py` checks a named physical microphone and station lifecycle. Use `--manual-reconnect` only when a person is available to unplug and reconnect the device.
- `tests/proof/real_pipeline.py` runs the production `ReplayCapture` or `StationCapture` through `Engine` and a real installed runtime. Its local HTTP collector verifies the reporting request boundary but does not prove database persistence.
- `tests/proof/database_event_log.py` exercises the backend API against an explicitly named isolated TimescaleDB database. It also sends a controlled spotting sequence through `report_event` and the HTTP ingest boundary.

Each ticket below records its own current outcome and remaining criteria; this file is the shared audit protocol, not a copy of ticket results.

## Retained audit evidence

The following raw outputs were retained from verification runs on 2026-10-09. These are current-state observations, not proof that every ticket has passed. Read the per-check outcomes, including strict expected failures, rather than treating a successful test-process exit as complete feature acceptance.

- [Deterministic pytest output and expected-failure traces](evidence/pytest.txt), from `uv run --no-sync --group dev pytest -q -rx`.
- [Live microphone lifecycle output](evidence/microphone.txt), from `uv run --no-sync python tests/proof/microphone_smoke.py --device 'MacBook Air Microphone' --duration 1`. This checks physical capture with a capture-only fake runtime; it does not prove real-model inference or physical unplug recovery.
- [Real-model replay output and full transcript](evidence/replay.txt), from `uv run --no-sync python tests/proof/real_pipeline.py --source replay --model turbo --runtime ctranslate2 --clip audio/test_clip.wav --keywords 'สวัสดี'`. The target check failed and the process exited with status 1; clip provenance remains unresolved.
- [Isolated TimescaleDB/API output](evidence/database.txt), from the database runner using `uv run --no-sync --with 'psycopg[binary]'` and an explicit temporary test-database URL. The database ran in a dedicated tmpfs container, separate from application volumes, and generated event rows were cleaned. The temporary container was removed after verification.
