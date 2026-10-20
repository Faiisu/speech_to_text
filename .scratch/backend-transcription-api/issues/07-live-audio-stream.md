# Live workflow audio playback on the frontend device

Status: wontfix

Legacy outcome: Implemented same-capture 16 kHz mono PCM monitoring for shared and spawned workflow execution, bounded fanout and independent IPC, same-origin WebSocket transport, and explicit browser Listen/Mute controls. No audio is stored and mute/navigation do not stop STT. See the [API contract](../spec.md#ws-transcriptionsworkflow_idaudio).

## Acceptance

- Active workflows stream to the frontend device without opening another microphone.
- Mute, disconnect, workflow completion, recovery, and navigation release listeners.
- Slow listeners do not block STT or grow unbounded queues; audio is absent from event history.
- Focused Python tests, browser lifecycle tests, and production build verify the path with synthetic audio. Real microphone/device acoustic quality remains a hardware check.

Verification: 87 focused Python tests passed; 18 browser tests passed; frontend production build and `git diff --check` passed. Synthetic browser tests verify binary PCM reception and creation of playback buffers, mute without stopping STT, re-listening, and navigation cleanup.
