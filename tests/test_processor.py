"""Tests for event processor."""

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Set environment before importing
os.environ["GHOST_WEBHOOK_SECRET"] = "test-secret-key"
os.environ["CM_API_KEY"] = "test-cm-api-key"
os.environ["CM_LIST_ID"] = "test-list-id"

from src.processor import detect_status_change, process_event


class TestDetectStatusChange:
    """Tests for status change detection."""

    def test_no_existing_subscriber(self) -> None:
        """Test detection when subscriber doesn't exist."""
        changed, previous = detect_status_change("paid", None)

        assert changed is False
        assert previous is None

    def test_no_status_change(self) -> None:
        """Test detection when status hasn't changed."""
        existing = {
            "CustomFields": [
                {"Key": "ghost_status", "Value": "paid"},
            ]
        }

        changed, previous = detect_status_change("paid", existing)

        assert changed is False
        assert previous == "paid"

    def test_status_changed(self) -> None:
        """Test detection when status has changed."""
        existing = {
            "CustomFields": [
                {"Key": "ghost_status", "Value": "free"},
            ]
        }

        changed, previous = detect_status_change("paid", existing)

        assert changed is True
        assert previous == "free"

    def test_no_ghost_status_field(self) -> None:
        """Test detection when ghost_status field doesn't exist."""
        existing = {
            "CustomFields": [
                {"Key": "other_field", "Value": "value"},
            ]
        }

        changed, previous = detect_status_change("paid", existing)

        assert changed is False
        assert previous is None


class TestProcessEvent:
    """Tests for event processing."""

    @patch("src.processor.get_cm_client")
    def test_process_member_added(
        self, mock_get_client: MagicMock, sample_ghost_payload: dict[str, Any]
    ) -> None:
        """Test processing member.added event."""
        mock_client = MagicMock()
        mock_client.get_subscriber.return_value = None
        mock_client.add_or_update_subscriber.return_value = {"success": True}
        mock_get_client.return_value = mock_client

        result = process_event("member.added", sample_ghost_payload)

        assert result.success is True
        assert result.event_type == "member.added"
        assert result.email == "test@example.com"
        mock_client.add_or_update_subscriber.assert_called_once()

    @patch("src.processor.get_cm_client")
    def test_process_member_added_includes_name(
        self, mock_get_client: MagicMock, sample_ghost_payload: dict[str, Any]
    ) -> None:
        """Test that member.added event passes name to Campaign Monitor."""
        mock_client = MagicMock()
        mock_client.get_subscriber.return_value = None
        mock_client.add_or_update_subscriber.return_value = {"success": True}
        mock_get_client.return_value = mock_client

        result = process_event("member.added", sample_ghost_payload)

        assert result.success is True
        # Verify the member passed to add_or_update_subscriber has the name
        call_args = mock_client.add_or_update_subscriber.call_args
        member = call_args.kwargs.get("member") or call_args.args[0]
        assert member.name == "Test User"

    @patch("src.processor.get_cm_client")
    def test_process_member_updated_with_status_change(
        self, mock_get_client: MagicMock, sample_ghost_update_payload: dict[str, Any]
    ) -> None:
        """Test processing member.updated with status change."""
        mock_client = MagicMock()
        mock_client.get_subscriber.return_value = {
            "CustomFields": [{"Key": "ghost_status", "Value": "free"}]
        }
        mock_client.add_or_update_subscriber.return_value = {"success": True}
        mock_get_client.return_value = mock_client

        result = process_event("member.updated", sample_ghost_update_payload)

        assert result.success is True
        assert result.status_changed is True
        assert result.previous_status == "free"
        assert result.new_status == "paid"

    @patch("src.processor.get_cm_client")
    def test_process_member_deleted(
        self, mock_get_client: MagicMock, sample_ghost_payload: dict[str, Any]
    ) -> None:
        """Test processing member.deleted event."""
        mock_client = MagicMock()
        mock_client.unsubscribe.return_value = {"success": True}
        mock_get_client.return_value = mock_client

        result = process_event("member.deleted", sample_ghost_payload)

        assert result.success is True
        assert result.event_type == "member.deleted"
        mock_client.unsubscribe.assert_called_once_with("test@example.com")

    def test_process_unknown_event_type(self, sample_ghost_payload: dict[str, Any]) -> None:
        """Test processing unknown event type."""
        result = process_event("member.unknown", sample_ghost_payload)

        assert result.success is False
        assert "Unknown event type" in result.message
