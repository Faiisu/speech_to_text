# Speech-to-Text Testing

Testing and benchmarking Typhoon Whisper speech-to-text models (Thai-language Whisper fine-tunes) for transcription quality and hardware performance.

## Language

**Real-time transcription**:
Live incremental transcription of audio as it's being spoken, produced by re-transcribing overlapping buffered chunks while recording is still in progress (not a native streaming model capability). Distinct from a single batch transcription run over a completed recording.
_Avoid_: Streaming (ambiguous — could imply native model streaming support, which these models don't have)

**Chunk**:
A fixed-length window of buffered audio (5 seconds by default, with 1 second of overlap with the previous chunk) that gets independently transcribed during a real-time session. Overlapping regions are not deduplicated or stitched — each chunk's transcript is logged as-is. The length is a property of the session, chosen per run and capped at 30 seconds because Whisper pads every chunk to a 30-second window and discards anything beyond it; a longer chunk therefore spends the same fixed encoder pass on more audio.

**RTF_chunk**:
Real-time factor for a single chunk: transcription latency for that chunk divided by the chunk's audio duration. RTF_chunk < 1 means the model kept pace with live speech; RTF_chunk ≥ 1 means it fell behind.

**Reference transcript**:
The full-clip batch transcription produced once, after a real-time session stops, by transcribing the entire recording in one pass. Used as a manual quality comparison point against the chunked/streamed output — not a ground-truth score (no automated WER/diff, since no independently-verified ground truth exists).

**Keyword spotting**:
Detecting whether one or more target keywords/phrases were spoken during a real-time session, by exact substring match against each chunk's transcribed text. Distinct from general transcription — the goal is "was this said," not a full readable transcript.
_Avoid_: Keyword detection, wake word (this project's targets aren't limited to wake words)

**Debounce window**:
The time window during which a repeat alert for the same keyword text is suppressed, to avoid double-alerting when a keyword falls across the overlap between two chunks. Sized to the chunk *step* interval (chunk length minus overlap) plus a small buffer, not the overlap duration itself — an overlap-caused duplicate detection always lands exactly one chunk later, i.e. one step apart, not within the overlap window. See ADR 0002.

**Model**:
The trained weights that do the transcribing, named by a short **model key** (`turbo`) rather than its Hugging Face repo. Which models exist is a property of the machine, discovered from what is installed, not a fixed list — so "the models" means different things on two machines and is always answered by asking, never assumed.
_Avoid_: Runtime (that's the machinery that executes a model, not the model)

**Language**:
Which language a runtime is told to transcribe, chosen per session (default Thai). Every runtime honours the same setting, so it is a property of the session rather than of the runtime. Setting it to **auto-detect** delegates the choice to the model, which then decides per chunk — a different thing from picking a language, and the reason the two are named separately here.
_Avoid_: Locale (implies formatting/region, not speech content)

**Silence gate**:
The loudness floor below which a chunk is skipped entirely rather than transcribed, because these models invent text when fed silence. Expressed as an RMS threshold (default `0.02`). Separate from, and not a substitute for, the anti-repetition generation settings — the two address different hallucination failures. See ADR 0004.

## Execution

**Runtime**:
The machinery that executes a model, chosen independently of *which* model runs: `pytorch`, `openvino-gpu`, `openvino-cpu`, `openvino-npu`, `ctranslate2`, or `whispercpp`. Swapping runtime changes speed, not the model. See ADR 0005.
_Avoid_: Backend (means the TimescaleDB service in this project), engine, device

In the station service the runtime is **shared**: one loaded model serves every station, behind a queue. A second copy would not run in parallel on a single iGPU — it would contend for the same execution units — so more stations mean more queueing, not more throughput.

**Session**:
One run that produces transcripts — either a **live session** reading from a microphone, or a **replay session** reading a clip from disk. Both use identical chunking and emit identical events; only the audio source differs.

**Station**:
One microphone plus the configuration that gives it meaning: a human **label** ("Line 1"), the audio device it captures from, its keywords, and its language. The unit an operator configures, the monitor displays, and every stored detection is tagged with. Several stations run at once, each with its own session id.

Distinct from a session: a station is the standing configuration, a session is one run of it. A station outlives many restarts; its label is what makes a detection row mean something a week later.
_Avoid_: Channel (suggests audio channels within one stream), input, source

**Device name**:
How a station identifies its microphone — the device's *name*, never its index. PortAudio indices shift when a USB mic is replugged or the machine reboots, so an index would silently rebind a station to a different microphone and mislabel everything it detected. An ambiguous name is an error rather than a first-match guess, for the same reason.

**Drop**:
A chunk discarded without being transcribed, because the shared model was too far behind and the queue was full. The oldest queued chunk goes first. Dropping is the deliberate alternative to an unbounded backlog: audio is lost, but it is lost *visibly* and counted per station, whereas a growing queue looks like a working system until the machine runs out of memory.
_Avoid_: Skip (that is the silence gate, which is a different decision — see **Silence gate**)

**Replay**:
Feeding a stored clip through the live chunking path instead of a microphone. The only way to compare runtimes, models, or machines, since two live sessions are never the same input. See ADR 0006.
_Avoid_: Playback (implies audio being played aloud; nothing is played)

**Clip**:
An audio file in `audio/` used as replay input — uploaded through the panel, recorded by the server, or copied in directly. Always stored as 16kHz mono; anything else is converted on the way in.
