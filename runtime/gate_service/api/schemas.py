"""Versioned REST request schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


class _StrictRequest(BaseModel):
    """Reject unknown fields and scalar coercion on command boundaries."""

    model_config = ConfigDict(extra="forbid")


class StartInventoryRequest(_StrictRequest):
    reference: StrictStr = Field(min_length=1, max_length=128)
    operation: Literal["INBOUND", "OUTBOUND"]
    expected_epcs: list[StrictStr] = Field(default_factory=list, max_length=20_000)
    antennas: list[StrictBool] = Field(
        default_factory=lambda: [True, True, False, False],
        min_length=4,
        max_length=4,
    )
    session: StrictInt = Field(default=0, ge=0, le=3)
    target: Literal["A", "B"] = "A"

    @model_validator(mode="after")
    def require_enabled_antenna(self) -> "StartInventoryRequest":
        if not any(self.antennas):
            raise ValueError("at least one antenna must be enabled")
        return self


class CancelTransactionRequest(_StrictRequest):
    reason: StrictStr = Field(min_length=3, max_length=500)


class _CalibrationRequest(_StrictRequest):
    """Reject misspelled commissioning fields instead of silently ignoring them."""


class StartCalibrationRequest(_CalibrationRequest):
    notes: StrictStr = Field(default="", max_length=1000)


class CalibrationBackgroundRequest(_CalibrationRequest):
    """Request an empty-gate RF capture; observations always come from USB."""

    duration_seconds: StrictFloat = Field(default=30, ge=5, le=300)


class CalibrationPassRequest(_CalibrationRequest):
    """Label one physical pass without accepting client-supplied evidence."""

    direction: Literal["IN", "OUT"]
    expected_epcs: list[StrictStr] = Field(min_length=1, max_length=20_000)
    timeout_seconds: StrictFloat = Field(default=60, ge=1, le=300)


class EvaluateCalibrationRequest(_CalibrationRequest):
    pass


class AbortCalibrationRequest(_CalibrationRequest):
    reason: StrictStr = Field(min_length=3, max_length=500)


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class CommandResponse(BaseModel):
    command: str
    accepted: bool
    result: dict[str, Any]


class CalibrationMutationResponse(BaseModel):
    calibration_id: str
    status: str
    updated_at: str


class ItemsResponse(BaseModel):
    items: list[dict[str, Any]]


class PageResponse(ItemsResponse):
    limit: int
    offset: int


class TransactionResponse(BaseModel):
    transaction: dict[str, Any]
    reconciliation: dict[str, Any]
