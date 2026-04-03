from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ShipmentItem(BaseModel):
    description: str
    quantity: int
    country_of_origin: str
    hs_code: str
    declared_value_usd: float


class ExtractedShipmentData(BaseModel):
    importer: str
    exporter: str
    incoterm: str
    port_of_entry: str
    shipment_value_usd: float
    currency: str
    items: list[ShipmentItem]


class ComplianceIssue(BaseModel):
    severity: str
    title: str
    detail: str
    regulation: str


class DutyEstimate(BaseModel):
    assessable_value_inr: float
    bcd_inr: float
    sws_inr: float
    igst_inr: float
    total_duty_inr: float
    effective_rate_pct: float
    note: str


class ComplianceResult(BaseModel):
    score: int = Field(ge=0, le=100)
    clearance_prediction: str
    risk_level: str
    issues: list[ComplianceIssue]
    suggestions: list[str]
    duty_estimate: DutyEstimate | None = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    result: list["DocumentAnalysisResponse"] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentAnalysisResponse(BaseModel):
    analysis_id: int | None = None
    filename: str
    classification: str
    extracted_text_preview: str
    extracted_data: ExtractedShipmentData
    compliance: ComplianceResult
    assistant_summary: str
    processing_stages: list[dict[str, str]]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_model_output: dict[str, Any] | None = None
    performance: dict[str, Any] | None = None
