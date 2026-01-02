"""Tests for FastAPI endpoints."""

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set environment before importing
os.environ["GHOST_WEBHOOK_SECRET"] = "test-secret-key"
os.environ["CM_API_KEY"] = "test-cm-api-key"
os.environ["CM_LIST_ID"] = "test-list-id"

from src.signature import compute_signature


class TestWebhookEndpoint:
    """Tests for /webhook/ghost endpoint."""

    def test_valid_webhook(
        self,
        client: TestClient,
        sample_ghost_payload: dict[str, Any],
    ) -> None:
        """Test valid webhook with correct signature."""
        payload_bytes = json.dumps(sample_ghost_payload).encode()
        signature = compute_signature(payload_bytes, "test-secret-key")

        response = client.post(
            "/webhook/ghost",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Ghost-Signature": signature,
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert "job_id" in data

    def test_missing_signature(
        self,
        client: TestClient,
        sample_ghost_payload: dict[str, Any],
    ) -> None:
        """Test webhook without signature header."""
        response = client.post(
            "/webhook/ghost",
            json=sample_ghost_payload,
        )

        assert response.status_code == 401

    def test_invalid_signature(
        self,
        client: TestClient,
        sample_ghost_payload: dict[str, Any],
    ) -> None:
        """Test webhook with invalid signature."""
        response = client.post(
            "/webhook/ghost",
            json=sample_ghost_payload,
            headers={"X-Ghost-Signature": "sha256=invalid, t=12345"},
        )

        assert response.status_code == 401

    def test_invalid_json(self, client: TestClient) -> None:
        """Test webhook with invalid JSON payload."""
        payload = b"not valid json"
        signature = compute_signature(payload, "test-secret-key")

        response = client.post(
            "/webhook/ghost",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Ghost-Signature": signature,
            },
        )

        assert response.status_code == 400


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_check_healthy(self, client: TestClient) -> None:
        """Test health check when all services healthy."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "checks" in data

    @patch("src.main.get_redis_connection")
    def test_health_check_redis_down(
        self, mock_redis: MagicMock, client: TestClient
    ) -> None:
        """Test health check when Redis is down."""
        mock_redis.side_effect = Exception("Connection refused")

        response = client.get("/health")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"


class TestMetricsEndpoint:
    """Tests for /metrics endpoint."""

    def test_metrics(self, client: TestClient) -> None:
        """Test metrics endpoint."""
        response = client.get("/metrics")

        assert response.status_code == 200
        data = response.json()

        assert "events_received" in data
        assert "events_processed" in data
        assert "events_failed" in data
        assert "queue_depth" in data
        assert "uptime_seconds" in data
        assert "success_rate" in data
