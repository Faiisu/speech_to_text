"""Speech gating, strict options, and VAD cost accounting."""

import importlib

import numpy as np
import pytest

from .support import MeasurementCollector, RuntimeFactory, ScriptedRuntime, wav_bytes


def test_vad_options_are_complete_detached_and_strict(api):
    options = {"enabled": True, "threshold": 0.62}
    normalized = api.normalize_vad_options(options)
    assert normalized == {
        "enabled": True,
        "threshold": 0.62,
        "min_speech_duration_ms": 250,
    }
    normalized["enabled"] = False
    assert options == {"enabled": True, "threshold": 0.62}
    assert api.normalize_vad_options() == {
        "enabled": False,
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
    }
    for invalid in (
        True,
        {"other": 1},
        {"enabled": 1},
        {"threshold": 0},
        {"threshold": 1},
        {"threshold": float("nan")},
        {"min_speech_duration_ms": True},
        {"min_speech_duration_ms": 30001},
    ):
        with pytest.raises(api.ConfigurationError):
            api.normalize_vad_options(invalid)


def test_vad_rejection_skips_finite_clip_inference_and_records_vad_cost(api, monkeypatch):
    model_module = importlib.import_module("speech_to_text.features.model_deployment.model")
    monkeypatch.setattr(model_module, "detect_speech", lambda _audio, _options: (False, 0.04))
    runtime = ScriptedRuntime([])
    history = MeasurementCollector()
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        text = api.transcribe_clip(
            wav_bytes([10000] * 1600),
            handle,
            {"chunk_seconds": 0.1, "vad": {"enabled": True}},
            measurement_sink=history.record,
        )
        record, = history.history()
        assert text == ""
        assert runtime.audio == []
        assert record["status"] == "skipped"
        assert record["skip_reason"] == "no_speech"
        assert record["inference_seconds"] == pytest.approx(0.04)
        assert record["elapsed_seconds"] == pytest.approx(0.04)
        assert record["rtf"] == pytest.approx(0.4)
    finally:
        handle.close()


def test_enabled_vad_failure_is_explicit_for_clip_and_live_session(api, monkeypatch):
    error = api.VADUnavailableError("test detector unavailable")
    model_module = importlib.import_module("speech_to_text.features.model_deployment.model")
    monkeypatch.setattr(model_module, "detect_speech", lambda *_args: (_ for _ in ()).throw(error))
    runtime = ScriptedRuntime([])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        with pytest.raises(api.VADUnavailableError, match="test detector unavailable"):
            api.transcribe_clip(
                wav_bytes([10000] * 1600),
                handle,
                {"chunk_seconds": 0.1, "vad": {"enabled": True}},
            )
        assert runtime.audio == []
    finally:
        handle.close()


def test_live_vad_failure_emits_fatal_terminal_events(api, monkeypatch):
    session_module = importlib.import_module("speech_to_text.features.model_deployment.session")
    error = api.VADUnavailableError("test detector unavailable")
    monkeypatch.setattr(session_module, "detect_speech", lambda *_args: (_ for _ in ()).throw(error))
    from .support import ManualAudioSource, collect_until_completed, stop_and_close

    source = ManualAudioSource()
    runtime = ScriptedRuntime([])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    session = api.start_microphone_flow(
        model_handle=handle,
        flow_config={"source_id": "vad-live-error", "chunk_seconds": 0.1, "vad": {"enabled": True}},
        audio_source_factory=source,
    )
    try:
        source.push(np.full(1600, 0.3))
        session.stop(timeout=3)
        events = collect_until_completed(session)
        error_event, = [event for event in events if event["type"] == "error"]
        assert error_event["code"] == "VAD_FAILED"
        assert error_event["fatal"] is True
        assert events[-1]["status"] == "failed"
        assert runtime.audio == []
    finally:
        stop_and_close(handle, session)


