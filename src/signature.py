"""Webhook signature validation for Ghost webhooks."""

import hashlib
import hmac
import time

from src.logging_config import get_logger

# Maximum age in seconds for a valid signature (5 minutes)
SIGNATURE_MAX_AGE_SECONDS = 300

logger = get_logger(__name__)


def validate_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    """
    Validate Ghost webhook signature.

    Ghost uses HMAC-SHA256 with the webhook secret to sign payloads.
    The signature is sent in the X-Ghost-Signature header.

    Args:
        payload: Raw request body bytes
        signature: Signature from X-Ghost-Signature header
        secret: The webhook secret for this site

    Returns:
        True if signature is valid, False otherwise
    """
    # Webhook secret is mandatory - reject if not configured
    if not secret:
        logger.error("signature_validation_failed", reason="webhook secret not configured")
        raise ValueError("Webhook secret is required for signature validation")

    if not signature:
        logger.warning("signature_missing")
        return False

    secret_bytes = secret.encode()

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

    # Validate timestamp is within acceptable window to prevent replay attacks
    try:
        ts = int(timestamp)
        current_time = int(time.time())
        age_seconds = abs(current_time - ts)
        if age_seconds > SIGNATURE_MAX_AGE_SECONDS:
            logger.warning(
                "signature_timestamp_expired",
                timestamp=timestamp,
                age_seconds=age_seconds,
                max_age_seconds=SIGNATURE_MAX_AGE_SECONDS,
            )
            return False
    except (ValueError, TypeError):
        logger.warning("signature_timestamp_invalid", timestamp=timestamp)
        return False

    # Ghost signs: body + timestamp (concatenated)
    payload_to_sign = payload + timestamp.encode()
    computed = hmac.new(secret_bytes, payload_to_sign, hashlib.sha256).hexdigest()

    is_valid = hmac.compare_digest(computed, expected_sig)

    if not is_valid:
        logger.warning(
            "signature_mismatch",
            expected=expected_sig[:16] + "...",
            computed=computed[:16] + "...",
        )

    return is_valid


def compute_signature(payload: bytes, secret: str) -> str:
    """
    Compute signature for a payload.

    Useful for testing and generating signatures.

    Args:
        payload: Request body bytes
        secret: The webhook secret

    Returns:
        Signature string in Ghost format
    """
    timestamp = str(int(time.time()))
    # Ghost signs: body + timestamp (concatenated)
    payload_to_sign = payload + timestamp.encode()
    computed = hmac.new(secret.encode(), payload_to_sign, hashlib.sha256).hexdigest()
    return f"sha256={computed}, t={timestamp}"
