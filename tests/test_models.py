"""Tests for Pydantic models."""

import os
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

# Set environment before importing
os.environ["GHOST_WEBHOOK_SECRET"] = "test-secret-key"
os.environ["CM_API_KEY"] = "test-cm-api-key"
os.environ["CM_LIST_ID"] = "test-list-id"

from src.models import (
    CMCustomField,
    CMSubscriberPayload,
    GhostLabel,
    GhostMemberData,
    GhostMemberPayload,
    GhostWebhookPayload,
    QueuedEvent,
    SyncResult,
)


class TestGhostMemberData:
    """Tests for GhostMemberData model."""

    def test_valid_member(self) -> None:
        """Test parsing valid member data."""
        member = GhostMemberData(
            id="abc123",
            email="test@example.com",
            name="Test User",
            status="paid",
            subscribed=True,
            created_at=datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, 14, 22, 0, tzinfo=timezone.utc),
            labels=[GhostLabel(name="VIP", slug="vip")],
        )

        assert member.email == "test@example.com"
        assert member.status == "paid"
        assert len(member.labels) == 1

    def test_minimal_member(self) -> None:
        """Test parsing member with minimal fields."""
        member = GhostMemberData(
            id="abc123",
            email="test@example.com",
            status="free",
            created_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
            updated_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
        )

        assert member.name is None
        assert member.subscribed is True
        assert member.labels == []

    def test_invalid_email(self) -> None:
        """Test that invalid email raises validation error."""
        with pytest.raises(ValidationError):
            GhostMemberData(
                id="abc123",
                email="not-an-email",
                status="free",
                created_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
                updated_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
            )


class TestGhostWebhookPayload:
    """Tests for GhostWebhookPayload model."""

    def test_valid_payload(self, sample_ghost_payload: dict) -> None:
        """Test parsing valid webhook payload."""
        payload = GhostWebhookPayload.model_validate(sample_ghost_payload)

        assert payload.member.current.email == "test@example.com"
        assert payload.member.current.status == "free"

    def test_payload_with_previous(self, sample_ghost_update_payload: dict) -> None:
        """Test parsing payload with previous data."""
        payload = GhostWebhookPayload.model_validate(sample_ghost_update_payload)

        assert payload.member.current.status == "paid"
        assert payload.member.previous is not None
        assert payload.member.previous.status == "free"


class TestCMSubscriberPayload:
    """Tests for Campaign Monitor subscriber payload."""

    def test_subscriber_payload(self) -> None:
        """Test creating subscriber payload."""
        payload = CMSubscriberPayload(
            EmailAddress="test@example.com",
            Name="Test User",
            CustomFields=[
                CMCustomField(Key="ghost_status", Value="paid"),
                CMCustomField(Key="ghost_labels", Value="VIP"),
            ],
        )

        assert payload.EmailAddress == "test@example.com"
        assert len(payload.CustomFields) == 2
        assert payload.Resubscribe is True
        assert payload.ConsentToTrack == "Yes"


class TestSyncResult:
    """Tests for SyncResult model."""

    def test_successful_result(self) -> None:
        """Test successful sync result."""
        result = SyncResult(
            success=True,
            email="test@example.com",
            event_type="member.added",
            message="Subscriber added",
            latency_ms=150.5,
            status_changed=True,
            previous_status="free",
            new_status="paid",
        )

        assert result.success is True
        assert result.status_changed is True

    def test_failed_result(self) -> None:
        """Test failed sync result."""
        result = SyncResult(
            success=False,
            email="test@example.com",
            event_type="member.added",
            message="API error",
            latency_ms=500.0,
        )

        assert result.success is False
        assert result.status_changed is False
