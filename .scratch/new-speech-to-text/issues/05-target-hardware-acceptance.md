# 05. Target machine model, microphone, and capacity proof

Status: ready-for-human
Execution: linux-model-deployment-verified; reference-quality-and-capacity-pending
Blocked by: 01, 02, 03, 04

## Scope

Run deployment proofs on the actual macOS microphone host and Advantech UBX-330M Linux target. This ticket requires attached hardware, installed model weights, an independently verified versioned Thai reference set, and operator access; source-code completion cannot close it.

## Acceptance checklist

- [x] Run the documented macOS microphone smoke test with a named device and report the observed model/runtime and clean shutdown.
- [x] Install and load `turbo` with OpenVINO GPU at source precision on the UBX-330M; record device and conversion/load outcomes.
- [ ] Replay the versioned, independently checked Thai reference WAV; record hashes, model/runtime/device/precision/language, transcript, per-chunk latency, RTF, and normalized CER at most 20%.
- [ ] Run three microphones at audio pace using shared-model IPC and per-input model processes; separately report memory, queue growth, dropped chunks, and real-time sustainment.
- [ ] Exercise device disconnect/reconnect and queue overload on target hardware and record source isolation behavior.
- [ ] Record all results as reproducible evidence; mark any unavailable prerequisite or failed run explicitly.

## Comments

This remains `ready-for-human` until target hardware and verified reference material are available. It cannot be marked completed based on injected-source tests or the archived PoC benchmark.

Partial local evidence: the Mac named microphone was exercised through both process topologies with an injected runtime; see `../evidence/local-ipc-microphone-smoke.json`. This establishes capture/IPC routing/clean process shutdown only. Real model recognition on microphone input, OpenVINO GPU, verified Thai CER, and target capacity remain unchecked.

Later evidence supersedes the earlier OpenVINO GPU and Mac capture statements below: the Mac Docker bridge captured and inferred from the named microphone using the real CTranslate2 runtime, and the UBX-330M loaded `turbo/openvino-gpu/source` and completed real clip inference. See [`Mac Docker microphone proof`](../evidence/mac-docker-microphone-20261009.json) and [`Linux local RTF proof`](../evidence/linux-local-rtf-20261009.json). The microphone run does not establish recognition accuracy; the Linux clip lacks an independently verified reference transcript. Three-microphone capacity, both real-model process topologies, disconnect/reconnect, and overload remain pending.

## Linux deployment proof: 2026-10-09

Deployed base commit `46d6015` with the runtime token-budget correction to `/home/ubx-330m/apps/speech_to_text` on the UBX-330M. Dependencies and download caches are local to the project. The initial deployment enabled `speech-feature-01.service` on loopback port 8765; the separate `speech-feature-01` Compose project serves TimescaleDB on 5434 and Grafana on 3000 without replacing the existing services. Machine credentials remain in the untracked, mode-0600 `.env`.

OpenVINO detected CPU and Intel Meteor Lake GPU. The default Turbo/source model converted successfully to `models/openvino-turbo-source` and loaded on GPU. Real inference initially exposed an excessive decoder token budget; the adapter now reserves start/language/task/timestamp positions for explicit and automatic language selection, including partial forced-token configurations. Three regression tests cover this failure. The final Linux software suite passed 103 tests; seven skipped (five requiring Chrome/Chromium and two opt-in hardware proofs).

Observed real-model results are recorded in [Linux deployment evidence](../evidence/linux-deployment-20261009.json):

| Input/configuration | Audio duration | Observed RTF | Result |
| --- | --- | --- | --- |
| Thai, 5-second chunks | 21.129 s | 0.615 aggregate; 1.606 final partial chunk | Text and five DB measurements; per-chunk realtime gate not fully met |
| Thai, 30-second chunk limit | 21.129 s | 0.288 | Text and one DB measurement; this clip met realtime pace |
| Automatic language, 30-second chunk limit | 6.252 s | 3.008 | Text and one DB measurement; realtime gate failed |

The default microphone produced finite but silent samples. Its API flow started, stopped, and emitted completion; lifecycle events reached the database. This proves capture/control plumbing, not speech recognition quality. No independently verified reference transcript was available, so CER was not evaluated. Three concurrent microphones, both real-model process topologies, disconnect/reconnect, and overload remain pending. This ticket is not complete.

The subsequent [container deployment](../../system-observability/issues/03-containerized-main-service.md#target-verification-2026-10-09) replaced the primary systemd process with a healthy Docker application on the same port and verified rollback. That ticket is authoritative for the current deployment mechanism; the hardware quality and capacity gates above remain open.
