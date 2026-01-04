"""Security validation utilities for input sanitization and validation."""

import re
import time
from urllib.parse import urlparse

from src.logging_config import get_logger

logger = get_logger(__name__)

# Valid site_id pattern: alphanumeric, hyphens, underscores, 1-50 chars
SITE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")

# Valid email pattern for filter sanitization (strict subset)
# Only allows alphanumeric, dots, hyphens, underscores, plus signs, and @
EMAIL_FILTER_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# Maximum field lengths for Campaign Monitor custom fields
CM_FIELD_MAX_LENGTHS = {
    "ghost_status": 20,
    "ghost_signup_date": 10,
    "ghost_last_updated": 24,
    "ghost_status_changed_at": 24,
    "ghost_previous_status": 20,
    "ghost_labels": 500,  # Comma-separated labels
    "ghost_email_enabled": 5,
}

# Maximum length for subscriber name
CM_NAME_MAX_LENGTH = 200


def validate_site_id(site_id: str) -> bool:
    """
    Validate site_id format to prevent injection and enumeration attacks.

    Args:
        site_id: The site identifier to validate

    Returns:
        True if valid, False otherwise
    """
    if not site_id:
        return False

    if not SITE_ID_PATTERN.match(site_id):
        logger.warning("invalid_site_id_format", site_id=site_id[:50])
        return False

    return True


