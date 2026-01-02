#!/usr/bin/env python3
"""
Cleanup unsubscribed members from Ghost.

Deletes members from Ghost who are not subscribed to any newsletters.
This helps keep your member list clean and accurate.

Usage:
    python scripts/cleanup_unsubscribed.py --dry-run  # Preview deletions
    python scripts/cleanup_unsubscribed.py            # Execute deletions
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

    def get_unsubscribed_members(self, limit: int = 100) -> list[dict]:
        """
        Fetch all members who are not subscribed to newsletters.

        Args:
            limit: Number of members per page

        Returns:
            List of unsubscribed member dictionaries
        """
        all_members = []
        page = 1

        while True:
            response = self.client.get(
                "/members/",
                params={
                    "filter": "subscribed:false",
                    "limit": limit,
                    "page": page,
                },
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch members: {response.text}")

            data = response.json()
            members = data.get("members", [])

            if not members:
                break

            all_members.extend(members)

            # Check pagination
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("pages", 1):
                break

            page += 1
            print(f"  Fetched {len(all_members)} unsubscribed members so far...")

        return all_members

    def delete_member(self, member_id: str) -> bool:
        """
        Delete a member from Ghost.

        Args:
            member_id: The Ghost member ID

        Returns:
            True if successful
        """
        response = self.client.delete(f"/members/{member_id}/")

        if response.status_code not in (200, 204):
            raise Exception(f"Failed to delete member: {response.text}")

        return True

    def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Delete unsubscribed members from Ghost"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletions without executing them",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of members to delete (0 = all)",
    )
    parser.add_argument(
        "--keep-paid",
        action="store_true",
        help="Keep paid/comped members even if unsubscribed",
    )
    args = parser.parse_args()

    settings = get_settings()

    if not settings.ghost_url or not settings.ghost_admin_api_key:
        print("Error: GHOST_URL and GHOST_ADMIN_API_KEY environment variables are required")
        return 1

    print("Ghost Unsubscribed Member Cleanup")
    print("=" * 40)
    print(f"Ghost URL: {settings.ghost_url}")
    print(f"Dry Run: {args.dry_run}")
    print(f"Keep Paid Members: {args.keep_paid}")
    print()

    # Initialize client
    ghost_client = GhostAdminClient(settings.ghost_url, settings.ghost_admin_api_key)

    try:
        # Fetch unsubscribed members
        print("Fetching unsubscribed members from Ghost...")
        members = ghost_client.get_unsubscribed_members()
        print(f"Found {len(members)} unsubscribed members")

        # Filter out paid/comped if --keep-paid
        if args.keep_paid:
            original_count = len(members)
            members = [m for m in members if m.get("status") == "free"]
            kept = original_count - len(members)
            if kept > 0:
                print(f"Keeping {kept} paid/comped members")
            print(f"Will process {len(members)} free unsubscribed members")

        if not members:
            print("No members to delete.")
            return 0

        # Apply limit
        if args.limit > 0:
            members = members[:args.limit]
            print(f"Limited to {len(members)} members")

        print()

        # Confirm if not dry run
        if not args.dry_run:
            print(f"WARNING: This will permanently delete {len(members)} members from Ghost!")
            print("Type 'yes' to confirm: ", end="")
            confirmation = input().strip().lower()
            if confirmation != "yes":
                print("Aborted.")
                return 1
            print()

        # Process deletions
        results = {
            "deleted": 0,
            "failed": 0,
        }

        print("Processing deletions...")
        for i, member in enumerate(members, 1):
            email = member.get("email", "unknown")
            member_id = member.get("id")

            if not member_id:
                continue

            try:
                if args.dry_run:
                    print(f"  Would delete: {email} (status: {member.get('status')})")
                    results["deleted"] += 1
                else:
                    ghost_client.delete_member(member_id)
                    results["deleted"] += 1
                    print(f"  Deleted: {email}")

            except Exception as e:
                results["failed"] += 1
                print(f"  Failed to delete {email}: {e}")

            # Progress indicator
            if i % 50 == 0:
                print(f"  Processed {i}/{len(members)} members...")

        # Summary
        print()
        print("=" * 40)
        print("Summary:")
        print(f"  Total unsubscribed members found: {len(members)}")
        print(f"  Deleted: {results['deleted']}")
        if results["failed"] > 0:
            print(f"  Failed: {results['failed']}")

        if args.dry_run:
            print()
            print("This was a dry run. No members were deleted.")

        return 0 if results["failed"] == 0 else 1

    finally:
        ghost_client.close()


if __name__ == "__main__":
    sys.exit(main())
