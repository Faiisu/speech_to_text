"""HTTP request schemas for the file-replay stress workflow."""

from pydantic import BaseModel, StrictStr


class StressTestRequest(BaseModel):
    class Config:
        extra = "forbid"

    model: StrictStr | None = None
    runtime: StrictStr | None = None
    precision: StrictStr | None = None
