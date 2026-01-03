# Ghost → Campaign Monitor Integration

## Project Overview

Webhook-based integration that synchronizes Ghost membership data to Campaign Monitor in near real-time. Enables lifecycle email automation in Campaign Monitor based on Ghost subscription status, tier changes, and engagement timing.

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Set environment variables (see .env.example)
cp .env.example .env

# Run the webhook server
uvicorn src.main:app --reload

# Run the queue worker (separate terminal)
rq worker ghost-cm-sync
```

## Architecture

```
Ghost Webhooks → FastAPI Handler → Redis Queue (RQ) → Campaign Monitor API
```

- **Webhook Handler**: Receives Ghost events, validates signatures, queues for processing
- **Queue Worker**: Processes events asynchronously with retry logic
- **Campaign Monitor Client**: Handles API calls with connection pooling and backoff

## Key Files

- `src/main.py` - FastAPI application and webhook endpoints
- `src/worker.py` - RQ worker for async event processing
- `src/campaign_monitor.py` - Campaign Monitor API client
- `src/models.py` - Pydantic models for Ghost/CM data
- `src/config.py` - Configuration management
- `scripts/full_sync.py` - Full member sync for recovery
- `scripts/replay_dlq.py` - Dead letter queue replay

## Environment Variables

```
GHOST_WEBHOOK_SECRET     # Shared secret for webhook signature validation
CM_API_KEY               # Campaign Monitor API key
CM_LIST_ID               # Target Campaign Monitor list ID
REDIS_URL                # Redis connection URL (default: redis://localhost:6379)
LOG_LEVEL                # Logging level (default: info)
PORT                     # Server port (default: 3000)
```

## Common Commands

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=src

# Type checking
mypy src

# Linting
ruff check src

# Format code
ruff format src

# Full sync (dry run)
python scripts/full_sync.py --dry-run

# Full sync (execute)
python scripts/full_sync.py

# Replay dead letter queue
python scripts/replay_dlq.py --from 2026-01-01 --to 2026-01-02
```

## Data Flow

1. Ghost fires webhook on member.added/updated/deleted
2. Handler validates HMAC signature
3. Event queued to Redis for async processing
4. Worker fetches current subscriber from Campaign Monitor
5. Detects status changes, updates ghost_previous_status and ghost_status_changed_at
6. Upserts subscriber to Campaign Monitor with name, email, and all custom fields

## Fields Synced to Campaign Monitor

Standard fields (built-in CM fields):
- `EmailAddress` - Member email address
- `Name` - Member name from Ghost

Custom fields (configured in CM list):
- `ghost_status` - Member tier: free, paid, comped
- `ghost_signup_date` - Original Ghost signup date
- `ghost_last_updated` - Last Ghost modification timestamp
- `ghost_status_changed_at` - When status last changed
- `ghost_previous_status` - Previous status value
- `ghost_labels` - Comma-separated Ghost labels
- `ghost_email_enabled` - Newsletter opt-in status

## Error Handling

- Exponential backoff: 1s, 2s, 4s, 8s, 16s
- Max 5 retries over 24 hours
- Failed events go to dead letter queue
- Circuit breaker after 10 consecutive failures

## API Endpoints

- `POST /webhook/ghost` - Ghost webhook receiver
- `GET /health` - Health check endpoint
- `GET /metrics` - Prometheus metrics (if enabled)


# DEPLOY instructions
- found in DEPLOY.md
