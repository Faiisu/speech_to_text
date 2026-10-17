# Microphone input diagnostic

Status: wontfix
Legacy outcome: Implemented a bounded no-model microphone signal test with an RMS/peak frontend meter.

## Scope

Implement the [live audio input test contract](../spec.md), including a bounded in-memory backend diagnostic workflow and its microphone test API, then replace the initial profile/run page with a no-model live input-level meter. No audio storage or transcription.

## Validation

Review lifecycle and storage boundaries, then run the production frontend build. Do not add or run tests for this task.

## Comments

The page lists host microphones and provides live signal levels, start/stop, and status feedback. The backend diagnostic never loads a model or writes audio. Production frontend build and Python compile checks passed; automated tests and real hardware capture were not run.
