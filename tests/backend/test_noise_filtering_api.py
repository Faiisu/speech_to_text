"""HTTP behavior for the saved-recording noise-filtering workflow."""

from io import BytesIO
import wave

import numpy as np
from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app
from speech_to_text.backend.dependencies import BackendRuntime
from speech_to_text.workflows.audio_recording import RecordingService


class _Closable:
    def close(self):
        pass


def _wav_bytes(rate=8000):
    samples = np.rint(10000 * np.sin(2 * np.pi * 1000 * np.arange(rate) / rate)).astype("<i2")
    target = BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return target.getvalue()


def _uploaded(client, filename, data):
    response = client.post("/api/v1/recordings/upload", files={"file": (filename, data, "audio/wav")})
    assert response.status_code == 201, response.text
    return response.json()


def test_noise_filter_endpoint_returns_saved_copy_and_rejects_nyquist(tmp_path):
    recording_service = RecordingService(output_directory=tmp_path)
    runtime = BackendRuntime(
        workflow_service=_Closable(),
        profile_store=_Closable(),
        destination_registry=object(),
        recording_service=recording_service,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        source_response = client.post(
            "/api/v1/recordings/upload",
            files={"file": ("tones.wav", _wav_bytes(), "audio/wav")},
        )
        assert source_response.status_code == 201
        source = source_response.json()
        assert source["sample_rate"] == 8000
        source_bytes = client.get(f"/api/v1/recordings/{source['recording_id']}/audio").content

        invalid = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": source["recording_id"], "low_hz": 100, "high_hz": 4000,
        })
        assert invalid.status_code == 422
        assert "Nyquist" in invalid.json()["detail"]

        response = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": source["recording_id"], "low_hz": 100, "high_hz": 3500,
        })
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["recording"]["recording_id"] != source["recording_id"]
        assert result["settings"]["nyquist_hz"] == 4000
        assert result["source"]["recording_id"] == source["recording_id"]
        assert client.get(f"/api/v1/recordings/{source['recording_id']}/audio").content == source_bytes
        assert client.get(f"/api/v1/recordings/{result['recording']['recording_id']}/audio").content[:4] == b"RIFF"

        missing = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": "0123456789abcdef0123456789abcdef", "low_hz": 100, "high_hz": 3500,
        })
        assert missing.status_code == 404


def test_noise_profile_endpoint_and_opt_in_denoising_snapshot(tmp_path):
    recording_service = RecordingService(output_directory=tmp_path)
    runtime = BackendRuntime(
        workflow_service=_Closable(), profile_store=_Closable(), destination_registry=object(),
        recording_service=recording_service,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        target = _uploaded(client, "target.wav", _wav_bytes())
        noise = _uploaded(client, "noise.wav", _wav_bytes())
        analysis = client.post("/api/v1/noise-filtering/noise-profiles", json={"recording_id": noise["recording_id"]})
        assert analysis.status_code == 200, analysis.text
        profile = analysis.json()
        assert profile["recording_id"] == noise["recording_id"]
        assert profile["sample_rate"] == 8000
        assert profile["fft_size"] == 1024
        assert profile["window"] == "hann"
        assert "noise_power" not in profile

        invalid = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": target["recording_id"], "low_hz": 100, "high_hz": 3500,
            "denoise_enabled": "true", "noise_recording_id": noise["recording_id"],
        })
        assert invalid.status_code == 422

        result = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": target["recording_id"], "low_hz": 100, "high_hz": 3500,
            "denoise_enabled": True, "noise_recording_id": noise["recording_id"],
        })
        assert result.status_code == 201, result.text
        settings = result.json()["settings"]
        assert settings["denoise_enabled"] is True
        assert settings["noise_recording_id"] == noise["recording_id"]
        assert settings["noise_filename"] == "noise.wav"
        assert settings["denoising_fft_size"] == 1024
        assert settings["denoising_window"] == "hann"
        assert settings["denoising_subtraction_strength"] == 1.0
        assert settings["denoising_smoothing_size"] == 3

        other_rate_noise = _uploaded(client, "other-rate.wav", _wav_bytes(16000))
        mismatch = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": target["recording_id"], "low_hz": 100, "high_hz": 3500,
            "denoise_enabled": True, "noise_recording_id": other_rate_noise["recording_id"],
        })
        assert mismatch.status_code == 422
        assert "sample rate" in mismatch.json()["detail"]
        assert len(client.get("/api/v1/recordings").json()["recordings"]) == 4


def test_noise_profiles_and_filtering_accept_multiple_sources_and_reject_bad_collections(tmp_path):
    recording_service = RecordingService(output_directory=tmp_path)
    runtime = BackendRuntime(
        workflow_service=_Closable(), profile_store=_Closable(), destination_registry=object(),
        recording_service=recording_service,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        target = _uploaded(client, "target.wav", _wav_bytes())
        noise_a = _uploaded(client, "vent.wav", _wav_bytes())
        noise_b = _uploaded(client, "traffic.wav", _wav_bytes())
        ids = [noise_a["recording_id"], noise_b["recording_id"]]
        analysis = client.post("/api/v1/noise-filtering/noise-profiles", json={"recording_ids": ids})
        assert analysis.status_code == 200, analysis.text
        assert analysis.json()["recording_ids"] == ids
        assert [source["filename"] for source in analysis.json()["sources"]] == ["vent.wav", "traffic.wav"]
        assert analysis.json()["source_count"] == 2
        assert analysis.json()["combination"] == "maximum"

        result = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": target["recording_id"], "low_hz": 100, "high_hz": 3500,
            "denoise_enabled": True, "noise_recording_ids": ids,
        })
        assert result.status_code == 201, result.text
        settings = result.json()["settings"]
        assert settings["noise_recording_ids"] == ids
        assert settings["noise_source_count"] == 2
        assert [source["filename"] for source in settings["noise_sources"]] == ["vent.wav", "traffic.wav"]
        assert settings["noise_combination"] == "maximum"
        assert settings["noise_recording_id"] is None
        assert settings["noise_filename"] is None

        for payload in ({"recording_ids": []}, {"recording_ids": ids + ids[:1]}, {"recording_ids": [""]}, {"recording_id": ids[0], "recording_ids": ids}):
            invalid = client.post("/api/v1/noise-filtering/noise-profiles", json=payload)
            assert invalid.status_code == 422
        invalid_filter = client.post("/api/v1/noise-filtering/recordings", json={
            "recording_id": target["recording_id"], "low_hz": 100, "high_hz": 3500,
            "denoise_enabled": True, "noise_recording_ids": [],
        })
        assert invalid_filter.status_code == 422
