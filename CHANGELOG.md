# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Multi-site support**: Sync multiple Ghost instances to separate Campaign Monitor lists
- New webhook endpoint pattern: `/webhook/ghost/{site_id}` for per-site routing
- Per-site configuration via environment variables (SITE1_*, SITE2_*, etc.)
- `SiteConfig` model for per-site settings (webhook secret, Ghost URL, CM list ID)
- `--site` flag for full_sync.py to sync specific sites
- `--list-sites` flag for full_sync.py to show configured sites
- Per-site Campaign Monitor client caching
- Site ID tracking in queued events for proper routing
- Health endpoint now shows configured sites

### Improved

- Enhanced name field sync visibility: Added name to subscriber upsert logging
- Added `--verbose` flag to full_sync.py to display names during sync
- Improved sync result output to include member names
- Empty names display as blank (not placeholder text) in logs and output
- Logging includes site_id for multi-site debugging

### Changed

- **BREAKING**: Webhook endpoint changed from `/webhook/ghost` to `/webhook/ghost/{site_id}`
- **BREAKING**: Environment variables restructured for multi-site (see README.md)
- Signature validation now parameterized per-site
- Campaign Monitor client now accepts list_id at initialization

### Fixed

- Signature computation now correctly includes timestamp in HMAC (was causing validation failures)
- Unsubscribe now treats "subscriber not in list" (Code 203) as success (idempotent delete)
- Settings model now ignores SITE*_ variables (fixes startup crash with multi-site config)
- full_sync.py now explicitly loads .env file from project root

### Tests

- Added tests for name field serialization in Campaign Monitor payload
- Added test to verify name is passed through event processor
- Updated all tests for multi-site endpoint pattern
- Added tests for QueuedEvent site_id field

## [0.1.0] - 2026-01-02

### Added

- Initial release
- FastAPI webhook handler for Ghost member events
- Campaign Monitor API client with connection pooling
- Redis-backed RQ queue for async event processing
- Webhook signature validation (HMAC)
- Status change detection with historical tracking
- Custom field mapping: ghost_status, ghost_signup_date, ghost_last_updated, ghost_status_changed_at, ghost_previous_status, ghost_labels, ghost_email_enabled
- Exponential backoff retry (1s, 2s, 4s, 8s, 16s)
- Dead letter queue for failed events
- Circuit breaker pattern (10 failures = 5 min cooldown)
- Health check endpoint (`GET /health`)
- Full sync script for initial migration and recovery
- Dead letter queue replay script
- Structured JSON logging
- Unit tests with pytest

### Security

- HMAC signature validation for all webhooks
- Rate limiting ready (nginx config provided)
- Environment-based secret management
- No PII logged beyond email address

[Unreleased]: https://github.com/yourusername/ghost-cm-sync/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yourusername/ghost-cm-sync/releases/tag/v0.1.0
