# Context Map

## Contexts

- [Speech-to-Text](./CONTEXT.md): captures or replays audio, transcribes it in overlapping chunks, spots keywords, and reports detections. Covers `transcribe.py` (CLI), `server.py` (web GUI + HTTP API, including the benchmark), `runtimes.py` (interchangeable inference backends), and `model_catalog.py` (which models this machine can run, discovered rather than declared). Its code lives at the repo root, so its context file stays at the repo root too.
- [Backend](./backend/CONTEXT.md): owns the TimescaleDB event log and exposes the HTTP API for ingesting and querying keyword detections.

## Relationships

- **Speech-to-Text → Backend**: Speech-to-Text POSTs a detection event (word, time, model, session_id) to the Backend's ingest endpoint over plain HTTP whenever a keyword is spotted. No message queue, no retry beyond a short request timeout — both services run on the same machine for this demo. See ADR 0003.

## Why they're split

The Backend and its database run in docker-compose; Speech-to-Text runs natively. That isn't arbitrary — a container can't reach the host microphone without awkward audio passthrough, and Speech-to-Text needs it. The split follows from that one constraint. See ADR 0003.