def validate_ghost_url(url: str, allow_localhost: bool = False) -> tuple[bool, str | None]:
    """
    Validate Ghost URL to prevent SSRF attacks.

    Args:
        url: The Ghost URL to validate
        allow_localhost: If True, allow localhost URLs (for development)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not url:
        return False, "URL is required"

    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL format: {e}"

    # Must use HTTPS in production
    if parsed.scheme not in ("https", "http"):
        return False, f"URL must use HTTPS or HTTP scheme, got: {parsed.scheme}"

    if parsed.scheme == "http" and not allow_localhost:
        # Only allow HTTP for localhost in development
        if parsed.hostname not in ("localhost", "127.0.0.1"):
            return False, "HTTP is only allowed for localhost URLs"

    # Must have a valid hostname
    if not parsed.hostname:
        return False, "URL must have a valid hostname"

    # Block obviously dangerous hostnames
    dangerous_hosts = {
        "0.0.0.0",
        "169.254.169.254",  # AWS metadata
        "metadata.google.internal",  # GCP metadata
        "metadata.azure.com",  # Azure metadata
    }

    if parsed.hostname in dangerous_hosts:
        logger.warning("blocked_dangerous_host", hostname=parsed.hostname)
        return False, "URL hostname is not allowed"

    # Block private IP ranges (basic check)
    if not allow_localhost:
        hostname = parsed.hostname.lower()
        if hostname in ("localhost", "127.0.0.1", "::1"):
            return False, "Localhost URLs not allowed in production"

        # Block common private network patterns
        if hostname.startswith("10.") or hostname.startswith("192.168."):
            return False, "Private IP addresses not allowed"

        if hostname.startswith("172."):
            # 172.16.0.0 - 172.31.255.255 is private
            parts = hostname.split(".")
            if len(parts) >= 2:
                try:
                    second_octet = int(parts[1])
                    if 16 <= second_octet <= 31:
                        return False, "Private IP addresses not allowed"
                except ValueError:
                    pass

    return True, None


def validate_hex_secret(secret: str) -> tuple[bool, bytes | None, str | None]:
    """
    Validate and decode a hex-encoded secret.

    Args:
        secret: The hex-encoded secret string

    Returns:
        Tuple of (is_valid, decoded_bytes, error_message)
    """
    if not secret:
        return False, None, "Secret is required"

    # Remove any whitespace
    secret = secret.strip()

    # Must be even length (hex pairs)
    if len(secret) % 2 != 0:
        return False, None, "Invalid hex encoding: odd number of characters"

    # Must be valid hex characters
    if not re.match(r"^[0-9a-fA-F]+$", secret):
        return False, None, "Invalid hex encoding: non-hex characters found"

    try:
        decoded = bytes.fromhex(secret)
        return True, decoded, None
    except ValueError as e:
        return False, None, f"Failed to decode hex secret: {e}"


def sanitize_email_for_filter(email: str) -> tuple[bool, str | None, str | None]:
    """
    Sanitize an email address for use in Ghost API filter queries.

    Prevents filter injection attacks by validating email format.

    Args:
        email: The email address to sanitize

    Returns:
        Tuple of (is_valid, sanitized_email, error_message)
    """
    if not email:
        return False, None, "Email is required"

    # Trim whitespace
    email = email.strip().lower()

    # Check length
    if len(email) > 254:  # RFC 5321 limit
        return False, None, "Email address too long"

    # Validate format
    if not EMAIL_FILTER_PATTERN.match(email):
        logger.warning("invalid_email_for_filter", email_hash=email[:3] + "***")
        return False, None, "Invalid email format"

    # Escape single quotes to prevent filter injection
    # Ghost API uses filter: email:'value' syntax
    sanitized = email.replace("'", "")  # Remove quotes entirely

    # Double-check no injection characters remain
    if any(c in sanitized for c in ["'", '"', ";", "--", "/*", "*/"]):
        return False, None, "Email contains invalid characters"

    return True, sanitized, None


def truncate_cm_field(key: str, value: str) -> str:
    """
    Truncate a Campaign Monitor custom field value to its maximum length.

    Args:
        key: The field key
        value: The field value

    Returns:
        Truncated value
    """
    max_length = CM_FIELD_MAX_LENGTHS.get(key, 200)

    if len(value) > max_length:
        logger.warning(
            "cm_field_truncated",
            field=key,
            original_length=len(value),
            max_length=max_length,
        )
        return value[:max_length]

    return value


def sanitize_cm_name(name: str | None) -> str:
    """
    Sanitize a name for Campaign Monitor.

    Args:
        name: The name to sanitize

    Returns:
        Sanitized name
    """
    if not name:
        return ""

    # Truncate to max length
    if len(name) > CM_NAME_MAX_LENGTH:
        name = name[:CM_NAME_MAX_LENGTH]

    # Remove any control characters
    name = "".join(c for c in name if c.isprintable() or c in (" ", "-", "'"))

    return name.strip()


def sanitize_cm_labels(labels: list) -> str:
    """
    Sanitize Ghost labels for Campaign Monitor custom field.

    Args:
        labels: List of label objects with 'name' attribute

    Returns:
        Sanitized comma-separated label string
    """
    if not labels:
        return ""

    sanitized_names = []
    for label in labels:
        name = getattr(label, "name", str(label)) if hasattr(label, "name") else str(label)
        # Remove commas and limit length per label
        name = name.replace(",", "").strip()[:50]
        if name:
            sanitized_names.append(name)

    result = ",".join(sanitized_names)

    # Truncate total length
    max_length = CM_FIELD_MAX_LENGTHS.get("ghost_labels", 500)
    if len(result) > max_length:
        result = result[:max_length].rsplit(",", 1)[0]  # Don't cut mid-label

    return result


def sanitize_error_message(message: str, max_length: int = 200) -> str:
    """
    Sanitize an error message to prevent information disclosure.

    Removes potentially sensitive information like API keys, URLs with auth, etc.

    Args:
        message: The error message to sanitize
        max_length: Maximum length for the sanitized message

    Returns:
        Sanitized error message
    """
    if not message:
        return "Unknown error"

    # Truncate first
    if len(message) > max_length:
        message = message[:max_length] + "..."

    # Remove potential API keys (long hex/base64 strings)
    message = re.sub(r"[a-fA-F0-9]{32,}", "[REDACTED]", message)
    message = re.sub(r"[a-zA-Z0-9+/]{40,}={0,2}", "[REDACTED]", message)

    # Remove URLs with potential credentials
    message = re.sub(r"https?://[^:]+:[^@]+@", "https://[REDACTED]@", message)

    # Remove email addresses
    message = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[EMAIL]", message)

    return message


class RateLimiter:
    """
    Simple token bucket rate limiter for API calls.

    Thread-safe for single-process use with GIL.
    """

    def __init__(self, calls_per_second: float = 10.0, burst_size: int = 20):
        """
        Initialize rate limiter.

        Args:
            calls_per_second: Maximum sustained call rate
            burst_size: Maximum burst size
        """
        self.calls_per_second = calls_per_second
        self.burst_size = burst_size
        self._tokens = float(burst_size)
        self._last_update = time.time()

    def acquire(self, timeout: float = 5.0) -> bool:
        """
        Acquire a token, waiting if necessary.

        Args:
            timeout: Maximum time to wait for a token

        Returns:
            True if token acquired, False if timeout
        """
        start = time.time()

        while True:
            now = time.time()

            # Refill tokens based on time elapsed
            elapsed = now - self._last_update
            self._tokens = min(
                self.burst_size,
                self._tokens + elapsed * self.calls_per_second,
            )
            self._last_update = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            # Check timeout
            if now - start >= timeout:
                return False

            # Wait a bit before retrying
            time.sleep(0.05)

    def try_acquire(self) -> bool:
        """
        Try to acquire a token without waiting.

        Returns:
            True if token acquired, False otherwise
        """
        now = time.time()

        # Refill tokens based on time elapsed
        elapsed = now - self._last_update
        self._tokens = min(
            self.burst_size,
            self._tokens + elapsed * self.calls_per_second,
        )
        self._last_update = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True

        return False
