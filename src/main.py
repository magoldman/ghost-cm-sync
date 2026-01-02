"""FastAPI application for Ghost webhook handling."""

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.logging_config import configure_logging, get_logger, hash_email
from src.queue import enqueue_event, get_queue, get_redis_connection
from src.signature import validate_signature

# Configure logging on startup
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("application_starting")
    yield
    logger.info("application_shutting_down")


app = FastAPI(
    title="Ghost → Campaign Monitor Sync",
    description="Webhook-based integration for syncing Ghost members to Campaign Monitor",
    version="0.1.0",
    lifespan=lifespan,
)


# Request metrics
_metrics = {
    "events_received": 0,
    "events_processed": 0,
    "events_failed": 0,
    "start_time": datetime.now(timezone.utc),
}


@app.post("/webhook/ghost")
async def handle_ghost_webhook(
    request: Request,
    x_ghost_signature: str | None = Header(None, alias="X-Ghost-Signature"),
) -> JSONResponse:
    """
    Handle incoming Ghost webhook events.

    Validates signature, acknowledges receipt, and queues for async processing.

    Args:
        request: FastAPI request object
        x_ghost_signature: Ghost webhook signature header

    Returns:
        JSON response acknowledging receipt
    """
    start_time = time.time()
    _metrics["events_received"] += 1

    # Read raw body for signature validation
    body = await request.body()

    # Validate webhook signature
    if not validate_signature(body, x_ghost_signature):
        logger.warning("webhook_signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # Parse payload
    try:
        payload: dict[str, Any] = await request.json()
    except Exception as e:
        logger.error("webhook_payload_parse_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    # Determine event type from Ghost webhook headers or payload structure
    # Ghost sends the event type in the URL path or we detect it from payload
    event_type = _detect_event_type(request, payload)

    if event_type is None:
        logger.warning("webhook_unknown_event_type", payload_keys=list(payload.keys()))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown event type",
        )

    # Extract email for logging
    email = payload.get("member", {}).get("current", {}).get("email", "unknown")

    # Queue event for async processing
    try:
        job_id = enqueue_event(event_type, payload)
        _metrics["events_processed"] += 1

        latency_ms = (time.time() - start_time) * 1000

        logger.info(
            "webhook_received",
            event_type=event_type,
            email_hash=hash_email(email),
            job_id=job_id,
            latency_ms=latency_ms,
        )

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "accepted",
                "job_id": job_id,
                "event_type": event_type,
            },
        )

    except Exception as e:
        _metrics["events_failed"] += 1
        logger.error(
            "webhook_queue_error",
            event_type=event_type,
            email_hash=hash_email(email),
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue event",
        )


def _detect_event_type(request: Request, payload: dict[str, Any]) -> str | None:
    """
    Detect the Ghost event type from request or payload.

    Ghost can send event type in different ways:
    1. As a query parameter
    2. In the payload structure
    3. Based on the presence of 'previous' data

    Args:
        request: FastAPI request
        payload: Parsed webhook payload

    Returns:
        Event type string or None if unknown
    """
    # Check query parameters first
    event_type = request.query_params.get("event")
    if event_type in ("member.added", "member.updated", "member.deleted"):
        return event_type

    # Check X-Ghost-Event header (if Ghost sends it)
    event_header = request.headers.get("X-Ghost-Event")
    if event_header:
        return event_header

    # Infer from payload structure
    if "member" not in payload:
        return None

    member_data = payload.get("member", {})

    # If there's previous data, it's an update
    if member_data.get("previous"):
        return "member.updated"

    # Check if current has data - could be added
    if member_data.get("current"):
        # Default to added for new members
        return "member.added"

    return None


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """
    Health check endpoint.

    Verifies Redis connectivity and returns service status.

    Returns:
        Health status with component checks
    """
    checks = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    # Check Redis connection
    try:
        conn = get_redis_connection()
        conn.ping()
        checks["checks"]["redis"] = "healthy"
    except Exception as e:
        checks["checks"]["redis"] = f"unhealthy: {e}"
        checks["status"] = "degraded"

    # Check queue
    try:
        queue = get_queue()
        checks["checks"]["queue"] = {
            "status": "healthy",
            "depth": len(queue),
        }
    except Exception as e:
        checks["checks"]["queue"] = f"unhealthy: {e}"
        checks["status"] = "degraded"

    status_code = status.HTTP_200_OK if checks["status"] == "healthy" else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(status_code=status_code, content=checks)


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    """
    Basic metrics endpoint.

    Returns:
        Service metrics including event counts and uptime
    """
    uptime = datetime.now(timezone.utc) - _metrics["start_time"]

    queue_depth = 0
    try:
        queue = get_queue()
        queue_depth = len(queue)
    except Exception:
        pass

    return {
        "events_received": _metrics["events_received"],
        "events_processed": _metrics["events_processed"],
        "events_failed": _metrics["events_failed"],
        "queue_depth": queue_depth,
        "uptime_seconds": uptime.total_seconds(),
        "success_rate": (
            _metrics["events_processed"] / _metrics["events_received"] * 100
            if _metrics["events_received"] > 0
            else 100.0
        ),
    }


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
