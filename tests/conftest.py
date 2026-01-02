"""Pytest fixtures for ghost-cm-sync tests."""

import os
from datetime import datetime, timezone
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set test environment variables before importing app
os.environ["GHOST_WEBHOOK_SECRET"] = "test-secret-key"
os.environ["CM_API_KEY"] = "test-cm-api-key"
os.environ["CM_LIST_ID"] = "test-list-id"
os.environ["REDIS_URL"] = "redis://localhost:6379"


@pytest.fixture
def mock_redis() -> Generator[MagicMock, None, None]:
    """Mock Redis connection."""
    with patch("src.queue.get_redis_connection") as mock:
        mock_conn = MagicMock()
        mock_conn.ping.return_value = True
        mock.return_value = mock_conn
        yield mock_conn


@pytest.fixture
def mock_queue(mock_redis: MagicMock) -> Generator[MagicMock, None, None]:
    """Mock RQ queue."""
    with patch("src.queue.Queue") as mock:
        mock_queue = MagicMock()
        mock_queue.__len__ = MagicMock(return_value=0)
        mock_job = MagicMock()
        mock_job.id = "test-job-id"
        mock_queue.enqueue.return_value = mock_job
        mock.return_value = mock_queue
        yield mock_queue


@pytest.fixture
def client(mock_redis: MagicMock, mock_queue: MagicMock) -> TestClient:
    """FastAPI test client with mocked dependencies."""
    from src.main import app

    return TestClient(app)


@pytest.fixture
def sample_ghost_payload() -> dict[str, Any]:
    """Sample Ghost webhook payload for member.added."""
    return {
        "member": {
            "current": {
                "id": "abc123",
                "email": "test@example.com",
                "name": "Test User",
                "status": "free",
                "subscribed": True,
                "created_at": "2025-06-15T10:30:00.000Z",
                "updated_at": "2026-01-02T14:22:00.000Z",
                "labels": [{"name": "VIP", "slug": "vip"}],
            },
            "previous": None,
        }
    }


@pytest.fixture
def sample_ghost_update_payload() -> dict[str, Any]:
    """Sample Ghost webhook payload for member.updated with status change."""
    return {
        "member": {
            "current": {
                "id": "abc123",
                "email": "test@example.com",
                "name": "Test User",
                "status": "paid",
                "subscribed": True,
                "created_at": "2025-06-15T10:30:00.000Z",
                "updated_at": "2026-01-02T14:22:00.000Z",
                "labels": [{"name": "VIP", "slug": "vip"}],
            },
            "previous": {"status": "free"},
        }
    }


@pytest.fixture
def valid_signature() -> str:
    """Generate valid webhook signature for test payload."""
    from src.signature import compute_signature

    import json

    payload = {
        "member": {
            "current": {
                "id": "abc123",
                "email": "test@example.com",
                "name": "Test User",
                "status": "free",
                "subscribed": True,
                "created_at": "2025-06-15T10:30:00.000Z",
                "updated_at": "2026-01-02T14:22:00.000Z",
                "labels": [{"name": "VIP", "slug": "vip"}],
            },
            "previous": None,
        }
    }
    return compute_signature(json.dumps(payload).encode(), "test-secret-key")
