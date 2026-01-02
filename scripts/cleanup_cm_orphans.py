#!/usr/bin/env python3
"""
Cleanup orphaned subscribers from Campaign Monitor.

Removes subscribers from Campaign Monitor who don't exist in Ghost.
Ghost is the source of truth.

Usage:
    python scripts/cleanup_cm_orphans.py --dry-run  # Preview removals
    python scripts/cleanup_cm_orphans.py            # Execute removals
"""

import argparse
import sys
import time
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
    """Ghost Admin API client for fetching members."""

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
            "exp": now + 300,
            "aud": "/admin/",
        }

        return jwt.encode(payload, secret_bytes, algorithm="HS256", headers={"kid": key_id})

    @property
    def client(self) -> httpx.Client:
        """Get HTTP client with JWT auth, refreshing token if needed."""
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

    def get_all_member_emails(self, limit: int = 100) -> set[str]:
        """
        Fetch all member emails from Ghost.

        Returns:
            Set of lowercase email addresses
        """
        all_emails = set()
        page = 1

        while True:
            response = self.client.get(
                "/members/",
                params={
                    "limit": limit,
                    "page": page,
                    "fields": "email",
                },
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch members: {response.text}")

            data = response.json()
            members = data.get("members", [])

            if not members:
                break

            for m in members:
                email = m.get("email")
                if email:
                    all_emails.add(email.lower())

            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("pages", 1):
                break

            page += 1
            if page % 10 == 0:
                print(f"  Fetched {len(all_emails)} Ghost emails so far...")

        return all_emails

    def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


class CampaignMonitorClient:
    """Campaign Monitor API client."""

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

    def get_active_subscribers(self) -> list[dict]:
        """
        Fetch all active subscribers from Campaign Monitor.

        Returns:
            List of subscriber dicts
        """
        all_subscribers = []
        page = 1
        page_size = 1000

        while True:
            response = self.client.get(
                f"/lists/{self.list_id}/active.json",
                params={
                    "page": page,
                    "pagesize": page_size,
                    "orderfield": "email",
                    "orderdirection": "asc",
                },
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch subscribers: {response.text}")

            data = response.json()
            results = data.get("Results", [])

            if not results:
                break

            all_subscribers.extend(results)

            total_items = data.get("TotalNumberOfRecords", 0)
            if len(all_subscribers) >= total_items:
                break

            page += 1
            print(f"  Fetched {len(all_subscribers)} CM subscribers so far...")

        return all_subscribers

    def unsubscribe(self, email: str) -> bool:
        """Unsubscribe a subscriber."""
        response = self.client.post(
            f"/subscribers/{self.list_id}/unsubscribe.json",
            json={"EmailAddress": email},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Failed to unsubscribe: {response.text}")

        return True

    def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Remove Campaign Monitor subscribers who don't exist in Ghost"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview removals without executing them",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of subscribers to remove (0 = all)",
    )
    args = parser.parse_args()

    settings = get_settings()

    if not settings.ghost_url or not settings.ghost_admin_api_key:
        print("Error: GHOST_URL and GHOST_ADMIN_API_KEY environment variables are required")
        return 1

    print("Campaign Monitor Orphan Cleanup")
    print("=" * 40)
    print(f"Ghost URL: {settings.ghost_url}")
    print(f"CM List ID: {settings.cm_list_id}")
    print(f"Dry Run: {args.dry_run}")
    print()

    ghost_client = GhostAdminClient(settings.ghost_url, settings.ghost_admin_api_key)
    cm_client = CampaignMonitorClient(settings.cm_api_key, settings.cm_list_id)

    try:
        # Step 1: Get all Ghost emails
        print("Step 1: Fetching all member emails from Ghost...")
        ghost_emails = ghost_client.get_all_member_emails()
        print(f"Found {len(ghost_emails)} members in Ghost")
        print()

        # Step 2: Get all CM active subscribers
        print("Step 2: Fetching all active subscribers from Campaign Monitor...")
        cm_subscribers = cm_client.get_active_subscribers()
        print(f"Found {len(cm_subscribers)} active subscribers in Campaign Monitor")
        print()

        # Step 3: Find orphans (in CM but not in Ghost)
        print("Step 3: Finding orphaned subscribers...")
        orphans = []
        for sub in cm_subscribers:
            email = sub.get("EmailAddress", "").lower()
            if email and email not in ghost_emails:
                orphans.append(sub)

        print(f"Found {len(orphans)} subscribers in CM that don't exist in Ghost")

        if not orphans:
            print("No orphans to remove.")
            return 0

        # Apply limit
        if args.limit > 0:
            orphans = orphans[:args.limit]
            print(f"Limited to {len(orphans)} subscribers")

        print()

        # Confirm if not dry run
        if not args.dry_run:
            print(f"WARNING: This will unsubscribe {len(orphans)} subscribers from Campaign Monitor!")
            print("Type 'yes' to confirm: ", end="")
            confirmation = input().strip().lower()
            if confirmation != "yes":
                print("Aborted.")
                return 1
            print()

        # Step 4: Remove orphans
        results = {
            "removed": 0,
            "failed": 0,
        }

        print("Step 4: Removing orphaned subscribers...")
        for i, sub in enumerate(orphans, 1):
            email = sub.get("EmailAddress")

            try:
                if args.dry_run:
                    print(f"  Would remove: {email}")
                    results["removed"] += 1
                else:
                    cm_client.unsubscribe(email)
                    results["removed"] += 1

            except Exception as e:
                results["failed"] += 1
                print(f"  Failed to remove {email}: {e}")

            if i % 50 == 0:
                print(f"  Processed {i}/{len(orphans)} orphans...")

        # Summary
        print()
        print("=" * 40)
        print("Summary:")
        print(f"  Ghost members: {len(ghost_emails)}")
        print(f"  CM subscribers: {len(cm_subscribers)}")
        print(f"  Orphans found: {len(orphans)}")
        print(f"  Removed from CM: {results['removed']}")
        if results["failed"] > 0:
            print(f"  Failed: {results['failed']}")

        if args.dry_run:
            print()
            print("This was a dry run. No subscribers were removed.")

        return 0 if results["failed"] == 0 else 1

    finally:
        ghost_client.close()
        cm_client.close()


if __name__ == "__main__":
    sys.exit(main())
