"""Tests for webhook signature validation."""

import os

# Set environment before importing
os.environ["CM_API_KEY"] = "test-cm-api-key"
os.environ["SITE1_NAME"] = "testsite"
os.environ["SITE1_GHOST_WEBHOOK_SECRET"] = "test-secret-key"
os.environ["SITE1_CM_LIST_ID"] = "test-list-id"

from src.signature import compute_signature, validate_signature


class TestSignatureValidation:
    """Tests for validate_signature function."""

    def test_valid_signature(self) -> None:
        """Test that valid signatures pass validation."""
        payload = b'{"test": "data"}'
        secret = "test-secret-key"
        signature = compute_signature(payload, secret)

        assert validate_signature(payload, signature, secret) is True

    def test_invalid_signature(self) -> None:
        """Test that invalid signatures fail validation."""
        payload = b'{"test": "data"}'
        secret = "test-secret-key"
        signature = "sha256=invalid_signature, t=1234567890"

        assert validate_signature(payload, signature, secret) is False

    def test_missing_signature(self) -> None:
        """Test that missing signatures fail validation."""
        payload = b'{"test": "data"}'
        secret = "test-secret-key"

        assert validate_signature(payload, None, secret) is False

    def test_malformed_signature(self) -> None:
        """Test that malformed signatures fail validation."""
        payload = b'{"test": "data"}'
        secret = "test-secret-key"

        # Missing sha256 prefix
        assert validate_signature(payload, "just_a_hash", secret) is False

        # Empty signature
        assert validate_signature(payload, "", secret) is False

    def test_tampered_payload(self) -> None:
        """Test that tampered payloads fail validation."""
        original_payload = b'{"test": "data"}'
        secret = "test-secret-key"
        signature = compute_signature(original_payload, secret)

        tampered_payload = b'{"test": "tampered"}'

        assert validate_signature(tampered_payload, signature, secret) is False

    def test_empty_secret_raises_error(self) -> None:
        """Test that empty secret raises ValueError (security requirement)."""
        import pytest

        payload = b'{"test": "data"}'

        with pytest.raises(ValueError, match="Webhook secret is required"):
            validate_signature(payload, "any-signature", "")

    def test_expired_signature_rejected(self) -> None:
        """Test that signatures older than 5 minutes are rejected."""
        import hashlib
        import hmac
        import time

        payload = b'{"test": "data"}'
        secret = "test-secret-key"

        # Create a signature with an old timestamp (10 minutes ago)
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        payload_to_sign = payload + old_timestamp.encode()
        computed = hmac.new(secret.encode(), payload_to_sign, hashlib.sha256).hexdigest()
        old_signature = f"sha256={computed}, t={old_timestamp}"

        # This should be rejected due to expired timestamp
        assert validate_signature(payload, old_signature, secret) is False

    def test_future_signature_rejected(self) -> None:
        """Test that signatures with future timestamps are rejected."""
        import hashlib
        import hmac
        import time

        payload = b'{"test": "data"}'
        secret = "test-secret-key"

        # Create a signature with a future timestamp (10 minutes from now)
        future_timestamp = str(int(time.time()) + 600)  # 10 minutes in future
        payload_to_sign = payload + future_timestamp.encode()
        computed = hmac.new(secret.encode(), payload_to_sign, hashlib.sha256).hexdigest()
        future_signature = f"sha256={computed}, t={future_timestamp}"

        # This should be rejected due to future timestamp
        assert validate_signature(payload, future_signature, secret) is False


class TestComputeSignature:
    """Tests for compute_signature function."""

    def test_signature_format(self) -> None:
        """Test that computed signature has correct format."""
        payload = b'{"test": "data"}'
        signature = compute_signature(payload, "test-secret-key")

        assert signature.startswith("sha256=")
        assert ", t=" in signature

    def test_deterministic(self) -> None:
        """Test that same payload produces same hash (ignoring timestamp)."""
        payload = b'{"test": "data"}'

        sig1 = compute_signature(payload, "test-secret-key")
        sig2 = compute_signature(payload, "test-secret-key")

        # Extract just the hash part
        hash1 = sig1.split(",")[0]
        hash2 = sig2.split(",")[0]

        assert hash1 == hash2

    def test_different_secrets_different_signatures(self) -> None:
        """Test that different secrets produce different signatures."""
        payload = b'{"test": "data"}'

        sig1 = compute_signature(payload, "secret1")
        sig2 = compute_signature(payload, "secret2")

        hash1 = sig1.split(",")[0]
        hash2 = sig2.split(",")[0]

        assert hash1 != hash2
