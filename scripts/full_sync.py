#!/usr/bin/env python3
"""
Full sync script for Ghost → Campaign Monitor.

Pulls all Ghost members and syncs them to Campaign Monitor.
Used for initial migration and recovery from data drift.

Usage:
    python scripts/full_sync.py --dry-run  # Preview changes
    python scripts/full_sync.py            # Execute sync
"""

import argparse
import hashlib
import hmac
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import jwt

from src.campaign_monitor import CampaignMonitorError, CircuitBreakerOpen, get_cm_client
from src.config import get_settings
from src.logging_config import configure_logging, get_logger, hash_email
from src.models import GhostLabel, GhostMemberData

configure_logging()
logger = get_logger(__name__)


class GhostAPIClient:
    """Ghost Admin API client for fetching members."""

    def __init__(self, url: str, admin_api_key: str):
        self.url = url.rstrip("/")
        self.admin_api_key = admin_api_key
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Get HTTP client with JWT auth."""
        if self._client is None:
            token = self._create_jwt()
            self._client = httpx.Client(
                base_url=f"{self.url}/ghost/api/admin",
                headers={
                    "Authorization": f"Ghost {token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        return self._client

    def _create_jwt(self) -> str:
        """Create JWT token for Ghost Admin API."""
        # Ghost Admin API key format: id:secret
        key_parts = self.admin_api_key.split(":")
        if len(key_parts) != 2:
            raise ValueError("Invalid Ghost Admin API key format. Expected 'id:secret'")

        key_id, secret = key_parts

        # Decode the secret from hex
        secret_bytes = bytes.fromhex(secret)

        # Create JWT payload
        now = int(time.time())
        payload = {
            "iat": now,
            "exp": now + 300,  # 5 minutes
            "aud": "/admin/",
        }

        # Create JWT with HS256
        token = jwt.encode(payload, secret_bytes, algorithm="HS256", headers={"kid": key_id})

        return token

    def get_members(self, limit: int = 100) -> list[dict]:
        """
        Fetch all members from Ghost with pagination.

        Args:
            limit: Number of members per page

        Returns:
            List of all member dictionaries
        """
        all_members = []
        page = 1

        while True:
            response = self.client.get(
                "/members/",
                params={
                    "limit": limit,
                    "page": page,
                    "include": "labels",
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
            logger.info("fetching_members", page=page, total_so_far=len(all_members))

        return all_members

    def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def parse_ghost_member(member_dict: dict) -> GhostMemberData:
    """Convert Ghost API response to GhostMemberData model."""
    labels = [
        GhostLabel(name=label["name"], slug=label["slug"])
        for label in member_dict.get("labels", [])
    ]

    return GhostMemberData(
        id=member_dict["id"],
        email=member_dict["email"],
        name=member_dict.get("name"),
        status=member_dict.get("status", "free"),
        subscribed=member_dict.get("subscribed", True),
        created_at=datetime.fromisoformat(member_dict["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(member_dict["updated_at"].replace("Z", "+00:00")),
        labels=labels,
    )


def sync_member(member: GhostMemberData, dry_run: bool = False) -> dict:
    """
    Sync a single member to Campaign Monitor.

    Args:
        member: Ghost member data
        dry_run: If True, don't actually sync

    Returns:
        Sync result dictionary
    """
    cm_client = get_cm_client()

    if dry_run:
        return {
            "email": member.email,
            "status": member.status,
            "action": "would_sync",
            "dry_run": True,
        }

    try:
        # Check existing subscriber for status change detection
        existing = cm_client.get_subscriber(member.email)

        previous_status = None
        status_changed = False

        if existing:
            # Extract current ghost_status
            for field in existing.get("CustomFields", []):
                if field.get("Key") == "ghost_status":
                    if field.get("Value") != member.status:
                        previous_status = field.get("Value")
                        status_changed = True
                    break

        status_changed_at = datetime.now(timezone.utc) if status_changed else None

        cm_client.add_or_update_subscriber(
            member=member,
            previous_status=previous_status if status_changed else None,
            status_changed_at=status_changed_at,
        )

        return {
            "email": member.email,
            "status": member.status,
            "action": "synced",
            "status_changed": status_changed,
            "previous_status": previous_status,
        }

    except CampaignMonitorError as e:
        return {
            "email": member.email,
            "status": member.status,
            "action": "failed",
            "error": str(e),
        }


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync all Ghost members to Campaign Monitor"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of members to sync (0 = all)",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include members with disabled emails (bounced/failed delivery)",
    )
    args = parser.parse_args()

    settings = get_settings()

    if not settings.ghost_url or not settings.ghost_admin_api_key:
        logger.error("missing_ghost_config", message="GHOST_URL and GHOST_ADMIN_API_KEY required")
        print("Error: GHOST_URL and GHOST_ADMIN_API_KEY environment variables are required")
        return 1

    print(f"Ghost → Campaign Monitor Full Sync")
    print(f"{'=' * 40}")
    print(f"Ghost URL: {settings.ghost_url}")
    print(f"CM List ID: {settings.cm_list_id}")
    print(f"Dry Run: {args.dry_run}")
    print(f"Include Disabled Emails: {args.include_disabled}")
    print()

    # Initialize Ghost client
    ghost_client = GhostAPIClient(settings.ghost_url, settings.ghost_admin_api_key)

    try:
        # Fetch all members
        print("Fetching members from Ghost...")
        members = ghost_client.get_members()
        print(f"Found {len(members)} total members")

        # Separate members into active and disabled
        active_members = []
        disabled_members = []
        for m in members:
            # Check if email is disabled or suppressed
            is_disabled = m.get("email_disabled", False)
            suppression = m.get("email_suppression") or {}
            is_suppressed = suppression.get("suppressed", False) if isinstance(suppression, dict) else False

            if is_disabled or is_suppressed:
                disabled_members.append(m)
            else:
                active_members.append(m)

        if disabled_members:
            print(f"Found {len(disabled_members)} members with disabled/suppressed emails")
        print(f"Found {len(active_members)} members with valid emails")

        if args.limit > 0:
            active_members = active_members[: args.limit]
            print(f"Limited to {len(active_members)} active members")

        # Sync results
        results = {
            "synced": 0,
            "failed": 0,
            "status_changes": 0,
            "unsubscribed": 0,
            "skipped_not_in_list": 0,
        }

        cm_client = get_cm_client()

        # Step 1: Sync active members (add or update)
        print()
        print("Step 1: Syncing active members to Campaign Monitor...")

        for i, member_dict in enumerate(active_members, 1):
            member = parse_ghost_member(member_dict)
            result = sync_member(member, dry_run=args.dry_run)

            if result["action"] == "synced" or result["action"] == "would_sync":
                results["synced"] += 1
                if result.get("status_changed"):
                    results["status_changes"] += 1
            else:
                results["failed"] += 1
                print(f"  Failed: {member.email} - {result.get('error')}")

            # Progress indicator
            if i % 50 == 0:
                print(f"  Processed {i}/{len(active_members)} active members...")

        # Step 2: Unsubscribe disabled members (only if they exist in CM)
        if disabled_members:
            print()
            print("Step 2: Processing disabled members...")
            print(f"  Checking {len(disabled_members)} disabled members against Campaign Monitor...")

            for i, member_dict in enumerate(disabled_members, 1):
                email = member_dict.get("email")
                if not email:
                    continue

                if args.dry_run:
                    # In dry run, we'd check if they exist
                    results["unsubscribed"] += 1
                else:
                    try:
                        # First check if subscriber exists in CM
                        existing = cm_client.get_subscriber(email)

                        if existing is None:
                            # Not in list, skip
                            results["skipped_not_in_list"] += 1
                        else:
                            # Exists in CM, unsubscribe them
                            cm_client.unsubscribe(email)
                            results["unsubscribed"] += 1

                    except CampaignMonitorError as e:
                        # Log but don't count as failure for "not in list" type errors
                        error_str = str(e).lower()
                        if "203" in str(e) or "not in list" in error_str:
                            results["skipped_not_in_list"] += 1
                        else:
                            print(f"  Error processing {email}: {e}")
                    except CircuitBreakerOpen:
                        print(f"  Circuit breaker open - waiting 10 seconds...")
                        time.sleep(10)
                        cm_client._circuit_open_until = None
                        cm_client._failure_count = 0

                if i % 50 == 0:
                    print(f"  Processed {i}/{len(disabled_members)} disabled members...")

        # Summary
        print()
        print(f"{'=' * 40}")
        print("Summary:")
        print(f"  Total members in Ghost: {len(members)}")
        print()
        print("Active members:")
        print(f"  Synced: {results['synced']}")
        print(f"  Failed: {results['failed']}")
        print(f"  Status changes detected: {results['status_changes']}")
        print()
        print("Disabled members:")
        print(f"  Unsubscribed from CM: {results['unsubscribed']}")
        print(f"  Skipped (not in CM): {results['skipped_not_in_list']}")

        if args.dry_run:
            print()
            print("This was a dry run. No changes were made.")

        return 0 if results["failed"] == 0 else 1

    finally:
        ghost_client.close()


if __name__ == "__main__":
    sys.exit(main())