def test_vad_speech_keeps_original_chunk_and_adds_cost_to_clip_rtf(api, monkeypatch):
    model_module = importlib.import_module("speech_to_text.features.model_deployment.model")
    monkeypatch.setattr(model_module, "detect_speech", lambda _audio, _options: (True, 0.03))
    runtime = ScriptedRuntime(["recognized"])
    history = MeasurementCollector()
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    clock_values = iter([1.0, 1.2, 1.7])
    samples = np.full(1600, 10000, dtype=np.int16)
    try:
        text = api.transcribe_clip(
            wav_bytes(samples),
            handle,
            {"chunk_seconds": 0.1, "vad": {"enabled": True}},
            measurement_sink=history.record,
            monotonic_clock=lambda: next(clock_values),
        )
        record, = history.history()
        assert text == "recognized"
        assert len(runtime.audio) == 1
        assert np.allclose(runtime.audio[0], samples / 32768)
        assert record["queue_wait_seconds"] == pytest.approx(0.2)
        assert record["inference_seconds"] == pytest.approx(0.53)
        assert record["elapsed_seconds"] == pytest.approx(0.73)
        assert record["rtf"] == pytest.approx(7.3)
    finally:
        handle.close()


def test_mixed_clip_measurements_remain_in_original_chunk_order(api, monkeypatch):
    model_module = importlib.import_module("speech_to_text.features.model_deployment.model")
    decisions = iter([(True, 0.01), (False, 0.02), (True, 0.03)])
    monkeypatch.setattr(model_module, "detect_speech", lambda *_args: next(decisions))
    runtime = ScriptedRuntime(["first", "third"])
    history = MeasurementCollector()
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    clip = wav_bytes([10000] * 1600 * 3)
    try:
        assert api.transcribe_clip(
            clip,
            handle,
            {"chunk_seconds": 0.1, "vad": {"enabled": True}},
            measurement_sink=history.record,
        ) == "first third"
        records = history.history()
        assert [record["sequence"] for record in records] == [0, 1, 2]
        assert [record["status"] for record in records] == ["completed", "skipped", "completed"]
    finally:
        handle.close()


