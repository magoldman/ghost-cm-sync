#!/usr/bin/env python3
"""
Restore Ghost members who signed up during the SSRF-blocked window
(default 2026-04-24 to 2026-05-23) to Active CM state + Ghost subscribed=true.

Context: between 2026-04-25 and 2026-05-23, Ghost's outbound webhooks were
blocked by Ghost's own SSRF guard (publishing.totavi.com → 127.0.1.1 via
/etc/hosts), so new Ghost signups never reached the webhook receiver and
never landed in CM. The 2026-05-23 backfill correctly added them. But then
cleanup_backfill_damage.py's heuristic (CM Date == backfill date AND Ghost
created_at < backfill date) couldn't distinguish them from true
pre-existing-opt-outs being wrongly resubscribed, and unsubscribed both
populations.

This script restores the in-window cohort. It force-adds them to CM with
Resubscribe=True (intentionally bypassing the new State-check guard for
this legitimate one-off restoration) and sets Ghost subscribed=true. Use
ONLY for this incident recovery — do not generalize to a routine sync path.

Caveat: anyone who signed up in the window AND legitimately unsubscribed
during the window will also be reactivated. Small population, accepted as
trade-off per user direction.

Usage:
    python scripts/restore_ssrf_window_members.py --site cardsftw --dry-run
    python scripts/restore_ssrf_window_members.py --site cardsftw
    python scripts/restore_ssrf_window_members.py --site productftw \\
        --start 2026-04-24 --end 2026-05-23
"""

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

import httpx
import jwt

from src.config import get_settings, get_site_config, get_site_ids
from src.logging_config import configure_logging, get_logger
from src.validation import (
    sanitize_cm_labels,
    sanitize_cm_name,
    sanitize_email_for_filter,
    truncate_cm_field,
    validate_ghost_url,
    validate_hex_secret,
)

configure_logging()
logger = get_logger(__name__)


