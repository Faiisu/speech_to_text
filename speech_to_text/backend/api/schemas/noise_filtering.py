"""HTTP request schema for saved-recording filtering."""

from pydantic import BaseModel, ConfigDict, Field


class FilterRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording_id: str = Field(min_length=1, max_length=128)
    low_hz: float = Field(gt=0, allow_inf_nan=False)
    high_hz: float = Field(gt=0, allow_inf_nan=False)
    order: int = Field(default=4, ge=1, le=8)
