# Ghost → Campaign Monitor Sync

A webhook-based integration that synchronizes Ghost membership data to Campaign Monitor in near real-time, enabling lifecycle email automation based on subscription status and tier changes.

## Features

- **Multi-site support**: Sync multiple Ghost instances to separate Campaign Monitor lists
- Real-time sync of Ghost member events (added, updated, deleted)
- Syncs member name and email to Campaign Monitor
- Status change detection with historical tracking
- Async processing with Redis-backed queue
- Exponential backoff retry with dead letter queue
- Full sync capability for initial migration and recovery
- Health check and metrics endpoints

## Requirements

- Python 3.11+
- Redis 6.0+
- Campaign Monitor account with API access
- Ghost site with custom integration

## Installation

### 1. Clone and Install

```bash
git clone https://github.com/yourusername/ghost-cm-sync.git
cd ghost-cm-sync
pip install -e ".[dev]"
```

### 2. Install Redis (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Verify Redis is running
redis-cli ping  # Should return PONG
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required environment variables:

**Shared Configuration:**

| Variable | Description |
|----------|-------------|
| `CM_API_KEY` | Campaign Monitor API key |
| `REDIS_URL` | Redis connection URL (default: `redis://localhost:6379`) |
| `LOG_LEVEL` | Logging level (default: `info`) |
| `PORT` | Server port (default: `3000`) |

**Per-Site Configuration** (replace N with 1, 2, 3, etc.):

| Variable | Description |
|----------|-------------|
| `SITEN_NAME` | Site identifier (used in webhook URLs) |
| `SITEN_GHOST_WEBHOOK_SECRET` | Webhook secret for this Ghost site |
| `SITEN_GHOST_URL` | Ghost site URL (for full sync) |
| `SITEN_GHOST_ADMIN_API_KEY` | Ghost Admin API key (for full sync) |
| `SITEN_CM_LIST_ID` | Campaign Monitor list ID for this site |

Example `.env` for two sites:
```env
CM_API_KEY=your-cm-api-key

SITE1_NAME=mainblog
SITE1_GHOST_WEBHOOK_SECRET=secret1
SITE1_CM_LIST_ID=list-id-1

SITE2_NAME=newsletter
SITE2_GHOST_WEBHOOK_SECRET=secret2
SITE2_CM_LIST_ID=list-id-2
```

### 4. Configure Campaign Monitor

Create the following custom fields in your Campaign Monitor list:

| Field Name | Type | Description |
|------------|------|-------------|
| `ghost_status` | Text | Member tier: free, paid, comped |
| `ghost_signup_date` | Date | Original Ghost signup date |
| `ghost_last_updated` | Date | Last Ghost modification |
| `ghost_status_changed_at` | Date | When status last changed |
| `ghost_previous_status` | Text | Previous status value |
| `ghost_labels` | Text | Comma-separated labels |
| `ghost_email_enabled` | Text | Newsletter opt-in status |

### 5. Configure Ghost Webhooks

For **each Ghost site**:

1. Go to Ghost Admin → Settings → Integrations
2. Create a new Custom Integration named "Campaign Monitor Sync"
3. Add webhooks using your site name in the URL:
   - `member.added` → `https://your-domain.com/webhook/ghost/{site_name}?event=member.added`
   - `member.updated` → `https://your-domain.com/webhook/ghost/{site_name}?event=member.updated`
   - `member.deleted` → `https://your-domain.com/webhook/ghost/{site_name}?event=member.deleted`
4. Copy the webhook secret to your `.env` file as `SITEN_GHOST_WEBHOOK_SECRET`

Example for site "mainblog":
- `https://sync.example.com/webhook/ghost/mainblog?event=member.added`

## Running the Service

### Development

```bash
# Start the webhook server
uvicorn src.main:app --reload --port 3000

# Start the queue worker (separate terminal)
rq worker ghost-cm-sync
```

### Production

Using systemd (recommended):

```bash
# Copy service files
sudo cp deploy/ghost-cm-sync.service /etc/systemd/system/
sudo cp deploy/ghost-cm-worker.service /etc/systemd/system/

# Enable and start services
sudo systemctl enable ghost-cm-sync ghost-cm-worker
sudo systemctl start ghost-cm-sync ghost-cm-worker
```

Using PM2 alternative (if preferred):

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

## Operations

### Health Check

```bash
curl http://localhost:3000/health
```

### Full Sync

For initial migration or recovery from data drift:

```bash
# List configured sites
python scripts/full_sync.py --list-sites

# Preview changes without applying
python scripts/full_sync.py --site mainblog --dry-run

# Execute full sync for a site
python scripts/full_sync.py --site mainblog

# Execute with verbose output (shows names)
python scripts/full_sync.py --site mainblog --verbose
```

### Replay Failed Events

```bash
# Replay dead letter queue events from a date range
python scripts/replay_dlq.py --from 2026-01-01 --to 2026-01-02
```

### View Queue Status

```bash
# Check queue depth
rq info

# Monitor workers
rq info --only-workers
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/ghost/{site_id}` | POST | Ghost webhook receiver (per-site) |
| `/health` | GET | Health check (shows configured sites) |
| `/metrics` | GET | Prometheus metrics |

## Nginx Configuration

Example nginx configuration for reverse proxy:

```nginx
server {
    listen 443 ssl http2;
    server_name sync.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/sync.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sync.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=webhook:10m rate=100r/m;
    location /webhook/ {
        limit_req zone=webhook burst=20 nodelay;
        proxy_pass http://127.0.0.1:3000;
    }
}
```

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|--------------|------------|
| Subscribers not appearing | Webhook not firing | Verify Ghost webhook config |
| Status not updating | Signature validation failing | Check GHOST_WEBHOOK_SECRET |
| Intermittent failures | CM rate limiting | Check backoff logic |
| Duplicate subscribers | Idempotency issue | Check event processing |

View logs:

```bash
# Webhook server logs
journalctl -u ghost-cm-sync -f

# Worker logs
journalctl -u ghost-cm-worker -f
```

## Development

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Type checking
mypy src

# Linting
ruff check src

# Format code
ruff format src
```

## License

MIT License - see LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request