class GhostAdminClient:
    def __init__(self, url: str, admin_api_key: str, allow_localhost: bool = False):
        is_valid, error = validate_ghost_url(url, allow_localhost=allow_localhost)
        if not is_valid:
            raise ValueError(f"Invalid Ghost URL: {error}")
        self.url = url.rstrip("/")
        self.admin_api_key = admin_api_key
        self._client: httpx.Client | None = None
        self._token_created_at: float = 0

    def _create_jwt(self) -> str:
        key_parts = self.admin_api_key.split(":")
        if len(key_parts) != 2:
            raise ValueError("Invalid Ghost Admin API key format. Expected 'id:secret'")
        key_id, secret = key_parts
        is_valid, secret_bytes, error = validate_hex_secret(secret)
        if not is_valid or secret_bytes is None:
            raise ValueError(f"Invalid Ghost Admin API secret: {error}")
        now = int(time.time())
        payload = {"iat": now, "exp": now + 300, "aud": "/admin/"}
        return jwt.encode(payload, secret_bytes, algorithm="HS256", headers={"kid": key_id})

    @property
    def client(self) -> httpx.Client:
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
                verify=True,
            )
            self._token_created_at = time.time()
        return self._client

    def list_members_in_window(self, start: date, end: date) -> list[dict]:
        """Fetch all Ghost members with created_at in [start, end)."""
        results: list[dict] = []
        page = 1
        filter_clause = f"created_at:>='{start.isoformat()}'+created_at:<'{end.isoformat()}'"
        while True:
            response = self.client.get(
                "/members/",
                params={
                    "limit": 100,
                    "page": page,
                    "include": "labels",
                    "filter": filter_clause,
                },
            )
            if response.status_code != 200:
                raise RuntimeError(f"Ghost member fetch failed: {response.text}")
            data = response.json()
            members = data.get("members", [])
            if not members:
                break
            results.extend(members)
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("pages", 1):
                break
            page += 1
        return results

    def set_subscribed(self, member_id: str, subscribed: bool) -> None:
        response = self.client.put(
            f"/members/{member_id}/",
            json={"members": [{"subscribed": subscribed}]},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Ghost subscribed update failed: {response.text}")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


def build_cm_payload(member: dict) -> dict:
    """Mirror the same custom-field shape full_sync uses, with Resubscribe=True.

    ghost_email_enabled reflects the post-restoration intent (True), not the
    member's current Ghost subscribed value — at read time it's likely False
    from cleanup_backfill_damage.py, but we're about to set Ghost subscribed=true.
    """
    email = member.get("email") or ""
    name = sanitize_cm_name(member.get("name"))
    status = member.get("status") or "free"
    subscribed = True  # intent: this script restores them to subscribed
    labels = member.get("labels") or []

    # Coerce labels to simple objects with .name for sanitize_cm_labels
    label_objs = [type("L", (), {"name": (lab.get("name") or "")})() for lab in labels]

    custom_fields = [
        {"Key": "ghost_status", "Value": truncate_cm_field("ghost_status", status)},
    ]
    if member.get("created_at"):
        try:
            signup_date = datetime.fromisoformat(member["created_at"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
            custom_fields.append({"Key": "ghost_signup_date",
                                  "Value": truncate_cm_field("ghost_signup_date", signup_date)})
        except ValueError:
            pass
    if member.get("updated_at"):
        try:
            last_updated = datetime.fromisoformat(member["updated_at"].replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%SZ")
            custom_fields.append({"Key": "ghost_last_updated",
                                  "Value": truncate_cm_field("ghost_last_updated", last_updated)})
        except ValueError:
            pass
    custom_fields.append({"Key": "ghost_labels",
                          "Value": sanitize_cm_labels(label_objs)})
    custom_fields.append({"Key": "ghost_email_enabled",
                          "Value": truncate_cm_field("ghost_email_enabled", str(subscribed).lower())})

    return {
        "EmailAddress": email,
        "Name": name,
        "CustomFields": custom_fields,
        "Resubscribe": True,            # intentional: this is the recovery path
        "RestartSubscriptionBasedAutoresponders": False,
        "ConsentToTrack": "Yes",
    }


class CMClient:
    def __init__(self, api_key: str, list_id: str):
        self.api_key = api_key
        self.list_id = list_id
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url="https://api.createsend.com/api/v3.3",
                auth=(self.api_key, ""),
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def force_upsert(self, payload: dict) -> None:
        response = self.client.post(
            f"/subscribers/{self.list_id}.json",
            json=payload,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"CM upsert failed for {payload.get('EmailAddress')}: {response.text}")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore Ghost members from the SSRF-block window (2026-04-25 → 2026-05-22) to CM",
    )
    parser.add_argument("--site", required=True)
    parser.add_argument("--start", default="2026-04-24",
                        help="Start of created_at window (inclusive, YYYY-MM-DD). Default 2026-04-24.")
    parser.add_argument("--end", default="2026-05-23",
                        help="End of created_at window (exclusive, YYYY-MM-DD). Default 2026-05-23 (today's signups excluded).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError as exc:
        print(f"Error: invalid date — {exc}")
        return 1

    if start >= end:
        print(f"Error: --start ({start}) must be before --end ({end})")
        return 1

    site = get_site_config(args.site)
    if site is None:
        print(f"Error: unknown site '{args.site}'. Configured: {get_site_ids()}")
        return 1
    if not site.ghost_url or not site.ghost_admin_api_key:
        print(f"Error: Ghost credentials not configured for site '{args.site}'")
        return 1

    settings = get_settings()
    ghost = GhostAdminClient(site.ghost_url, site.ghost_admin_api_key)
    cm = CMClient(settings.cm_api_key, site.cm_list_id)

    print(f"Restore SSRF-window members — site={args.site}")
    print(f"  Window: [{start}, {end})")
    print(f"  Dry run: {args.dry_run}")
    print("=" * 60)

    try:
        print()
        print("Fetching Ghost members in window...")
        members = ghost.list_members_in_window(start, end)
        print(f"  Found {len(members)} Ghost member(s) created in window")

        cm_restored = 0
        cm_failed = 0
        ghost_resubscribed = 0
        ghost_failed = 0
        skipped_no_email = 0

        for i, member in enumerate(members, 1):
            email = member.get("email")
            if not email:
                skipped_no_email += 1
                continue

            payload = build_cm_payload(member)

            if args.dry_run:
                if args.verbose:
                    sub_state = "subscribed" if member.get("subscribed") else "unsubscribed"
                    print(f"  Would restore: {email} (Ghost: {sub_state}, created {member.get('created_at', '')[:10]})")
                cm_restored += 1
                if not member.get("subscribed", True):
                    ghost_resubscribed += 1
                continue

            # CM: force-add with Resubscribe=True
            try:
                cm.force_upsert(payload)
                cm_restored += 1
                if args.verbose:
                    print(f"  ✓ CM restored: {email}")
            except Exception as exc:
                cm_failed += 1
                print(f"  ✗ CM failed: {email} - {exc}")
                continue

            # Ghost: restore subscribed=true if currently false
            if not member.get("subscribed", True):
                try:
                    ghost.set_subscribed(member["id"], True)
                    ghost_resubscribed += 1
                    if args.verbose:
                        print(f"  ✓ Ghost subscribed=true: {email}")
                except Exception as exc:
                    ghost_failed += 1
                    print(f"  ✗ Ghost failed: {email} - {exc}")

            if i % 50 == 0 and not args.verbose:
                print(f"  Processed {i}/{len(members)}...")

        print()
        print("=" * 60)
        print(f"Summary for {args.site}:")
        print(f"  Members in window:            {len(members)}")
        print(f"  Skipped (no email):           {skipped_no_email}")
        print(f"  CM restored:                  {cm_restored}")
        print(f"  Ghost subscribed=true (was false): {ghost_resubscribed}")
        if cm_failed:
            print(f"  CM failures:                  {cm_failed}")
        if ghost_failed:
            print(f"  Ghost failures:               {ghost_failed}")
        if args.dry_run:
            print()
            print("Dry run. No changes were made.")
        return 0 if (cm_failed + ghost_failed) == 0 else 1

    finally:
        ghost.close()
        cm.close()


if __name__ == "__main__":
    sys.exit(main())
