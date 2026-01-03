"""Campaign Monitor API client with connection pooling and retry logic."""

import time
from datetime import datetime
from typing import Any

import httpx

from src.config import get_settings, get_site_config
from src.logging_config import get_logger, hash_email
from src.models import CMCustomField, CMSubscriberPayload, CMUnsubscribePayload, GhostMemberData

logger = get_logger(__name__)


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open."""

    pass


class CampaignMonitorError(Exception):
    """Campaign Monitor API error."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class CampaignMonitorClient:
    """Campaign Monitor API client with connection pooling and circuit breaker."""

    def __init__(self, list_id: str, site_id: str | None = None) -> None:
        """
        Initialize Campaign Monitor client.

        Args:
            list_id: The Campaign Monitor list ID to use
            site_id: Optional site identifier for logging
        """
        self.settings = get_settings()
        self.list_id = list_id
        self.site_id = site_id
        self._client: httpx.Client | None = None
        self._failure_count = 0
        self._circuit_open_until: float | None = None

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client with connection pooling."""
        if self._client is None:
            self._client = httpx.Client(
                base_url="https://api.createsend.com/api/v3.3",
                auth=(self.settings.cm_api_key, ""),
                timeout=self.settings.cm_api_timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker is open."""
        if self._circuit_open_until is not None:
            if time.time() < self._circuit_open_until:
                raise CircuitBreakerOpen(
                    f"Circuit breaker open until {datetime.fromtimestamp(self._circuit_open_until)}"
                )
            # Reset circuit breaker
            self._circuit_open_until = None
            self._failure_count = 0
            logger.info("circuit_breaker_reset", site_id=self.site_id)

    def _record_success(self) -> None:
        """Record successful API call."""
        self._failure_count = 0

    def _record_failure(self) -> None:
        """Record failed API call and potentially open circuit breaker."""
        self._failure_count += 1
        if self._failure_count >= self.settings.circuit_breaker_threshold:
            self._circuit_open_until = time.time() + self.settings.circuit_breaker_timeout
            logger.warning(
                "circuit_breaker_opened",
                site_id=self.site_id,
                failure_count=self._failure_count,
                timeout_seconds=self.settings.circuit_breaker_timeout,
            )

    def get_subscriber(self, email: str) -> dict[str, Any] | None:
        """
        Get subscriber details from Campaign Monitor.

        Args:
            email: Subscriber email address

        Returns:
            Subscriber data dict or None if not found
        """
        self._check_circuit_breaker()

        try:
            response = self.client.get(
                f"/subscribers/{self.list_id}.json",
                params={"email": email},
            )

            if response.status_code == 200:
                self._record_success()
                return response.json()
            elif response.status_code == 400:
                # Check if it's "subscriber not found" (Code 203) - this is not an error
                try:
                    error_data = response.json()
                    if error_data.get("Code") == 203:
                        self._record_success()
                        return None
                except Exception:
                    pass
                self._record_failure()
                raise CampaignMonitorError(
                    f"Failed to get subscriber: {response.text}",
                    status_code=response.status_code,
                )
            elif response.status_code == 404:
                self._record_success()
                return None
            else:
                self._record_failure()
                raise CampaignMonitorError(
                    f"Failed to get subscriber: {response.text}",
                    status_code=response.status_code,
                )
        except httpx.RequestError as e:
            self._record_failure()
            raise CampaignMonitorError(f"Request failed: {e}")

    def add_or_update_subscriber(
        self,
        member: GhostMemberData,
        previous_status: str | None = None,
        status_changed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Add or update a subscriber in Campaign Monitor.

        Args:
            member: Ghost member data
            previous_status: Previous status if status changed
            status_changed_at: Timestamp of status change

        Returns:
            API response data
        """
        self._check_circuit_breaker()

        custom_fields = [
            CMCustomField(Key="ghost_status", Value=member.status),
            CMCustomField(Key="ghost_signup_date", Value=member.created_at.strftime("%Y-%m-%d")),
            CMCustomField(
                Key="ghost_last_updated", Value=member.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
            CMCustomField(
                Key="ghost_labels", Value=",".join(label.name for label in member.labels)
            ),
            CMCustomField(Key="ghost_email_enabled", Value=str(member.subscribed).lower()),
        ]

        if previous_status is not None:
            custom_fields.append(CMCustomField(Key="ghost_previous_status", Value=previous_status))

        if status_changed_at is not None:
            custom_fields.append(
                CMCustomField(
                    Key="ghost_status_changed_at",
                    Value=status_changed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )

        payload = CMSubscriberPayload(
            EmailAddress=member.email,
            Name=member.name or "",
            CustomFields=custom_fields,
            Resubscribe=True,
            ConsentToTrack="Yes",
        )

        try:
            response = self.client.post(
                f"/subscribers/{self.list_id}.json",
                json=payload.model_dump(by_alias=True),
            )

            if response.status_code in (200, 201):
                self._record_success()
                logger.info(
                    "subscriber_upserted",
                    site_id=self.site_id,
                    email_hash=hash_email(member.email),
                    name=member.name or "",
                    status=member.status,
                    status_changed=previous_status is not None,
                )
                return {"success": True, "email": member.email, "name": member.name}
            else:
                self._record_failure()
                raise CampaignMonitorError(
                    f"Failed to upsert subscriber: {response.text}",
                    status_code=response.status_code,
                )
        except httpx.RequestError as e:
            self._record_failure()
            raise CampaignMonitorError(f"Request failed: {e}")

    def unsubscribe(self, email: str) -> dict[str, Any]:
        """
        Unsubscribe a subscriber (soft delete).

        Args:
            email: Subscriber email address

        Returns:
            API response data
        """
        self._check_circuit_breaker()

        payload = CMUnsubscribePayload(EmailAddress=email)

        try:
            response = self.client.post(
                f"/subscribers/{self.list_id}/unsubscribe.json",
                json=payload.model_dump(by_alias=True),
            )

            if response.status_code in (200, 201):
                self._record_success()
                logger.info(
                    "subscriber_unsubscribed",
                    site_id=self.site_id,
                    email_hash=hash_email(email),
                )
                return {"success": True, "email": email}
            elif response.status_code == 400:
                # Check if it's "subscriber not in list" (Code 203) - treat as success
                try:
                    error_data = response.json()
                    if error_data.get("Code") == 203:
                        self._record_success()
                        logger.info(
                            "subscriber_already_removed",
                            site_id=self.site_id,
                            email_hash=hash_email(email),
                        )
                        return {"success": True, "email": email, "already_removed": True}
                except Exception:
                    pass
                self._record_failure()
                raise CampaignMonitorError(
                    f"Failed to unsubscribe: {response.text}",
                    status_code=response.status_code,
                )
            else:
                self._record_failure()
                raise CampaignMonitorError(
                    f"Failed to unsubscribe: {response.text}",
                    status_code=response.status_code,
                )
        except httpx.RequestError as e:
            self._record_failure()
            raise CampaignMonitorError(f"Request failed: {e}")

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None


# Per-site client cache
_clients: dict[str, CampaignMonitorClient] = {}


def get_cm_client(site_id: str) -> CampaignMonitorClient:
    """
    Get or create Campaign Monitor client for a specific site.

    Args:
        site_id: The site identifier

    Returns:
        CampaignMonitorClient configured for the site

    Raises:
        ValueError: If site_id is not configured
    """
    global _clients

    if site_id not in _clients:
        site_config = get_site_config(site_id)
        if site_config is None:
            raise ValueError(f"Unknown site: {site_id}")

        _clients[site_id] = CampaignMonitorClient(
            list_id=site_config.cm_list_id,
            site_id=site_id,
        )

    return _clients[site_id]
