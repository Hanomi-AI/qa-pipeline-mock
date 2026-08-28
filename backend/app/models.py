from typing import Any, Literal
from pydantic import BaseModel, Field

InputType = Literal["raw", "compressed", "legacy", "structured"]


class SubmitJob(BaseModel):
    input_ref: str = Field(min_length=1, max_length=256)
    input_type: InputType


class JobAccepted(BaseModel):
    job_id: str
    status: str


class Job(BaseModel):
    job_id: str
    input_ref: str
    input_type: str
    status: str
    stage: str | None = None
    attempts: int
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class JobPage(BaseModel):
    jobs: list[Job]
    next_cursor: str | None = None


class WebhookIn(BaseModel):
    url: str = Field(min_length=1, max_length=1024)


class Webhook(BaseModel):
    id: str
    url: str
