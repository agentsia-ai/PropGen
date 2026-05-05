"""PropGen core data models — Pydantic v2 throughout."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from propgen._time import now_utc


class ProposalSource(str, Enum):
    EMAIL = "email"
    WEB_FORM = "web_form"
    MCP = "mcp"
    LEADGEN = "leadgen"
    SCHEDBOT = "schedbot"
    MANUAL = "manual"


class ProposalStatus(str, Enum):
    DRAFTED = "drafted"
    SENT = "sent"
    VIEWED = "viewed"
    SIGNED = "signed"
    DECLINED = "declined"
    EXPIRED = "expired"
    VOIDED = "voided"
    ACCEPTED = "accepted"


class BillingKind(str, Enum):
    FIXED = "fixed"
    HOURLY = "hourly"
    PER_UNIT = "per_unit"


class VersionDraftStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"


class FollowUpChannel(str, Enum):
    EMAIL = "email"
    SMS = "sms"


class FollowUpStatus(str, Enum):
    DRAFTED = "drafted"
    APPROVED = "approved"
    SENT = "sent"
    SKIPPED = "skipped"


class AcceptanceKind(str, Enum):
    SENT = "sent"
    VIEWED = "viewed"
    SIGNED = "signed"
    DECLINED = "declined"
    VOIDED = "voided"
    MANUAL_OVERRIDE = "manual_override"


class ProposalRequestKind(str, Enum):
    """Classifier output — inbound proposal workflow intent."""

    NEW_PROPOSAL = "new_proposal"
    REVISE = "revise"
    QUESTION = "question"
    PRICING_ONLY = "pricing_only"
    UNCERTAIN = "uncertain"

    @classmethod
    def values(cls) -> list[str]:
        return [r.value for r in cls]


class ClientInfo(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""

    @staticmethod
    def normalize_email(raw: str) -> str:
        return raw.strip().lower()


class LineItem(BaseModel):
    name: str = ""
    description: str = ""
    quantity: float = 1.0
    unit: str = "ea"
    unit_price: float = 0.0
    line_total: float = 0.0
    taxable: bool = True
    optional: bool = False
    sort_order: int = 0


class PricingCatalogEntry(BaseModel):
    slug: str
    name: str
    description: str = ""
    unit_price: float = 0.0
    unit: str = "ea"
    billing_kind: BillingKind = BillingKind.FIXED
    taxable: bool = True


class ProposalClassification(BaseModel):
    kind: ProposalRequestKind = ProposalRequestKind.UNCERTAIN
    confidence: float = 0.0
    reasoning: str = ""
    classified_at: datetime = Field(default_factory=now_utc)


class Proposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source: ProposalSource = ProposalSource.MANUAL
    status: ProposalStatus = ProposalStatus.DRAFTED

    client: ClientInfo = ClientInfo()
    lead_id: Optional[str] = None
    appointment_id: Optional[str] = None

    subject: str = "Proposal"
    currency: str = "USD"
    subtotal: float = 0.0
    tax_amount: float = 0.0
    discount_amount: float = 0.0
    total: float = 0.0

    expires_at: Optional[datetime] = None
    docusign_envelope_id: Optional[str] = None
    current_version_id: Optional[str] = None

    cover_email_subject: str = ""
    cover_email_body: str = ""

    send_approval_token: str = Field(default_factory=lambda: str(uuid4()))
    send_approved_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    accepted_at: Optional[datetime] = None
    signed_at: Optional[datetime] = None

    raw_data: dict[str, Any] = {}

    def touch(self) -> None:
        self.updated_at = now_utc()


class ProposalVersion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    proposal_id: str = ""
    version_number: int = 1
    narrative_md: str = ""
    line_items: list[LineItem] = Field(default_factory=list)
    pdf_path: str = ""
    draft_status: VersionDraftStatus = VersionDraftStatus.DRAFT
    drafted_by: str = "ai"
    created_at: datetime = Field(default_factory=now_utc)


class FollowUpRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    proposal_id: str = ""
    channel: FollowUpChannel = FollowUpChannel.EMAIL
    scheduled_for: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    draft_subject: str = ""
    draft_body: str = ""
    status: FollowUpStatus = FollowUpStatus.DRAFTED
    approval_token: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=now_utc)


class AcceptanceEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    proposal_id: str = ""
    kind: AcceptanceKind = AcceptanceKind.SENT
    occurred_at: datetime = Field(default_factory=now_utc)
    payload_json: str = "{}"


class RawProposalRequest(BaseModel):
    provider: str
    provider_event_id: Optional[str] = None
    event_kind: str = "proposal.requested"

    client_name: str = ""
    client_email: str = ""
    client_phone: str = ""
    subject_hint: str = ""
    scope_notes: str = ""
    lead_id: Optional[str] = None
    appointment_id: Optional[str] = None

    raw_payload: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AcceptanceKind",
    "AcceptanceEvent",
    "BillingKind",
    "ClientInfo",
    "FollowUpChannel",
    "FollowUpRecord",
    "FollowUpStatus",
    "LineItem",
    "PricingCatalogEntry",
    "Proposal",
    "ProposalClassification",
    "ProposalRequestKind",
    "ProposalSource",
    "ProposalStatus",
    "ProposalVersion",
    "RawProposalRequest",
    "VersionDraftStatus",
]
