"""Webhook signature validation for Ghost webhooks."""

import hashlib
import hmac
from typing import Any

from src.config import get_settings
from src.logging_config import get_logger

logger = get_logger(__name__)


def validate_signature(payload: bytes, signature: str | None) -> bool:
    """
    Validate Ghost webhook signature.

    Ghost uses HMAC-SHA256 with the webhook secret to sign payloads.
    The signature is sent in the X-Ghost-Signature header.

    Args:
        payload: Raw request body bytes
        signature: Signature from X-Ghost-Signature header

    Returns:
        True if signature is valid, False otherwise
    """
    settings = get_settings()

    # If no secret configured, skip validation (not recommended for production)
    if not settings.ghost_webhook_secret:
        logger.warning("signature_validation_disabled", reason="no secret configured")
        return True

    if not signature:
        logger.warning("signature_missing")
        return False

    secret = settings.ghost_webhook_secret.encode()

    # Ghost signature format: sha256=<hex_digest>, t=<timestamp>
    # Parse both parts
    sig_parts = dict(part.split("=", 1) for part in signature.split(", ") if "=" in part)
    expected_sig = sig_parts.get("sha256")
    timestamp = sig_parts.get("t")

    if not expected_sig:
        logger.warning("signature_parse_failed", signature=signature)
        return False

    if not timestamp:
        logger.warning("signature_timestamp_missing", signature=signature)
        return False

    # Ghost signs: body + timestamp (concatenated)
    payload_to_sign = payload + timestamp.encode()
    computed = hmac.new(secret, payload_to_sign, hashlib.sha256).hexdigest()

    is_valid = hmac.compare_digest(computed, expected_sig)

    if not is_valid:
        logger.warning(
            "signature_mismatch",
            expected=expected_sig[:16] + "...",
            computed=computed[:16] + "...",
        )

    return is_valid


def compute_signature(payload: bytes, secret: str | None = None) -> str:
    """
    Compute signature for a payload.

    Useful for testing and generating signatures.

    Args:
        payload: Request body bytes
        secret: Optional secret override (uses config if not provided)

    Returns:
        Signature string in Ghost format
    """
    if secret is None:
        settings = get_settings()
        secret = settings.ghost_webhook_secret

    computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={computed}, t={int(__import__('time').time())}"
