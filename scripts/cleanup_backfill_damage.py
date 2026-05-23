#!/usr/bin/env python3
"""
One-off cleanup for the 2026-05-23 Resubscribe=True backfill bug.

Runs two phases against a configured site, in dry-run-by-default mode for the
first pass:

Phase 1 — sync currently-Bounced CM subscribers to Ghost
    Fetches /lists/{listid}/bounced.json with date >= backfill date. These are
    subscribers who entered Bounced state on or after the backfill (in practice,
    people we reactivated who then bounced on the welcome email). For each, marks
    Ghost subscribed=false so the two sides agree.

Phase 2 — find wrongly-reactivated active CM subscribers
    Fetches /lists/{listid}/active.json with date >= backfill date and filters
    to subscribers with Date == backfill date exactly. Looks each up in Ghost;
    if the Ghost member's created_at predates the backfill, they're not a fresh
    signup — they were reactivated by the buggy backfill. Unsubscribes them in
    CM and marks Ghost subscribed=false.

Usage:
    python scripts/cleanup_backfill_damage.py --site cardsftw --dry-run
    python scripts/cleanup_backfill_damage.py --site cardsftw
    python scripts/cleanup_backfill_damage.py --site productftw --date 2026-05-23
"""

import argparse
import sys
import time
from datetime import date, datetime
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
    sanitize_email_for_filter,
    validate_ghost_url,
    validate_hex_secret,
)

configure_logging()
logger = get_logger(__name__)


