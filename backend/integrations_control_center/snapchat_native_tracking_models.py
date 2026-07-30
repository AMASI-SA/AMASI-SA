"""Secret-safe API models for Snapchat tracking diagnostics."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SnapchatTrackingDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    provider: Literal["snapchat_ads"]
    status: Literal["complete", "partial", "failed"]
    date_from: str | None = None
    date_to: str | None = None
    accounts_attempted: int = Field(ge=0)
    accounts_complete: int = Field(ge=0)
    pixels_found: int = Field(ge=0)
    pixels_complete: int = Field(ge=0)
    domains_observed: int = Field(ge=0)
    diagnostics_saved: int = Field(ge=0)
    recommendations_count: int = Field(ge=0)
    errors_count: int = Field(ge=0)
    source_only: Literal[True]
    provider_write_reached: Literal[False]
    event_write_reached: Literal[False]
    accounting_write_reached: Literal[False]
    qoyod_write_reached: Literal[False]


__all__ = ["SnapchatTrackingDiagnosticsResponse"]
