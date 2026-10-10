# 01. Match and forward completed transcripts

Status: ready-for-agent

## Scope

Implement keyword/phrase matching and outbound forwarding as specified in [Transcript Matching and Forwarding](../spec.md). Compose existing Feature 01 callable interfaces without changing their event contract.

## Acceptance checklist

- [x] Add the Thai `word_matching` module without a tokenizer runtime dependency.
- [x] Match normalized literal substrings and count overlapping occurrences per configured target.
- [x] Add the configurable outbound HTTP forwarder with documented retry and error behavior.
- [x] Add clip and session workflow interfaces, including process-session wrapping.
- [x] Preserve per-source transcript order and forward once after the terminal event.
- [x] Keep all new application code outside `legacies-poc/`.

## Comments

The parent agent reviewed the module boundaries and implementation. Automated tests were not added or run, per task instructions. The actual receiver URL and bearer token remain caller configuration.
