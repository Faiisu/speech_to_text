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
