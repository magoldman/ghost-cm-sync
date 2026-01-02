#!/usr/bin/env python3
"""
Replay dead letter queue events.

Re-processes failed events from the dead letter queue.
Useful for recovery after transient failures are resolved.

Usage:
    python scripts/replay_dlq.py --from 2026-01-01 --to 2026-01-02
    python scripts/replay_dlq.py --all
    python scripts/replay_dlq.py --list  # Show DLQ contents without replaying
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_settings
from src.logging_config import configure_logging, get_logger, hash_email
from src.processor import process_event
from src.queue import get_dlq_events, get_failed_queue, get_redis_connection

configure_logging()
logger = get_logger(__name__)


def parse_date(date_str: str) -> datetime:
    """Parse date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def list_dlq_events(from_date: datetime | None, to_date: datetime | None) -> list[dict]:
    """List events in the DLQ."""
    try:
        events = get_dlq_events(from_date, to_date)
        return events
    except Exception as e:
        logger.error("dlq_list_error", error=str(e))
        return []


def replay_event(event_data: dict) -> dict:
    """
    Replay a single failed event.

    Args:
        event_data: DLQ event data

    Returns:
        Replay result
    """
    original_args = event_data.get("args", [])
    if not original_args:
        return {
            "success": False,
            "error": "No original event data found",
        }

    queued_event = original_args[0]
    event_type = queued_event.get("event_type")
    payload = queued_event.get("payload")

    if not event_type or not payload:
        return {
            "success": False,
            "error": "Invalid event structure",
        }

    try:
        result = process_event(event_type, payload)
        return {
            "success": result.success,
            "email": result.email,
            "event_type": event_type,
            "message": result.message,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


def remove_from_dlq(event_data: dict) -> bool:
    """Remove an event from the DLQ after successful replay."""
    dlq = get_failed_queue()
    job_id = f"dlq-{event_data.get('original_job_id')}"

    try:
        job = dlq.fetch_job(job_id)
        if job:
            job.delete()
            return True
    except Exception as e:
        logger.warning("dlq_remove_error", job_id=job_id, error=str(e))

    return False


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Replay failed events from dead letter queue")
    parser.add_argument(
        "--from",
        dest="from_date",
        type=str,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=str,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Replay all events in DLQ",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List DLQ contents without replaying",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep events in DLQ after successful replay",
    )
    args = parser.parse_args()

    # Parse dates
    from_date = parse_date(args.from_date) if args.from_date else None
    to_date = parse_date(args.to_date) if args.to_date else None

    if not args.all and not from_date and not args.list:
        print("Error: Must specify --from/--to dates, --all, or --list")
        return 1

    print("Ghost → Campaign Monitor DLQ Replay")
    print("=" * 40)

    # Get events from DLQ
    print("Fetching events from dead letter queue...")
    events = list_dlq_events(from_date, to_date)

    if not events:
        print("No events found in DLQ matching criteria")
        return 0

    print(f"Found {len(events)} events in DLQ")
    print()

    # List mode - just show events
    if args.list:
        print("DLQ Contents:")
        print("-" * 60)
        for event in events:
            original_args = event.get("args", [{}])
            queued = original_args[0] if original_args else {}
            email = queued.get("payload", {}).get("member", {}).get("current", {}).get("email", "unknown")
            event_type = queued.get("event_type", "unknown")
            failed_at = event.get("failed_at", "unknown")
            reason = event.get("failure_reason", "unknown")

            print(f"  {hash_email(email)[:12]}... | {event_type:15} | {failed_at} | {reason[:30]}")
        print()
        return 0

    # Replay mode
    results = {
        "replayed": 0,
        "failed": 0,
        "removed": 0,
    }

    for i, event in enumerate(events, 1):
        original_args = event.get("args", [{}])
        queued = original_args[0] if original_args else {}
        email = queued.get("payload", {}).get("member", {}).get("current", {}).get("email", "unknown")

        print(f"  [{i}/{len(events)}] Replaying {hash_email(email)[:12]}...", end=" ")

        result = replay_event(event)

        if result.get("success"):
            print("OK")
            results["replayed"] += 1

            # Remove from DLQ unless --keep specified
            if not args.keep:
                if remove_from_dlq(event):
                    results["removed"] += 1
        else:
            print(f"FAILED: {result.get('error', 'Unknown error')}")
            results["failed"] += 1

    # Summary
    print()
    print("=" * 40)
    print("Summary:")
    print(f"  Total events: {len(events)}")
    print(f"  Successfully replayed: {results['replayed']}")
    print(f"  Failed: {results['failed']}")
    print(f"  Removed from DLQ: {results['removed']}")

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