class GhostAdminClient:
    """Ghost Admin API client with JWT auth and TLS verification."""

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

    def get_member_by_email(self, email: str) -> dict | None:
        is_valid, sanitized_email, _ = sanitize_email_for_filter(email)
        if not is_valid or sanitized_email is None:
            return None
        response = self.client.get(
            "/members/",
            params={"filter": f"email:'{sanitized_email}'", "limit": 1},
        )
        if response.status_code != 200:
            return None
        members = response.json().get("members", [])
        return members[0] if members else None

    def unsubscribe_member(self, member_id: str) -> None:
        response = self.client.put(
            f"/members/{member_id}/",
            json={"members": [{"subscribed": False}]},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Failed to unsubscribe Ghost member: {response.text}")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


class CMClient:
    """Minimal CM client for paginated list reads and unsubscribe."""

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

    def list_subscribers(self, endpoint: str, since: date) -> list[dict]:
        """Paginate through /lists/{id}/{endpoint}.json returning subscribers with date >= since."""
        results: list[dict] = []
        page = 1
        while True:
            response = self.client.get(
                f"/lists/{self.list_id}/{endpoint}.json",
                params={
                    "date": since.strftime("%Y-%m-%d"),
                    "page": page,
                    "pagesize": 1000,
                    "orderfield": "date",
                    "orderdirection": "asc",
                },
            )
            if response.status_code != 200:
                raise RuntimeError(f"CM {endpoint} fetch failed: {response.text}")
            data = response.json()
            batch = data.get("Results", [])
            if not batch:
                break
            results.extend(batch)
            total = data.get("TotalNumberOfRecords", 0)
            if len(results) >= total:
                break
            page += 1
        return results

    def unsubscribe(self, email: str) -> None:
        response = self.client.post(
            f"/subscribers/{self.list_id}/unsubscribe.json",
            json={"EmailAddress": email},
        )
        if response.status_code in (200, 201):
            return
        # CM Code 203 means "subscriber not in list / already unsubscribed" — idempotent.
        if response.status_code == 400:
            try:
                if response.json().get("Code") == 203:
                    return
            except Exception:
                pass
        raise RuntimeError(f"CM unsubscribe failed for {email}: {response.text}")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


def parse_cm_date(value: str) -> date | None:
    """CM returns 'YYYY-MM-DD HH:MM:SS'; we only need the date."""
    if not value:
        return None
    try:
        return datetime.strptime(value.split(" ")[0], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_ghost_created(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up wrongly-reactivated subscribers from the 2026-05-23 backfill",
    )
    parser.add_argument("--site", required=True, help="Site identifier (e.g., cardsftw)")
    parser.add_argument(
        "--date",
        default="2026-05-23",
        help="Backfill date YYYY-MM-DD (default: 2026-05-23)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    args = parser.parse_args()

    try:
        backfill_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        print(f"Error: invalid --date '{args.date}', expected YYYY-MM-DD")
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

    print(f"Cleanup backfill damage — site={args.site} backfill_date={backfill_date} dry_run={args.dry_run}")
    print("=" * 60)

    try:
        # -------- Phase 1: Bounced since backfill_date --------
        print()
        print("Phase 1: Sync currently-Bounced CM subscribers to Ghost")
        bounced = cm.list_subscribers("bounced", backfill_date)
        print(f"  Found {len(bounced)} bounced subscribers since {backfill_date}")
        p1_synced = p1_already = p1_no_ghost = p1_failed = 0

        for sub in bounced:
            email = sub.get("EmailAddress", "")
            if not email:
                continue
            member = ghost.get_member_by_email(email)
            if not member:
                p1_no_ghost += 1
                continue
            if not member.get("subscribed", True):
                p1_already += 1
                continue
            if args.dry_run:
                print(f"  ⊘ would unsubscribe in Ghost: {email}")
                p1_synced += 1
            else:
                try:
                    ghost.unsubscribe_member(member["id"])
                    print(f"  ⊘ {email}")
                    p1_synced += 1
                except Exception as exc:
                    print(f"  ✗ Ghost unsubscribe failed for {email}: {exc}")
                    p1_failed += 1

        # -------- Phase 2: Active with Date == backfill_date AND Ghost predates --------
        print()
        print(f"Phase 2: Find wrongly-reactivated active subscribers (Date == {backfill_date})")
        active = cm.list_subscribers("active", backfill_date)
        print(f"  Found {len(active)} active subscribers since {backfill_date}")
        candidates = [s for s in active if parse_cm_date(s.get("Date", "")) == backfill_date]
        print(f"  Of those, {len(candidates)} added exactly on {backfill_date}")

        p2_cleaned = p2_fresh = p2_no_ghost = p2_failed = 0

        for sub in candidates:
            email = sub.get("EmailAddress", "")
            if not email:
                continue
            member = ghost.get_member_by_email(email)
            if not member:
                p2_no_ghost += 1
                continue
            ghost_created = parse_ghost_created(member.get("created_at", ""))
            if ghost_created is None or ghost_created >= backfill_date:
                p2_fresh += 1
                continue
            # Wrongly reactivated: Ghost record predates the backfill.
            if args.dry_run:
                print(f"  ✗ would clean: {email} (Ghost created {ghost_created})")
                p2_cleaned += 1
                continue
            try:
                cm.unsubscribe(email)
            except Exception as exc:
                print(f"  ✗ CM unsubscribe failed for {email}: {exc}")
                p2_failed += 1
                continue
            if member.get("subscribed", True):
                try:
                    ghost.unsubscribe_member(member["id"])
                except Exception as exc:
                    print(f"  ⚠ CM unsubscribed but Ghost failed for {email}: {exc}")
                    p2_failed += 1
                    continue
            print(f"  ✗ cleaned: {email}")
            p2_cleaned += 1

        # -------- Summary --------
        print()
        print("=" * 60)
        print(f"Summary for {args.site}:")
        print()
        print("Phase 1 — Ghost ←mirror CM Bounced:")
        print(f"  Marked unsubscribed in Ghost:    {p1_synced}")
        print(f"  Already unsubscribed in Ghost:   {p1_already}")
        print(f"  Not found in Ghost:              {p1_no_ghost}")
        if p1_failed:
            print(f"  Failed:                          {p1_failed}")
        print()
        print("Phase 2 — Wrongly-reactivated cleanup:")
        print(f"  Cleaned (unsub CM + Ghost):      {p2_cleaned}")
        print(f"  Fresh signups (left alone):      {p2_fresh}")
        print(f"  Not found in Ghost:              {p2_no_ghost}")
        if p2_failed:
            print(f"  Failed:                          {p2_failed}")
        if args.dry_run:
            print()
            print("Dry run. No changes were made.")
        return 0 if (p1_failed + p2_failed) == 0 else 1

    finally:
        ghost.close()
        cm.close()


if __name__ == "__main__":
    sys.exit(main())
