# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `scripts/cleanup_backfill_damage.py` — one-off two-phase cleanup for the 2026-05-23 backfill: mirrors currently-Bounced CM subscribers into Ghost (`subscribed=false`), and unsubscribes CM/Ghost for users whose CM `Date` equals the backfill date but whose Ghost `created_at` predates it (wrongly reactivated).
- `scripts/restore_ssrf_window_members.py` — companion recovery for the above. The cleanup script's heuristic couldn't distinguish two populations: (a) genuinely wrongly-resubscribed historical opt-outs and (b) Apr 25–May 22 signups whose webhooks were blocked by the SSRF bug and only landed in CM via today's backfill. This script restores the in-window cohort by force-adding them with `Resubscribe=True` and setting Ghost `subscribed=true`. Intentionally bypasses the new State-check guard for this one-off recovery. Hardcodes `ghost_email_enabled=true` in the payload to reflect post-restoration intent (rather than the stale-at-read-time `subscribed=false` value cleanup had set).
- `scripts/clear_ssrf_window_suppression.py` — final companion for the recovery trio. Clears Ghost `email_disabled` / `email_suppression.suppressed` flags via `DELETE /members/{id}/suppression/` for members whose Ghost-side delivery was blocked. Runs against the same date window as the restore script.

### Changed

- `full_sync.py --dry-run` now calls CM's read endpoints (Step 1 `get_subscriber`, Step 2 existence check) so the dry-run summary reflects real opt-out and list-membership state. Trade-off: dry-run is slower (one CM GET per active member, one per disabled member) but accurate.

### Fixed

- **Never resubscribe opted-out or bounced subscribers.** `CMSubscriberPayload.Resubscribe` was hardcoded to `True`, causing both webhook sync and `full_sync.py` to forcibly reactivate anyone with State Unsubscribed/Bounced/Deleted in Campaign Monitor. Default is now `False` and the upsert path is gated by an explicit State check (subscribers in non-Active states are skipped and logged as `skipped_cm_opt_out`). Surfaced when the 2026-05-23 backfill silently reactivated previously-unsubscribed users.

### Tests

- Regression tests in `tests/test_processor.py` that assert `process_member_added` and `process_member_updated` skip the upsert (and never call `add_or_update_subscriber`) when the existing CM subscriber has State `Unsubscribed`, `Bounced`, or `Deleted`.

## [0.2.0] - 2026-05-23

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
- Slack failure alerts via systemd `OnFailure=` (`deploy/slack-alert-failure.sh`, `deploy/alert-failure@.service`, `deploy/notify.env.example`); fires when either unit enters the failed state, posting the last 20 journal lines to a Slack incoming webhook

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

### Security

- **BREAKING**: Webhook secrets are now mandatory - validation raises error if secret is missing (fail-safe)
- Added timestamp validation to signatures - rejects signatures older than 5 minutes (replay attack prevention)
- Added rate limiting on webhook endpoint (100 requests/minute per IP via slowapi)
- Removed debug logging of raw webhook payloads (PII protection)
- Fixed DLQ replay script to properly pass site_id to process_event
- Added Ghost URL validation (HTTPS required, blocks AWS/GCP metadata endpoints, private IPs)
- Added hex secret validation with proper error handling
- Added email sanitization for Ghost API filter queries (prevents injection)
- Added site_id format validation (alphanumeric, hyphens, underscores only)
- Added CM custom field length limits and sanitization
- Added CM API error message sanitization (prevents information disclosure)
- Added per-site rate limiting for CM API calls (10 req/sec with burst of 20)
- Added Pydantic models for CM API response validation
- Unknown site now returns 401 instead of 404 (prevents site enumeration)

### Fixed

- Signature computation now correctly includes timestamp in HMAC (was causing validation failures)
- Unsubscribe now treats "subscriber not in list" (Code 203) as success (idempotent delete)
- Settings model now ignores SITE*_ variables (fixes startup crash with multi-site config)
- full_sync.py now explicitly loads .env file from project root
- replay_dlq.py now correctly extracts and passes site_id when replaying events
- Timestamp validation now handles Ghost's millisecond timestamps (converts to seconds before comparison)
- `full_sync.py` no longer crashes on members that already exist in Campaign Monitor: `get_subscriber()` returns a Pydantic `CMSubscriberResponse` model now, and the script was still using dict-style `.get()` access

### Tests

- Added tests for name field serialization in Campaign Monitor payload
- Added test to verify name is passed through event processor
- Updated all tests for multi-site endpoint pattern
- Added tests for QueuedEvent site_id field
- Added tests for mandatory webhook secret validation
- Added tests for expired/future signature timestamp rejection
- Added comprehensive test suite for validation module (43 new tests)
- Tests for URL validation, hex secret validation, email sanitization
- Tests for site_id validation, CM field truncation, error sanitization
- Tests for rate limiter functionality
- Tests for millisecond timestamp handling in signature validation

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

[Unreleased]: https://github.com/yourusername/ghost-cm-sync/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/yourusername/ghost-cm-sync/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/yourusername/ghost-cm-sync/releases/tag/v0.1.0
