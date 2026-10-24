from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Category = Literal[
    "Pothole / damaged road",
    "Garbage / sanitation",
    "Broken streetlight",
    "Open drain",
    "Drainage / waterlogging",
    "Water leakage",
    "Damaged footpath",
    "Damaged public signage",
    "Other civic issue",
]
Priority = Literal["Low", "Normal", "Urgent"]
ValidationStatus = Literal["valid", "irrelevant", "ambiguous"]
IssueStatus = Literal["Open", "Partially resolved", "Resolved", "Uncertain"]
ResolutionStatus = Literal["resolved", "partially_resolved", "still_open", "uncertain"]


class ComplaintDraft(BaseModel):
    """Validated analysis and editable complaint text returned by the AI."""

    is_civic_issue: bool = Field(default=True, description="Whether the evidence describes a public civic issue")
    validation_status: ValidationStatus = Field(default="valid", description="Use ambiguous when evidence is insufficient")
    confidence: float = Field(default=0.65, ge=0, le=1, description="Confidence from 0 to 1")
    category: Category = Field(description="Best matching civic issue category")
    summary: str = Field(description="One sentence describing the reported issue")
    draft_complaint: str = Field(description="Polite, factual complaint ready for citizen review")
    severity: int = Field(default=50, ge=0, le=100, description="Visible severity estimate from 0 to 100")
    public_impact: int = Field(default=50, ge=0, le=100, description="Estimated public impact from 0 to 100")
    safety_risk: int = Field(default=50, ge=0, le=100, description="Visible safety concern estimate from 0 to 100")
    evidence_quality: int = Field(default=50, ge=0, le=100, description="Photo and description evidence quality from 0 to 100")
    suggested_department: str = Field(default="Unverified local authority", description="Unverified routing suggestion")
    needs_clarification: bool = Field(default=False, description="Whether the resident should add details before review")
    reasoning_summary: str = Field(default="", description="Brief explanation grounded in supplied evidence")
    evidence_observations: list[str] = Field(
        description="Visible facts from the photo only; say when an observation is uncertain"
    )
    missing_information: list[str] = Field(
        description="Important details the citizen may want to add"
    )
    follow_up_questions: list[str] = Field(
        description="Short clarifying questions when the report lacks useful details"
    )
    priority: Priority = Field(description="Suggested review priority; never a safety guarantee")

    @field_validator("summary", "draft_complaint")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty")
        return value


class SavedReport(BaseModel):
    id: str
    created_at: str
    updated_at: str
    status: Literal["Draft", "Ready to submit", "Submitted", "In progress", "Resolved"]
    external_reference: str = ""
    citizen_description: str
    location_text: str
    latitude: float | None = None
    longitude: float | None = None
    photo_jpeg: bytes | None = None
    draft: ComplaintDraft
    issue_id: str | None = None


class CivicIssue(BaseModel):
    id: str
    category: Category
    summary: str
    location_text: str = ""
    latitude: float | None = None
    longitude: float | None = None
    status: IssueStatus = "Open"
    first_seen: str
    last_seen: str
    priority_score: float = 0
    severity: int = 50
    public_impact: int = 50
    safety_risk: int = 50
    evidence_confidence: float = 0.5
    evidence_quality: int = 50
    report_count: int = 1
    confirmation_count: int = 0
    before_photo: bytes | None = None
    latest_photo: bytes | None = None
    after_photo: bytes | None = None
    resolution_status: ResolutionStatus | None = None
    resolution_confidence: float | None = None
    resolution_note: str = ""


class ResolutionAssessment(BaseModel):
    status: ResolutionStatus = Field(description="Visual comparison result; use uncertain if images cannot be reliably matched")
    confidence: float = Field(ge=0, le=1, description="Confidence from 0 to 1")
    visible_changes: list[str] = Field(description="Changes visible between before and after evidence")
    reasoning_summary: str = Field(description="Concise comparison grounded only in the two images")
    limitations: list[str] = Field(description="Why the evidence may not prove the civic issue was resolved")


class RouteRecommendation(BaseModel):
    authority: str
    channel: str
    url: str
    note: str
