"""Redis queue management using RQ."""

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import redis
from rq import Queue
from rq.job import Job

from src.config import get_settings
from src.logging_config import get_logger, hash_email
from src.models import QueuedEvent

logger = get_logger(__name__)


def get_redis_connection() -> redis.Redis:
    """Get Redis connection."""
    settings = get_settings()
    return redis.from_url(settings.redis_url)


def get_queue() -> Queue:
    """Get the RQ queue instance."""
    settings = get_settings()
    conn = get_redis_connection()
    return Queue(settings.queue_name, connection=conn)


def get_failed_queue() -> Queue:
    """Get the dead letter queue for failed jobs."""
    conn = get_redis_connection()
    return Queue("ghost-cm-sync-dlq", connection=conn)


def enqueue_event(event_type: str, payload: dict[str, Any]) -> str:
    """
    Enqueue a Ghost webhook event for async processing.

    Args:
        event_type: Type of event (member.added, member.updated, member.deleted)
        payload: Ghost webhook payload

    Returns:
        Job ID
    """
    settings = get_settings()
    queue = get_queue()

    event = QueuedEvent(
        event_id=str(uuid4()),
        event_type=event_type,
        payload=payload,
        received_at=datetime.now(timezone.utc),
    )

    # Extract email for logging
    email = payload.get("member", {}).get("current", {}).get("email", "unknown")

    job = queue.enqueue(
        "src.worker.process_queued_event",
        event.model_dump(mode="json"),
        job_id=event.event_id,
        retry=settings.max_retries,
    )

    logger.info(
        "event_enqueued",
        event_id=event.event_id,
        event_type=event_type,
        email_hash=hash_email(email),
    )

    return job.id


def move_to_dlq(job: Job, reason: str) -> None:
    """
    Move a failed job to the dead letter queue.

    Args:
        job: Failed RQ job
        reason: Reason for failure
    """
    dlq = get_failed_queue()

    # Store job data in DLQ with failure metadata
    dlq_data = {
        "original_job_id": job.id,
        "args": job.args,
        "kwargs": job.kwargs,
        "failure_reason": reason,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "exc_info": str(job.exc_info) if job.exc_info else None,
    }

    dlq.enqueue(
        "src.worker.store_failed_event",
        dlq_data,
        job_id=f"dlq-{job.id}",
    )

    logger.warning(
        "job_moved_to_dlq",
        original_job_id=job.id,
        reason=reason,
    )


def get_dlq_events(from_date: datetime | None = None, to_date: datetime | None = None) -> list[dict]:
    """
    Get events from the dead letter queue.

    Args:
        from_date: Optional start date filter
        to_date: Optional end date filter

    Returns:
        List of failed event data
    """
    conn = get_redis_connection()
    dlq = get_failed_queue()

    events = []
    for job in dlq.get_jobs():
        if job.args:
            event_data = job.args[0]
            failed_at = datetime.fromisoformat(event_data.get("failed_at", ""))

            # Apply date filters
            if from_date and failed_at < from_date:
                continue
            if to_date and failed_at > to_date:
                continue

            events.append(event_data)

    return events
