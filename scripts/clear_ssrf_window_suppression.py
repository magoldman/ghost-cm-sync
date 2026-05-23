#!/usr/bin/env python3
"""
Clear Ghost email suppression for members who signed up during the SSRF
window (default 2026-04-24 → 2026-05-23).

Some of these members have `email_disabled` or `email_suppression.suppressed`
set in Ghost — typically from bouncing on a Ghost-sent email at some point —
which prevents Ghost from sending newsletters to them. This script removes
the suppression flag so Ghost can deliver to them again.

Pairs with `restore_ssrf_window_members.py`: that script ensured these
members are Active in CM and Ghost subscribed=true; this script unblocks
the Ghost delivery path for the same population.

Caveat: clearing suppression on a member with a genuinely dead address
will just produce another bounce. Ghost will re-flag them eventually.

Usage:
    python scripts/clear_ssrf_window_suppression.py --site cardsftw --dry-run
    python scripts/clear_ssrf_window_suppression.py --site cardsftw
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

from src.config import get_site_config, get_site_ids
from src.logging_config import configure_logging, get_logger
from src.validation import validate_ghost_url, validate_hex_secret

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
        results: list[dict] = []
        page = 1
        filter_clause = f"created_at:>='{start.isoformat()}'+created_at:<'{end.isoformat()}'"
        while True:
            response = self.client.get(
                "/members/",
                params={"limit": 100, "page": page, "filter": filter_clause},
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

    def clear_suppression(self, member_id: str) -> None:
        response = self.client.delete(f"/members/{member_id}/suppression/")
        if response.status_code not in (200, 204):
            raise RuntimeError(f"DELETE /members/{member_id}/suppression failed "
                               f"({response.status_code}): {response.text}")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


def is_suppressed(member: dict) -> tuple[bool, str]:
    """Return (suppressed, reason) — reason describes which flag tripped."""
    if member.get("email_disabled"):
        return True, "email_disabled"
    suppression = member.get("email_suppression") or {}
    if isinstance(suppression, dict) and suppression.get("suppressed"):
        info = suppression.get("info") or {}
        reason = info.get("reason") if isinstance(info, dict) else None
        return True, f"email_suppression ({reason or 'unknown'})"
    return False, ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear Ghost email suppression for SSRF-window members",
    )
    parser.add_argument("--site", required=True)
    parser.add_argument("--start", default="2026-04-24")
    parser.add_argument("--end", default="2026-05-23")
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

    ghost = GhostAdminClient(site.ghost_url, site.ghost_admin_api_key)

    print(f"Clear SSRF-window suppression — site={args.site}")
    print(f"  Window: [{start}, {end})")
    print(f"  Dry run: {args.dry_run}")
    print("=" * 60)

    try:
        print()
        print("Fetching Ghost members in window...")
        members = ghost.list_members_in_window(start, end)
        print(f"  Found {len(members)} member(s) in window")

        suppressed = [(m, is_suppressed(m)) for m in members]
        affected = [(m, reason) for m, (flag, reason) in suppressed if flag]
        print(f"  Suppressed: {len(affected)}")
        print()

        cleared = 0
        failed = 0

        for member, reason in affected:
            email = member.get("email", "<no-email>")
            member_id = member.get("id")
            if not member_id:
                continue

            if args.dry_run:
                if args.verbose:
                    print(f"  Would clear: {email} ({reason})")
                cleared += 1
                continue

            try:
                ghost.clear_suppression(member_id)
                cleared += 1
                if args.verbose:
                    print(f"  ✓ {email} ({reason})")
            except Exception as exc:
                failed += 1
                print(f"  ✗ {email}: {exc}")

        print()
        print("=" * 60)
        print(f"Summary for {args.site}:")
        print(f"  Members in window:       {len(members)}")
        print(f"  Suppressed:              {len(affected)}")
        print(f"  Cleared:                 {cleared}")
        if failed:
            print(f"  Failed:                  {failed}")
        if args.dry_run:
            print()
            print("Dry run. No changes were made.")
        return 0 if failed == 0 else 1

    finally:
        ghost.close()


if __name__ == "__main__":
    sys.exit(main())
