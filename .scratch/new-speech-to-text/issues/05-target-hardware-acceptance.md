# 05. Target machine model, microphone, and capacity proof

Status: ready-for-human
Execution: pending-prerequisites
Blocked by: 01, 02, 03, 04

## Scope

Run deployment proofs on the actual macOS microphone host and Advantech UBX-330M Linux target. This ticket requires attached hardware, installed model weights, an independently verified versioned Thai reference set, and operator access; source-code completion cannot close it.

## Acceptance checklist

- [ ] Run the documented macOS microphone smoke test with a named device and report the observed model/runtime and clean shutdown.
- [ ] Install and load `turbo` with OpenVINO GPU at source precision on the UBX-330M; record device and conversion/load outcomes.
- [ ] Replay the versioned, independently checked Thai reference WAV; record hashes, model/runtime/device/precision/language, transcript, per-chunk latency, RTF, and normalized CER at most 20%.
- [ ] Run three microphones at audio pace using shared-model IPC and per-input model processes; separately report memory, queue growth, dropped chunks, and real-time sustainment.
- [ ] Exercise device disconnect/reconnect and queue overload on target hardware and record source isolation behavior.
- [ ] Record all results as reproducible evidence; mark any unavailable prerequisite or failed run explicitly.

## Comments

This remains `ready-for-human` until target hardware and verified reference material are available. It cannot be marked completed based on injected-source tests or the archived PoC benchmark.

Partial local evidence: the Mac named microphone was exercised through both process topologies with an injected runtime; see `../evidence/local-ipc-microphone-smoke.json`. This establishes capture/IPC routing/clean process shutdown only. Real model recognition on microphone input, OpenVINO GPU, verified Thai CER, and target capacity remain unchecked.
