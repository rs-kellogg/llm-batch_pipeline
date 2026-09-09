from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CanonicalRecord(BaseModel):
    record_id: str
    source_row: int
    sent: dict[str, Any]
    preserved: dict[str, Any] = Field(default_factory=dict)
    truncated_fields: dict[str, dict[str, int]] = Field(default_factory=dict)


class CanonicalRequest(BaseModel):
    custom_id: str
    record_ids: list[str]
    system_prompt: str
    user_prompt: str


class BatchHandle(BaseModel):
    provider: str
    batch_id: str
    input_file_id: str | None = None
    status: str


class NormalizedResult(BaseModel):
    custom_id: str
    status: Literal["succeeded", "errored", "expired", "cancelled", "unknown"]
    response_text: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error_type: str | None = None
    error_message: str | None = None


class AuditFinding(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    record_ids: list[str] = Field(default_factory=list)
    source_rows: list[int] = Field(default_factory=list)


class CostEstimate(BaseModel):
    provider: str
    model: str
    request_count: int
    estimated_input_tokens: int
    maximum_output_tokens: int
    estimated_usd: float | None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    pricing_as_of: str | None = None
    method: str = "characters_divided_by_four"
