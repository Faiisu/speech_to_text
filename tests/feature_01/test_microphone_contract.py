"""Controlled capture at the external audio boundary; the system owns its pipeline."""

from queue import Empty

import numpy as np
import pytest

from .support import (
    ManualAudioSource, RuntimeFactory, ScriptedRuntime,
    collect_until_completed, stop_and_close, texts, wav_bytes,
)


def start(api, handle, source, source_id="mic-1", **settings):
    return api.start_microphone_flow(
        model_handle=handle,
        flow_config={"source_id": source_id, "chunk_seconds": 0.1, **settings},
        audio_source_factory=source,
    )


def test_callback_sizes_do_not_define_chunks_and_stop_flushes_partial_audio(api):
    source = ManualAudioSource()
    runtime = ScriptedRuntime(["first", "remainder"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    session = start(api, handle, source)
    try:
        source.push(np.full(600, 0.3))
        source.push(np.full(900, 0.3))
        source.push(np.full(500, 0.3))
        session.stop(timeout=3)
        events = collect_until_completed(session)
        assert texts(events) == ["first", "remainder"]
        assert [len(audio) for audio in runtime.audio] == [1600, 400]
        transcripts = [event for event in events if event["type"] == "transcript"]
        assert [event["sequence"] for event in transcripts] == [0, 1]
        assert all(event["source_id"] == "mic-1" for event in events)
        assert events[-1]["status"] == "stopped"
        assert source.stopped
        session.stop(timeout=3)
        with pytest.raises(Empty):
            session.result_queue.get(timeout=0.05)
    finally:
        stop_and_close(handle, session)


def test_default_microphone_is_selected_by_os_and_explicit_name_is_preserved(api):
    runtime = ScriptedRuntime([])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    default_source, named_source = ManualAudioSource(), ManualAudioSource()
    first = start(api, handle, default_source, "default")
    second = api.start_microphone_flow("USB microphone", handle, {"source_id": "named"}, audio_source_factory=named_source)
    try:
        assert default_source.device is None
        assert named_source.device == "USB microphone"
        assert default_source.started and named_source.started
    finally:
        stop_and_close(handle, first, second)


def test_duplicate_source_id_is_rejected_before_opening_second_capture(api):
    handle = api.load_model({}, runtime_factory=RuntimeFactory(ScriptedRuntime([])))
    first_source, duplicate_source = ManualAudioSource(), ManualAudioSource()
    first = start(api, handle, first_source, "same-id")
    try:
        with pytest.raises(api.ConfigurationError, match="already active"):
            start(api, handle, duplicate_source, "same-id")
        assert not duplicate_source.started
    finally:
        stop_and_close(handle, first)


def test_timed_stop_can_be_retried_and_session_stop_is_idempotent_after_close(api):
    source = ManualAudioSource()
    runtime = ScriptedRuntime(["eventual"])
    runtime.release.clear()
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    session = start(api, handle, source)
    source.push(np.full(1600, 0.3))
    assert runtime.entered.wait(2)
    with pytest.raises(TimeoutError):
        session.stop(timeout=0.01)
    runtime.release.set()
    session.stop(timeout=3)
    handle.close(timeout=3)
    session.stop(timeout=0)
    assert texts(collect_until_completed(session)) == ["eventual"]
    assert handle.state == "closed"


def test_failed_microphone_chunk_does_not_stop_later_chunks(api):
    source = ManualAudioSource()
    runtime = ScriptedRuntime(["first", RuntimeError("decode failed"), "third"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    session = start(api, handle, source)
    try:
        source.push(np.full(4800, 0.3))
        session.stop(timeout=3)
        events = collect_until_completed(session)
        assert texts(events) == ["first", "third"]
        error, = [event for event in events if event["type"] == "error"]
        assert error["code"] == "CHUNK_INFERENCE_FAILED"
        assert error["sequence"] == 1
        assert error["fatal"] is False
        assert error["message"]
        assert events[-1]["status"] == "stopped"
    finally:
        stop_and_close(handle, session)


def test_two_sources_share_one_model_and_keep_independent_settings(api):
    runtime = ScriptedRuntime(["left", "right"])
    factory = RuntimeFactory(runtime)
    handle = api.load_model({}, runtime_factory=factory)
    left_source, right_source = ManualAudioSource(), ManualAudioSource()
    left = start(api, handle, left_source, "left", language="th")
    right = start(api, handle, right_source, "right", language="en")
    try:
        left_source.push(np.full(1600, 0.2))
        assert runtime.entered.wait(2)
        right_source.push(np.full(1600, 0.4))
        left.stop(timeout=3)
        right.stop(timeout=3)
        left_events, right_events = collect_until_completed(left), collect_until_completed(right)
        assert texts(left_events) == ["left"]
        assert texts(right_events) == ["right"]
        assert all(event["source_id"] == "left" for event in left_events)
        assert all(event["source_id"] == "right" for event in right_events)
        assert [language for language, _ in runtime.settings] == ["th", "en"]
        assert len(factory.configs) == 1
    finally:
        stop_and_close(handle, left, right)


def test_shared_queue_is_fifo_across_sources(api):
    runtime = ScriptedRuntime(["left-1", "right-1", "left-2"])
    runtime.release.clear()
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    left_source, right_source = ManualAudioSource(), ManualAudioSource()
    left = start(api, handle, left_source, "left")
    right = start(api, handle, right_source, "right")
    try:
        left_source.push(np.full(1600, 0.2))
        assert runtime.entered.wait(2)
        right_source.push(np.full(1600, 0.4))
        left_source.push(np.full(1600, 0.6))
        runtime.release.set()
        left.stop(timeout=3)
        right.stop(timeout=3)
        assert texts(collect_until_completed(left)) == ["left-1", "left-2"]
        assert texts(collect_until_completed(right)) == ["right-1"]
        assert [float(np.mean(audio)) for audio in runtime.audio] == pytest.approx([0.2, 0.4, 0.6])
    finally:
        runtime.release.set()
        stop_and_close(handle, left, right)


def test_queue_timeout_stops_only_overloaded_source_and_discards_its_backlog(api):
    runtime = ScriptedRuntime(["healthy-1", "healthy-2"])
    runtime.release.clear()
    handle = api.load_model({"queue_capacity": 1, "enqueue_timeout_seconds": 0.05}, runtime_factory=RuntimeFactory(runtime))
    healthy_source, overloaded_source = ManualAudioSource(), ManualAudioSource()
    healthy = start(api, handle, healthy_source, "healthy")
    overloaded = start(api, handle, overloaded_source, "overloaded")
    try:
        healthy_source.push(np.full(1600, 0.2))
        assert runtime.entered.wait(2)
        overloaded_source.push(np.full(3200, 0.4))
        failed = collect_until_completed(overloaded)
        assert texts(failed) == []
        error, = [event for event in failed if event["type"] == "error"]
        assert error["code"] == "INPUT_QUEUE_TIMEOUT"
        assert error["fatal"] is True
        assert failed[-1]["status"] == "failed"
        assert overloaded_source.stopped
        assert not healthy_source.stopped
        assert handle.state == "ready"
        runtime.release.set()
        healthy_source.push(np.full(1600, 0.6))
        healthy.stop(timeout=3)
        assert texts(collect_until_completed(healthy)) == ["healthy-1", "healthy-2"]
        assert len(runtime.audio) == 2
    finally:
        runtime.release.set()
        stop_and_close(handle, healthy, overloaded)


def test_capture_failure_emits_terminal_error_and_releases_source(api):
    source = ManualAudioSource()
    handle = api.load_model({}, runtime_factory=RuntimeFactory(ScriptedRuntime([])))
    session = start(api, handle, source)
    try:
        source.on_error(OSError("microphone disconnected"))
        events = collect_until_completed(session)
        error, = [event for event in events if event["type"] == "error"]
        assert error["code"] == "CAPTURE_FAILED"
        assert error["fatal"] is True
        assert "microphone disconnected" in error["message"]
        assert events[-1]["status"] == "failed"
        assert source.stopped
        assert handle.state == "ready"
    finally:
        stop_and_close(handle, session)


@pytest.mark.parametrize("sample_rate", [8000, 44100, 48000])
def test_stream_resampling_is_independent_of_capture_callback_partition(api, sample_rate):
    from speech_to_text.features.model_deployment.audio import StreamingResampler, normalize_audio

    samples = np.sin(np.arange(sample_rate // 2, dtype=np.float32) * 0.01)
    resampler = StreamingResampler(sample_rate)
    chunks = [resampler.feed(samples[index:index + 2]) for index in range(0, len(samples), 2)]
    chunks.append(resampler.flush())
    streamed = np.concatenate(chunks)
    expected = normalize_audio(samples, sample_rate)
    assert streamed.shape == expected.shape
    assert np.allclose(streamed, expected, atol=2e-6)


def test_public_api_accepts_documented_keyword_names(api):
    runtime = ScriptedRuntime(["keyword-call"])
    handle = api.load_model(model_config={}, runtime_factory=RuntimeFactory(runtime))
    try:
        assert api.transcribe_clip(
            clip=wav_bytes([10000] * 1600),
            model_handle=handle,
            flow_config={"chunk_seconds": 0.1},
        ) == "keyword-call"
    finally:
        handle.close()


def test_queue_timeout_ignores_inflight_result_from_failed_source(api):
    runtime = ScriptedRuntime(["must be discarded", "survivor"])
    runtime.release.clear()
    handle = api.load_model({"queue_capacity": 1, "enqueue_timeout_seconds": 0.05}, runtime_factory=RuntimeFactory(runtime))
    failing_source, healthy_source = ManualAudioSource(), ManualAudioSource()
    failing = start(api, handle, failing_source, "failing")
    healthy = start(api, handle, healthy_source, "healthy")
    try:
        failing_source.push(np.full(1600, 0.2))
        assert runtime.entered.wait(2)
        failing_source.push(np.full(3200, 0.2))
        events = collect_until_completed(failing)
        assert texts(events) == []
        assert events[-1]["status"] == "failed"
        runtime.release.set()
        healthy_source.push(np.full(1600, 0.4))
        healthy.stop(timeout=3)
        assert texts(collect_until_completed(healthy)) == ["survivor"]
        with pytest.raises(Empty):
            failing.result_queue.get(timeout=0.05)
    finally:
        runtime.release.set()
        stop_and_close(handle, failing, healthy)
