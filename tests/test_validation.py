"""Tests for security validation utilities."""

import os
import time

import pytest

# Set environment before importing
os.environ["CM_API_KEY"] = "test-cm-api-key"
os.environ["SITE1_NAME"] = "testsite"
os.environ["SITE1_GHOST_WEBHOOK_SECRET"] = "test-secret-key"
os.environ["SITE1_CM_LIST_ID"] = "test-list-id"

from src.validation import (
    RateLimiter,
    sanitize_cm_labels,
    sanitize_cm_name,
    sanitize_email_for_filter,
    sanitize_error_message,
    truncate_cm_field,
    validate_ghost_url,
    validate_hex_secret,
    validate_site_id,
)


class TestValidateSiteId:
    """Tests for site_id validation."""

    def test_valid_site_id(self) -> None:
        """Test valid site IDs pass validation."""
        assert validate_site_id("testsite") is True
        assert validate_site_id("test-site") is True
        assert validate_site_id("test_site") is True
        assert validate_site_id("TestSite123") is True
        assert validate_site_id("a") is True  # Min length
        assert validate_site_id("a" * 50) is True  # Max length

    def test_invalid_site_id_empty(self) -> None:
        """Test empty site IDs fail validation."""
        assert validate_site_id("") is False
        assert validate_site_id(None) is False  # type: ignore

    def test_invalid_site_id_too_long(self) -> None:
        """Test site IDs over 50 chars fail validation."""
        assert validate_site_id("a" * 51) is False

    def test_invalid_site_id_special_chars(self) -> None:
        """Test site IDs with special characters fail validation."""
        assert validate_site_id("test.site") is False
        assert validate_site_id("test/site") is False
        assert validate_site_id("test site") is False
        assert validate_site_id("test@site") is False
        assert validate_site_id("../etc/passwd") is False


class TestValidateGhostUrl:
    """Tests for Ghost URL validation."""

    def test_valid_https_url(self) -> None:
        """Test valid HTTPS URLs pass validation."""
        is_valid, error = validate_ghost_url("https://blog.example.com")
        assert is_valid is True
        assert error is None

    def test_valid_https_url_with_port(self) -> None:
        """Test HTTPS URL with port passes."""
        is_valid, error = validate_ghost_url("https://blog.example.com:8443")
        assert is_valid is True

    def test_invalid_http_url_production(self) -> None:
        """Test HTTP URLs fail in production mode."""
        is_valid, error = validate_ghost_url("http://blog.example.com")
        assert is_valid is False
        assert "HTTP is only allowed for localhost" in error

    def test_http_localhost_allowed_in_dev(self) -> None:
        """Test HTTP localhost allowed when allow_localhost=True."""
        is_valid, error = validate_ghost_url("http://localhost:2368", allow_localhost=True)
        assert is_valid is True

    def test_localhost_blocked_in_production(self) -> None:
        """Test localhost blocked when allow_localhost=False."""
        is_valid, error = validate_ghost_url("http://localhost:2368", allow_localhost=False)
        assert is_valid is False

    def test_dangerous_hosts_blocked(self) -> None:
        """Test AWS/GCP metadata endpoints are blocked."""
        is_valid, error = validate_ghost_url("https://169.254.169.254")
        assert is_valid is False

        is_valid, error = validate_ghost_url("https://metadata.google.internal")
        assert is_valid is False

    def test_private_ips_blocked(self) -> None:
        """Test private IP ranges are blocked in production."""
        is_valid, error = validate_ghost_url("https://10.0.0.1", allow_localhost=False)
        assert is_valid is False

        is_valid, error = validate_ghost_url("https://192.168.1.1", allow_localhost=False)
        assert is_valid is False

        is_valid, error = validate_ghost_url("https://172.16.0.1", allow_localhost=False)
        assert is_valid is False

    def test_empty_url_fails(self) -> None:
        """Test empty URL fails validation."""
        is_valid, error = validate_ghost_url("")
        assert is_valid is False
        assert "required" in error.lower()

    def test_invalid_scheme(self) -> None:
        """Test invalid schemes fail."""
        is_valid, error = validate_ghost_url("ftp://blog.example.com")
        assert is_valid is False


