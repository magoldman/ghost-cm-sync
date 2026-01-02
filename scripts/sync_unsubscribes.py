#!/usr/bin/env python3
"""
Sync unsubscribes from Campaign Monitor back to Ghost.

Fetches unsubscribed members from Campaign Monitor and marks them
as unsubscribed in Ghost if they're currently subscribed.

Usage:
    python scripts/sync_unsubscribes.py --dry-run  # Preview changes
    python scripts/sync_unsubscribes.py            # Execute sync
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import jwt

from src.config import get_settings
from src.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


class GhostAdminClient:
    """Ghost Admin API client for managing members."""

    def __init__(self, url: str, admin_api_key: str):
        self.url = url.rstrip("/")
        self.admin_api_key = admin_api_key
        self._client: httpx.Client | None = None
        self._token_created_at: float = 0

    def _create_jwt(self) -> str:
        """Create JWT token for Ghost Admin API."""
        key_parts = self.admin_api_key.split(":")
        if len(key_parts) != 2:
            raise ValueError("Invalid Ghost Admin API key format. Expected 'id:secret'")

        key_id, secret = key_parts
        secret_bytes = bytes.fromhex(secret)

        now = int(time.time())
        payload = {
            "iat": now,
            "exp": now + 300,  # 5 minutes
            "aud": "/admin/",
        }

        return jwt.encode(payload, secret_bytes, algorithm="HS256", headers={"kid": key_id})

    @property
    def client(self) -> httpx.Client:
        """Get HTTP client with JWT auth, refreshing token if needed."""
        # Refresh token every 4 minutes
        if self._client is None or (time.time() - self._token_created_at) > 240:
            if self._client:
                self._client.close()
            token = self._create_jwt()
            self._client = httpx.Client(
                base_url=f"{self.url}/ghost/api/admin",
                headers={
                    "Authorization": f"Ghost {token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            self._token_created_at = time.time()
        return self._client

    def get_member_by_email(self, email: str) -> dict | None:
        """Fetch a member by email."""
        response = self.client.get(
            "/members/",
            params={"filter": f"email:'{email}'", "limit": 1},
        )

        if response.status_code != 200:
            raise Exception(f"Failed to fetch member: {response.text}")

        data = response.json()
        members = data.get("members", [])
        return members[0] if members else None

    def unsubscribe_member(self, member_id: str) -> bool:
        """Set a member's subscribed status to False."""
        response = self.client.put(
            f"/members/{member_id}/",
            json={"members": [{"subscribed": False}]},
        )

        if response.status_code != 200:
            raise Exception(f"Failed to unsubscribe member: {response.text}")

        return True

    def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


class CampaignMonitorClient:
    """Campaign Monitor API client for fetching unsubscribes."""

    def __init__(self, api_key: str, list_id: str):
        self.api_key = api_key
        self.list_id = list_id
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Get HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url="https://api.createsend.com/api/v3.3",
                auth=(self.api_key, ""),
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def get_unsubscribed(self, since_date: str = "2000-01-01") -> list[dict]:
        """
        Fetch all unsubscribed members from Campaign Monitor.

        Args:
            since_date: Fetch unsubscribes since this date (YYYY-MM-DD)

        Returns:
            List of unsubscribed subscriber dicts
        """
        all_unsubscribed = []
        page = 1
        page_size = 1000

        while True:
            response = self.client.get(
                f"/lists/{self.list_id}/unsubscribed.json",
                params={
                    "date": since_date,
                    "page": page,
                    "pagesize": page_size,
                    "orderfield": "date",
                    "orderdirection": "desc",
                },
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch unsubscribes: {response.text}")

            data = response.json()
            results = data.get("Results", [])

            if not results:
                break

            all_unsubscribed.extend(results)

            # Check if there are more pages
            total_items = data.get("TotalNumberOfRecords", 0)
            if len(all_unsubscribed) >= total_items:
                break

            page += 1
            print(f"  Fetched {len(all_unsubscribed)} unsubscribes so far...")

        return all_unsubscribed

    def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync unsubscribes from Campaign Monitor to Ghost"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--since",
        type=str,
        default="2000-01-01",
        help="Only process unsubscribes since this date (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    settings = get_settings()

    if not settings.ghost_url or not settings.ghost_admin_api_key:
        print("Error: GHOST_URL and GHOST_ADMIN_API_KEY environment variables are required")
        return 1

    print("Campaign Monitor → Ghost Unsubscribe Sync")
    print("=" * 45)
    print(f"Ghost URL: {settings.ghost_url}")
    print(f"CM List ID: {settings.cm_list_id}")
    print(f"Since: {args.since}")
    print(f"Dry Run: {args.dry_run}")
    print()

    # Initialize clients
    cm_client = CampaignMonitorClient(settings.cm_api_key, settings.cm_list_id)
    ghost_client = GhostAdminClient(settings.ghost_url, settings.ghost_admin_api_key)

    try:
        # Fetch unsubscribes from Campaign Monitor
        print("Fetching unsubscribed members from Campaign Monitor...")
        cm_unsubscribed = cm_client.get_unsubscribed(since_date=args.since)
        print(f"Found {len(cm_unsubscribed)} unsubscribed members in Campaign Monitor")
        print()

        if not cm_unsubscribed:
            print("No unsubscribes to process.")
            return 0

        # Process each unsubscribe
        results = {
            "unsubscribed": 0,
            "already_unsubscribed": 0,
            "not_in_ghost": 0,
            "failed": 0,
        }

        print("Processing unsubscribes...")
        for i, cm_member in enumerate(cm_unsubscribed, 1):
            email = cm_member.get("EmailAddress")
            if not email:
                continue

            try:
                # Look up member in Ghost
                ghost_member = ghost_client.get_member_by_email(email)

                if ghost_member is None:
                    results["not_in_ghost"] += 1
                    continue

                # Check if already unsubscribed in Ghost
                if not ghost_member.get("subscribed", True):
                    results["already_unsubscribed"] += 1
                    continue

                # Unsubscribe in Ghost
                if args.dry_run:
                    print(f"  Would unsubscribe: {email}")
                    results["unsubscribed"] += 1
                else:
                    ghost_client.unsubscribe_member(ghost_member["id"])
                    results["unsubscribed"] += 1
                    print(f"  Unsubscribed: {email}")

            except Exception as e:
                results["failed"] += 1
                print(f"  Error processing {email}: {e}")

            # Progress indicator
            if i % 50 == 0:
                print(f"  Processed {i}/{len(cm_unsubscribed)} members...")

        # Summary
        print()
        print("=" * 45)
        print("Summary:")
        print(f"  Total CM unsubscribes checked: {len(cm_unsubscribed)}")
        print(f"  Unsubscribed in Ghost: {results['unsubscribed']}")
        print(f"  Already unsubscribed in Ghost: {results['already_unsubscribed']}")
        print(f"  Not found in Ghost: {results['not_in_ghost']}")
        if results["failed"] > 0:
            print(f"  Failed: {results['failed']}")

        if args.dry_run:
            print()
            print("This was a dry run. No changes were made.")

        return 0 if results["failed"] == 0 else 1

    finally:
        cm_client.close()
        ghost_client.close()


if __name__ == "__main__":
    sys.exit(main())
