"""RQ worker for processing Ghost webhook events."""

import time
from typing import Any

from rq import get_current_job

from src.config import get_settings
from src.logging_config import configure_logging, get_logger, hash_email
from src.models import QueuedEvent
from src.processor import process_event
from src.queue import move_to_dlq

# Configure logging for worker
configure_logging()
logger = get_logger(__name__)


def process_queued_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """
    Process a queued Ghost webhook event.

    This function is called by RQ workers.

    Args:
        event_data: Serialized QueuedEvent data

    Returns:
        Processing result
    """
    settings = get_settings()
    event = QueuedEvent.model_validate(event_data)

    email = event.payload.get("member", {}).get("current", {}).get("email", "unknown")

    logger.info(
        "processing_event",
        site_id=event.site_id,
        event_id=event.event_id,
        event_type=event.event_type,
        email_hash=hash_email(email),
        retry_count=event.retry_count,
    )

    try:
        result = process_event(event.event_type, event.payload, event.site_id)

        if result.success:
            logger.info(
                "event_processed_successfully",
                site_id=event.site_id,
                event_id=event.event_id,
                event_type=event.event_type,
                email_hash=hash_email(email),
                latency_ms=result.latency_ms,
                status_changed=result.status_changed,
            )
        else:
            # Check if we should retry
            if event.retry_count < settings.max_retries:
                # Calculate backoff delay
                delay = settings.retry_delays[min(event.retry_count, len(settings.retry_delays) - 1)]
                logger.warning(
                    "event_processing_failed_will_retry",
                    site_id=event.site_id,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    email_hash=hash_email(email),
                    retry_count=event.retry_count,
                    next_retry_delay=delay,
                    error=result.message,
                )
                # RQ will handle the retry based on job configuration
                raise Exception(f"Processing failed: {result.message}")
            else:
                # Max retries exceeded, move to DLQ
                job = get_current_job()
                if job:
                    move_to_dlq(job, result.message)
                logger.error(
                    "event_processing_failed_max_retries",
                    site_id=event.site_id,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    email_hash=hash_email(email),
                    error=result.message,
                )

        return result.model_dump()

    except Exception as e:
        logger.error(
            "event_processing_exception",
            site_id=event.site_id,
            event_id=event.event_id,
            event_type=event.event_type,
            email_hash=hash_email(email),
            error=str(e),
        )
        raise


def store_failed_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """
    Store a failed event in the DLQ.

    This is a placeholder function for DLQ storage.
    The event data is already stored as job args.

    Args:
        event_data: Failed event data with metadata

    Returns:
        Confirmation
    """
    logger.info(
        "failed_event_stored",
        original_job_id=event_data.get("original_job_id"),
    )
    return {"stored": True, "job_id": event_data.get("original_job_id")}
