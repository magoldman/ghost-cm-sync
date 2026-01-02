# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
