# Ghost → Campaign Monitor Integration

## Project Overview

Webhook-based integration that synchronizes Ghost membership data to Campaign Monitor in near real-time. Supports **multiple Ghost sites** syncing to separate Campaign Monitor lists. Enables lifecycle email automation based on Ghost subscription status, tier changes, and engagement timing.

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
                      ↓
              Site-specific routing
                      ↓
              Per-site CM lists
```

- **Webhook Handler**: Receives Ghost events at `/webhook/ghost/{site_id}`, validates signatures per-site
- **Queue Worker**: Processes events asynchronously with site context
- **Campaign Monitor Client**: Per-site client cache, handles API calls with connection pooling

## Key Files

- `src/main.py` - FastAPI application and webhook endpoints
- `src/worker.py` - RQ worker for async event processing
- `src/campaign_monitor.py` - Campaign Monitor API client (per-site caching)
- `src/config.py` - Multi-site configuration management
- `src/models.py` - Pydantic models for Ghost/CM data
- `scripts/full_sync.py` - Full member sync for recovery (per-site)
- `scripts/replay_dlq.py` - Dead letter queue replay

## Environment Variables

```bash
# Shared configuration
CM_API_KEY=your-campaign-monitor-api-key
REDIS_URL=redis://localhost:6379
LOG_LEVEL=info
PORT=3000

# Per-site configuration (repeat for SITE2, SITE3, etc.)
SITE1_NAME=mainblog
SITE1_GHOST_WEBHOOK_SECRET=webhook-secret-for-site1
SITE1_GHOST_URL=https://blog1.example.com
SITE1_GHOST_ADMIN_API_KEY=admin-api-key-for-site1
SITE1_CM_LIST_ID=cm-list-id-for-site1
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

# List configured sites
python scripts/full_sync.py --list-sites

# Full sync (dry run for specific site)
python scripts/full_sync.py --site mainblog --dry-run

# Full sync (execute for specific site)
python scripts/full_sync.py --site mainblog

# Full sync with verbose output
python scripts/full_sync.py --site mainblog --verbose

# Replay dead letter queue
python scripts/replay_dlq.py --from 2026-01-01 --to 2026-01-02
```

## Data Flow

1. Ghost fires webhook to `/webhook/ghost/{site_id}` on member events
2. Handler validates HMAC signature using site-specific secret
3. Event queued to Redis with site_id for async processing
4. Worker fetches current subscriber from site-specific CM list
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
- Circuit breaker after 10 consecutive failures (per-site)

## API Endpoints

- `POST /webhook/ghost/{site_id}` - Ghost webhook receiver (site-specific)
- `GET /health` - Health check endpoint (shows configured sites)
- `GET /metrics` - Prometheus metrics (if enabled)

# DEPLOY instructions
- found in deploy/DEPLOY.md
