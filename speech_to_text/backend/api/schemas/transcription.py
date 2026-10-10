"""HTTP request schemas for transcription workflows."""

from typing import Literal

from pydantic import BaseModel, Field, StrictStr, validator


class MicrophoneTranscriptionRequest(BaseModel):
    class Config:
        extra = "forbid"

    keywords: list[StrictStr]
    device: StrictStr | None = None
    execution_mode: Literal["shared", "per_workflow_process"] = "shared"


class MicrophoneProfileDefinition(BaseModel):
    class Config:
        extra = "forbid"

    name: StrictStr
    device: StrictStr | None = None
    execution_mode: Literal["shared", "per_workflow_process"] = "shared"
    keywords: list[StrictStr]
    silence_threshold: float = Field(
        default=0.00, ge=0.0, lt=1.0, allow_inf_nan=False
    )
    model: StrictStr | None = None
    runtime: StrictStr | None = None

    @validator("silence_threshold", pre=True)
    def reject_boolean_silence_threshold(cls, value):
        if isinstance(value, bool):
            raise ValueError("silence_threshold must be a number")
        return value