class TestValidateHexSecret:
    """Tests for hex secret validation."""

    def test_valid_hex_secret(self) -> None:
        """Test valid hex secrets decode correctly."""
        is_valid, decoded, error = validate_hex_secret("deadbeef")
        assert is_valid is True
        assert decoded == b"\xde\xad\xbe\xef"
        assert error is None

    def test_valid_uppercase_hex(self) -> None:
        """Test uppercase hex is accepted."""
        is_valid, decoded, error = validate_hex_secret("DEADBEEF")
        assert is_valid is True
        assert decoded == b"\xde\xad\xbe\xef"

    def test_empty_secret_fails(self) -> None:
        """Test empty secret fails validation."""
        is_valid, decoded, error = validate_hex_secret("")
        assert is_valid is False
        assert decoded is None
        assert "required" in error.lower()

    def test_odd_length_fails(self) -> None:
        """Test odd-length hex strings fail."""
        is_valid, decoded, error = validate_hex_secret("abc")
        assert is_valid is False
        assert "odd number" in error.lower()

    def test_non_hex_chars_fail(self) -> None:
        """Test non-hex characters fail."""
        is_valid, decoded, error = validate_hex_secret("ghijklmn")
        assert is_valid is False
        assert "non-hex" in error.lower()


class TestSanitizeEmailForFilter:
    """Tests for email filter sanitization."""

    def test_valid_email(self) -> None:
        """Test valid emails pass sanitization."""
        is_valid, sanitized, error = sanitize_email_for_filter("test@example.com")
        assert is_valid is True
        assert sanitized == "test@example.com"
        assert error is None

    def test_email_lowercase(self) -> None:
        """Test emails are lowercased."""
        is_valid, sanitized, error = sanitize_email_for_filter("TEST@EXAMPLE.COM")
        assert is_valid is True
        assert sanitized == "test@example.com"

    def test_email_with_quotes_rejected(self) -> None:
        """Test emails with quotes are rejected (injection attempt)."""
        is_valid, sanitized, error = sanitize_email_for_filter("test'@example.com")
        assert is_valid is False

    def test_sql_injection_attempt(self) -> None:
        """Test SQL injection attempts are rejected."""
        is_valid, sanitized, error = sanitize_email_for_filter("test'; DROP TABLE--@example.com")
        assert is_valid is False

    def test_empty_email_fails(self) -> None:
        """Test empty email fails."""
        is_valid, sanitized, error = sanitize_email_for_filter("")
        assert is_valid is False

    def test_too_long_email_fails(self) -> None:
        """Test emails over 254 chars fail."""
        long_email = "a" * 250 + "@example.com"
        is_valid, sanitized, error = sanitize_email_for_filter(long_email)
        assert is_valid is False
        assert "too long" in error.lower()


class TestTruncateCmField:
    """Tests for CM field truncation."""

    def test_field_within_limit(self) -> None:
        """Test fields within limit are unchanged."""
        result = truncate_cm_field("ghost_status", "paid")
        assert result == "paid"

    def test_field_truncated(self) -> None:
        """Test long fields are truncated."""
        long_status = "a" * 100
        result = truncate_cm_field("ghost_status", long_status)
        assert len(result) == 20  # ghost_status max length

    def test_labels_truncated(self) -> None:
        """Test labels field truncated to 500 chars."""
        long_labels = "label," * 200
        result = truncate_cm_field("ghost_labels", long_labels)
        assert len(result) == 500


