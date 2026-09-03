# Context Map

## Contexts

- [Speech-to-Text](./CONTEXT.md): records live audio, spots target keywords, reports detections to the backend. Its code lives at the repo root, so its context file stays at the repo root too.
- [Backend](./backend/CONTEXT.md): owns the TimescaleDB event log and exposes the HTTP API for ingesting and querying keyword detections.

## Relationships

- **Speech-to-Text → Backend**: Speech-to-Text POSTs a detection event (word, time, model, session_id) to the Backend's ingest endpoint over plain HTTP whenever a keyword is spotted. No message queue, no retry beyond a short request timeout — both services run on the same machine for this demo.
