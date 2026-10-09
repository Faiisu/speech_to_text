# Performance is measured by replaying a fixed clip, never from a live microphone

Early performance figures came from live microphone sessions and were not comparable with each other: the same model on the same machine measured RTF 0.48–0.70 in one session and 1.30–1.90 in another, purely because one session contained sparse short utterances and the other continuous speech. A chunk densely packed with words takes far longer to transcribe than a chunk holding one short phrase, so the input dominates the result. Two live runs can never be compared, which makes them useless for deciding whether an optimisation helped.

Measurement therefore replays a **fixed audio file** through the identical chunking path (`run_replay_session`, and `POST /benchmark` for multi-runtime comparison). Same clip, same 5s/1s-overlap windows, same silence gate — so the only variable is what's being tested. Chunks are processed as fast as the model manages rather than paced to real time, since pacing would only add wall-clock time without changing the per-chunk latency being measured.

The replay path emits exactly the same event stream as a live session, so the web GUI renders it identically and needs no separate view. It also removes the microphone as a prerequisite for evaluating a machine — which mattered in practice, because the deployment box could not capture audio at all until an unrelated permissions problem was fixed.

Clips live in `audio/`, either uploaded through the panel or recorded on the server itself. Recording a clip once and replaying it is the intended workflow for comparing runtimes or models: record once, then change one variable at a time.

**Consequence:** benchmark numbers are only comparable against other runs on the *same clip*. A clip of realistic continuous speech should be used, since short test phrases systematically flatter the result — and the numbers say nothing about accuracy, only speed.
