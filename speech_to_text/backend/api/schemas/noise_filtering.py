"""HTTP request schema for saved-recording filtering."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

RecordingId = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class FilterRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording_id: str = Field(min_length=1, max_length=128)
    low_hz: float = Field(gt=0, allow_inf_nan=False)
    high_hz: float = Field(gt=0, allow_inf_nan=False)
    order: int = Field(default=4, ge=1, le=8)
    denoise_enabled: StrictBool = False
    noise_recording_id: str | None = Field(default=None, min_length=1, max_length=128)
    noise_recording_ids: list[RecordingId] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def validate_noise_ids(self):
        if self.noise_recording_id is not None and self.noise_recording_ids is not None:
            raise ValueError("Specify noise_recording_id or noise_recording_ids, not both")
        ids = self.noise_recording_ids
        if ids is not None and (not ids or any(not value.strip() for value in ids) or len(set(ids)) != len(ids)):
            raise ValueError("noise_recording_ids must contain 1 to 8 unique non-empty IDs")
        return self


class AnalyzeNoiseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording_id: str | None = Field(default=None, min_length=1, max_length=128)
    recording_ids: list[RecordingId] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def validate_recording_ids(self):
        if (self.recording_id is None) == (self.recording_ids is None):
            raise ValueError("Specify exactly one of recording_id or recording_ids")
        if self.recording_ids is not None and (not self.recording_ids or any(not value.strip() for value in self.recording_ids) or len(set(self.recording_ids)) != len(self.recording_ids)):
            raise ValueError("recording_ids must contain 1 to 8 unique non-empty IDs")
        return self
