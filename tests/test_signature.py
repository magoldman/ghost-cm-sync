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

    def test_empty_secret_skips_validation(self) -> None:
        """Test that empty secret skips validation (returns True)."""
        payload = b'{"test": "data"}'

        assert validate_signature(payload, "any-signature", "") is True


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
