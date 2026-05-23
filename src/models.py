"""Pydantic models for Ghost webhooks and Campaign Monitor API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class GhostLabel(BaseModel):
    """Ghost member label."""

    name: str
    slug: str


class GhostMemberData(BaseModel):
    """Ghost member data from webhook payload."""

    id: str | None = None
    email: EmailStr | None = None
    name: str | None = None
    status: str | None = None  # free, paid, comped
    subscribed: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    labels: list[GhostLabel] = Field(default_factory=list)


class GhostMemberPrevious(BaseModel):
    """Previous state of Ghost member (for update/delete events)."""

    id: str | None = None
    email: EmailStr | None = None
    name: str | None = None
    status: str | None = None
    subscribed: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    labels: list[GhostLabel] | None = None


class GhostMemberPayload(BaseModel):
    """Ghost member webhook payload structure."""

    current: GhostMemberData
    previous: GhostMemberPrevious | None = None


class GhostWebhookPayload(BaseModel):
    """Complete Ghost webhook payload."""

    member: GhostMemberPayload


class CMCustomField(BaseModel):
    """Campaign Monitor custom field."""

    Key: str
    Value: str


class CMSubscriberPayload(BaseModel):
    """Campaign Monitor subscriber add/update payload."""

    EmailAddress: EmailStr
    Name: str = ""
    CustomFields: list[CMCustomField] = Field(default_factory=list)
    # Defaults to False — never reactivate someone who unsubscribed or bounced.
    # Callers must check existing State and skip upsert for non-Active subscribers.
    Resubscribe: bool = False
    ConsentToTrack: str = "Yes"


class CMUnsubscribePayload(BaseModel):
    """Campaign Monitor unsubscribe payload."""

    EmailAddress: EmailStr


class QueuedEvent(BaseModel):
    """Event stored in the processing queue."""

    event_id: str
    event_type: str  # member.added, member.updated, member.deleted
    site_id: str  # Which Ghost site this event is from
    payload: dict[str, Any]
    received_at: datetime
    retry_count: int = 0


class SyncResult(BaseModel):
    """Result of a sync operation."""

    success: bool
    email: str
    event_type: str
    message: str
    latency_ms: float
    status_changed: bool = False
    previous_status: str | None = None
    new_status: str | None = None


# Campaign Monitor API Response Models


class CMSubscriberCustomField(BaseModel):
    """Campaign Monitor subscriber custom field in API response."""

    Key: str
    Value: str


class CMSubscriberResponse(BaseModel):
    """Campaign Monitor subscriber GET response."""

    EmailAddress: str
    Name: str = ""
    Date: str | None = None
    State: str | None = None  # Active, Unsubscribed, Bounced, Deleted
    CustomFields: list[CMSubscriberCustomField] = Field(default_factory=list)
    ReadsEmailWith: str | None = None


class CMErrorResponse(BaseModel):
    """Campaign Monitor API error response."""

    Code: int
    Message: str


class CMListStatsResponse(BaseModel):
    """Campaign Monitor list statistics response."""

    TotalActiveSubscribers: int = 0
    TotalUnsubscribes: int = 0
    TotalDeleted: int = 0
    TotalBounces: int = 0


class CMUnsubscribedResult(BaseModel):
    """Single unsubscribed subscriber in list response."""

    EmailAddress: str
    Name: str = ""
    Date: str
    State: str = "Unsubscribed"


class CMUnsubscribedListResponse(BaseModel):
    """Campaign Monitor unsubscribed list response."""

    Results: list[CMUnsubscribedResult] = Field(default_factory=list)
    ResultsOrderedBy: str = "date"
    OrderDirection: str = "desc"
    PageNumber: int = 1
    PageSize: int = 1000
    RecordsOnThisPage: int = 0
    TotalNumberOfRecords: int = 0
    NumberOfPages: int = 1