def test_disabled_vad_preserves_legacy_path_without_loading_detector(api, monkeypatch):
    model_module = importlib.import_module("speech_to_text.features.model_deployment.model")
    monkeypatch.setattr(
        model_module,
        "detect_speech",
        lambda *_args: pytest.fail("disabled VAD must not load or call the detector"),
    )
    runtime = ScriptedRuntime(["legacy"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    try:
        assert api.transcribe_clip(wav_bytes([10000] * 1600), handle, {}) == "legacy"
        assert len(runtime.audio) == 1
    finally:
        handle.close()


def test_live_vad_skip_is_measured_without_calling_runtime(api, monkeypatch):
    session_module = importlib.import_module("speech_to_text.features.model_deployment.session")
    monkeypatch.setattr(session_module, "detect_speech", lambda _audio, _options: (False, 0.025))
    from .support import ManualAudioSource, collect_until_completed, stop_and_close

    source = ManualAudioSource()
    runtime = ScriptedRuntime([])
    history = MeasurementCollector()
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    handle._measurement_sink = history.record
    session = api.start_microphone_flow(
        model_handle=handle,
        flow_config={"source_id": "vad-live", "chunk_seconds": 0.1, "vad": {"enabled": True}},
        audio_source_factory=source,
    )
    try:
        source.push(np.full(1600, 0.3))
        session.stop(timeout=3)
        events = collect_until_completed(session)
        record, = [event for event in events if event["type"] == "measurement"]
        assert runtime.audio == []
        assert record["status"] == "skipped"
        assert record["skip_reason"] == "no_speech"
        assert record["inference_seconds"] == pytest.approx(0.025)
        assert record["elapsed_seconds"] == pytest.approx(
            record["queue_wait_seconds"] + 0.025
        )
        assert record["rtf"] == pytest.approx(record["elapsed_seconds"] / 0.1)
        assert events[-1]["status"] == "stopped"
    finally:
        stop_and_close(handle, session)


def test_live_accepted_speech_measurement_includes_vad_cost(api, monkeypatch):
    session_module = importlib.import_module("speech_to_text.features.model_deployment.session")
    monkeypatch.setattr(session_module, "detect_speech", lambda _audio, _options: (True, 0.04))
    from .support import ManualAudioSource, collect_until_completed, stop_and_close

    source = ManualAudioSource()
    runtime = ScriptedRuntime(["speech"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    session = api.start_microphone_flow(
        model_handle=handle,
        flow_config={"source_id": "vad-live-speech", "chunk_seconds": 0.1, "vad": {"enabled": True}},
        audio_source_factory=source,
    )
    try:
        source.push(np.full(1600, 0.3))
        session.stop(timeout=3)
        events = collect_until_completed(session)
        record, = [event for event in events if event["type"] == "measurement"]
        assert runtime.audio and len(runtime.audio[0]) == 1600
        assert record["status"] == "completed"
        assert record["inference_seconds"] >= 0.04
        assert record["elapsed_seconds"] == pytest.approx(
            record["queue_wait_seconds"] + record["inference_seconds"]
        )
        assert record["rtf"] == pytest.approx(record["elapsed_seconds"] / 0.1)
    finally:
        stop_and_close(handle, session)


def test_cached_detector_state_is_reset_before_and_after_each_chunk(monkeypatch):
    vad_module = importlib.import_module("speech_to_text.features.model_deployment.vad")

    class Detector:
        def __init__(self):
            self.resets = 0

        def reset_states(self):
            self.resets += 1

    detector = Detector()
    monkeypatch.setattr(vad_module, "_model", detector)
    monkeypatch.setattr(vad_module, "_timestamps", lambda *_args, **_kwargs: [{"start": 0, "end": 1}])
    options = {"threshold": 0.5, "min_speech_duration_ms": 250}
    samples = np.ones(1600, dtype=np.float32)
    assert vad_module.detect_speech(samples, options)[0] is True
    assert vad_module.detect_speech(samples, options)[0] is True
    assert detector.resets == 4


def test_real_silero_rejects_noise_and_tone_and_keeps_reference_speech(api):
    from speech_to_text.features.model_deployment.audio import read_clip

    runtime = ScriptedRuntime(["reference speech"])
    handle = api.load_model({}, runtime_factory=RuntimeFactory(runtime))
    history = MeasurementCollector()
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.08, 160000).astype(np.float32)
    time_axis = np.arange(160000, dtype=np.float32) / 16000
    tone = (0.25 * np.sin(2 * np.pi * 440 * time_axis)).astype(np.float32)
    speech = read_clip("audio/test-audio.wav")[:160000]
    options = {
        "chunk_seconds": 10,
        "silence_threshold": 0,
        "vad": {"enabled": True},
    }
    try:
        assert api.transcribe_clip(
            wav_bytes((noise * 32767).astype(np.int16)),
            handle,
            options,
            measurement_sink=history.record,
            source_id="noise-smoke",
        ) == ""
        assert api.transcribe_clip(
            wav_bytes((tone * 32767).astype(np.int16)),
            handle,
            options,
            measurement_sink=history.record,
            source_id="tone-smoke",
        ) == ""
        assert api.transcribe_clip(
            wav_bytes((speech * 32767).astype(np.int16)),
            handle,
            options,
            measurement_sink=history.record,
            source_id="speech-smoke",
        ) == "reference speech"
        assert len(runtime.audio) == 1
        assert np.allclose(runtime.audio[0], speech, atol=1 / 32768)
        records = history.history()
        assert [record["status"] for record in records] == ["skipped", "skipped", "completed"]
        assert all(record["inference_seconds"] > 0 for record in records)
        assert all(record["rtf"] == pytest.approx(record["elapsed_seconds"] / record["audio_seconds"]) for record in records)
    finally:
        handle.close()
