"""Event processor for Ghost webhook events."""

import time
from datetime import datetime, timezone
from typing import Any

from src.campaign_monitor import CampaignMonitorError, CircuitBreakerOpen, get_cm_client
from src.config import get_settings
from src.logging_config import get_logger, hash_email
from src.models import GhostMemberData, GhostWebhookPayload, SyncResult

logger = get_logger(__name__)


def detect_status_change(
    current_status: str, cm_subscriber: dict[str, Any] | None
) -> tuple[bool, str | None]:
    """
    Detect if member status has changed.

    Args:
        current_status: Current Ghost member status
        cm_subscriber: Existing Campaign Monitor subscriber data

    Returns:
        Tuple of (status_changed, previous_status)
    """
    if cm_subscriber is None:
        return False, None

    # Extract ghost_status from custom fields
    custom_fields = cm_subscriber.get("CustomFields", [])
    previous_status = None

    for field in custom_fields:
        if field.get("Key") == "ghost_status":
            previous_status = field.get("Value")
            break

    if previous_status is None:
        return False, None

    return previous_status != current_status, previous_status


def process_member_added(payload: GhostWebhookPayload) -> SyncResult:
    """
    Process member.added event.

    Args:
        payload: Validated Ghost webhook payload

    Returns:
        SyncResult with operation details
    """
    start_time = time.time()
    member = payload.member.current
    client = get_cm_client()

    try:
        # Check if subscriber already exists (for resubscription case)
        existing = client.get_subscriber(member.email)
        status_changed, previous_status = detect_status_change(member.status, existing)

        status_changed_at = datetime.now(timezone.utc) if status_changed else None

        client.add_or_update_subscriber(
            member=member,
            previous_status=previous_status if status_changed else None,
            status_changed_at=status_changed_at,
        )

        latency_ms = (time.time() - start_time) * 1000

        return SyncResult(
            success=True,
            email=member.email,
            event_type="member.added",
            message="Subscriber added successfully",
            latency_ms=latency_ms,
            status_changed=status_changed,
            previous_status=previous_status,
            new_status=member.status,
        )

    except (CampaignMonitorError, CircuitBreakerOpen) as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(
            "process_member_added_failed",
            email_hash=hash_email(member.email),
            error=str(e),
        )
        return SyncResult(
            success=False,
            email=member.email,
            event_type="member.added",
            message=str(e),
            latency_ms=latency_ms,
        )


def process_member_updated(payload: GhostWebhookPayload) -> SyncResult:
    """
    Process member.updated event.

    Args:
        payload: Validated Ghost webhook payload

    Returns:
        SyncResult with operation details
    """
    start_time = time.time()
    member = payload.member.current
    client = get_cm_client()

    try:
        # Get current subscriber to detect status changes
        existing = client.get_subscriber(member.email)
        status_changed, previous_status = detect_status_change(member.status, existing)

        status_changed_at = datetime.now(timezone.utc) if status_changed else None

        # If status changed, log it
        if status_changed:
            logger.info(
                "status_change_detected",
                email_hash=hash_email(member.email),
                previous_status=previous_status,
                new_status=member.status,
            )

        client.add_or_update_subscriber(
            member=member,
            previous_status=previous_status if status_changed else None,
            status_changed_at=status_changed_at,
        )

        latency_ms = (time.time() - start_time) * 1000

        return SyncResult(
            success=True,
            email=member.email,
            event_type="member.updated",
            message="Subscriber updated successfully",
            latency_ms=latency_ms,
            status_changed=status_changed,
            previous_status=previous_status,
            new_status=member.status,
        )

    except (CampaignMonitorError, CircuitBreakerOpen) as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(
            "process_member_updated_failed",
            email_hash=hash_email(member.email),
            error=str(e),
        )
        return SyncResult(
            success=False,
            email=member.email,
            event_type="member.updated",
            message=str(e),
            latency_ms=latency_ms,
        )


def process_member_deleted(payload: GhostWebhookPayload) -> SyncResult:
    """
    Process member.deleted event.

    Never hard-deletes - only unsubscribes to preserve history.

    Args:
        payload: Validated Ghost webhook payload

    Returns:
        SyncResult with operation details
    """
    start_time = time.time()

    # For deleted events, Ghost sends data in 'previous', not 'current'
    email = (
        payload.member.current.email
        or (payload.member.previous.email if payload.member.previous else None)
    )

    if not email:
        return SyncResult(
            success=False,
            email="unknown",
            event_type="member.deleted",
            message="No email found in payload",
            latency_ms=(time.time() - start_time) * 1000,
        )

    client = get_cm_client()

    try:
        client.unsubscribe(email)

        latency_ms = (time.time() - start_time) * 1000

        return SyncResult(
            success=True,
            email=email,
            event_type="member.deleted",
            message="Subscriber unsubscribed (soft delete)",
            latency_ms=latency_ms,
        )

    except (CampaignMonitorError, CircuitBreakerOpen) as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(
            "process_member_deleted_failed",
            email_hash=hash_email(email),
            error=str(e),
        )
        return SyncResult(
            success=False,
            email=email,
            event_type="member.deleted",
            message=str(e),
            latency_ms=latency_ms,
        )


def process_event(event_type: str, payload_dict: dict[str, Any]) -> SyncResult:
    """
    Process a Ghost webhook event.

    Args:
        event_type: Type of event (member.added, member.updated, member.deleted)
        payload_dict: Raw payload dictionary

    Returns:
        SyncResult with operation details
    """
    # Validate payload
    payload = GhostWebhookPayload.model_validate(payload_dict)

    processors = {
        "member.added": process_member_added,
        "member.updated": process_member_updated,
        "member.deleted": process_member_deleted,
    }

    processor = processors.get(event_type)
    if processor is None:
        return SyncResult(
            success=False,
            email=payload.member.current.email,
            event_type=event_type,
            message=f"Unknown event type: {event_type}",
            latency_ms=0,
        )

    return processor(payload)
