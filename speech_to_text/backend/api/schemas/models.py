"""HTTP request schemas for model inspection and preparation."""

from typing import Literal

from pydantic import BaseModel, SecretStr, StrictStr


class _ModelRequest(BaseModel):
    class Config:
        extra = "forbid"

    repository: StrictStr
    revision: StrictStr = "main"
    token: SecretStr | None = None


class ModelInspectionRequest(_ModelRequest):
    pass


class ModelPreparationRequest(_ModelRequest):
    model_key: StrictStr
    operation: Literal["download", "convert"] = "convert"
    runtime: Literal["ctranslate2", "openvino-cpu", "openvino-gpu"] | None = None
    precision: Literal["source", "bf16", "int8", "int4", "float32"] | None = None