class TestSanitizeCmName:
    """Tests for CM name sanitization."""

    def test_normal_name(self) -> None:
        """Test normal names pass through."""
        result = sanitize_cm_name("John Doe")
        assert result == "John Doe"

    def test_empty_name(self) -> None:
        """Test empty/None name returns empty string."""
        assert sanitize_cm_name(None) == ""
        assert sanitize_cm_name("") == ""

    def test_long_name_truncated(self) -> None:
        """Test names over 200 chars are truncated."""
        long_name = "A" * 250
        result = sanitize_cm_name(long_name)
        assert len(result) == 200

    def test_control_chars_removed(self) -> None:
        """Test control characters are removed."""
        result = sanitize_cm_name("John\x00Doe\x1f")
        assert result == "JohnDoe"


class TestSanitizeCmLabels:
    """Tests for CM labels sanitization."""

    def test_normal_labels(self) -> None:
        """Test normal labels are joined correctly."""
        from src.models import GhostLabel

        labels = [GhostLabel(name="VIP", slug="vip"), GhostLabel(name="Premium", slug="premium")]
        result = sanitize_cm_labels(labels)
        assert result == "VIP,Premium"

    def test_empty_labels(self) -> None:
        """Test empty labels returns empty string."""
        assert sanitize_cm_labels([]) == ""
        assert sanitize_cm_labels(None) == ""  # type: ignore

    def test_commas_removed_from_labels(self) -> None:
        """Test commas are removed from individual labels."""
        from src.models import GhostLabel

        labels = [GhostLabel(name="VIP,Gold", slug="vip-gold")]
        result = sanitize_cm_labels(labels)
        assert "," not in result or result == ""  # Either no commas or handled properly


class TestSanitizeErrorMessage:
    """Tests for error message sanitization."""

    def test_normal_message(self) -> None:
        """Test normal messages pass through."""
        result = sanitize_error_message("Failed to connect")
        assert result == "Failed to connect"

    def test_long_message_truncated(self) -> None:
        """Test long messages are truncated."""
        long_msg = "A" * 300
        result = sanitize_error_message(long_msg, max_length=200)
        assert len(result) <= 203  # 200 + "..."

    def test_api_keys_redacted(self) -> None:
        """Test long hex strings (API keys) are redacted."""
        msg = "Failed with key: deadbeefdeadbeefdeadbeefdeadbeef"
        result = sanitize_error_message(msg)
        assert "deadbeef" not in result
        assert "[REDACTED]" in result

    def test_emails_redacted(self) -> None:
        """Test email addresses are redacted."""
        msg = "Error processing user@example.com"
        result = sanitize_error_message(msg)
        assert "user@example.com" not in result
        assert "[EMAIL]" in result

    def test_empty_message(self) -> None:
        """Test empty message returns default."""
        result = sanitize_error_message("")
        assert result == "Unknown error"


class TestRateLimiter:
    """Tests for rate limiter."""

    def test_acquire_within_limit(self) -> None:
        """Test acquiring tokens within limit succeeds."""
        limiter = RateLimiter(calls_per_second=10.0, burst_size=5)

        # Should be able to acquire burst_size tokens immediately
        for _ in range(5):
            assert limiter.try_acquire() is True

    def test_acquire_over_limit_fails(self) -> None:
        """Test acquiring over burst limit fails."""
        limiter = RateLimiter(calls_per_second=10.0, burst_size=3)

        # Exhaust burst
        for _ in range(3):
            limiter.try_acquire()

        # Next should fail
        assert limiter.try_acquire() is False

    def test_tokens_refill(self) -> None:
        """Test tokens refill over time."""
        limiter = RateLimiter(calls_per_second=100.0, burst_size=1)

        # Use the token
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

        # Wait for refill (100/sec = 0.01s per token)
        time.sleep(0.02)

        # Should have a token now
        assert limiter.try_acquire() is True

    def test_blocking_acquire(self) -> None:
        """Test blocking acquire waits for token."""
        limiter = RateLimiter(calls_per_second=100.0, burst_size=1)

        # Use the token
        limiter.try_acquire()

        # Blocking acquire should succeed after waiting
        start = time.time()
        result = limiter.acquire(timeout=0.5)
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 0.5  # Should complete before timeout
