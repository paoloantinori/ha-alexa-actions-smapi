"""Proactive Events API client for Alexa-initiated notifications.

Sends proactive events to Alexa devices without requiring ``play_media``
or the ``alexa_media`` integration.  Uses the same LWA credentials
configured for SMAPI.

**Important prerequisites**:

1. The OAuth authorization scope must include ``alexa::proactive_events``.
   If the existing refresh token was obtained with only the SMAPI scope,
   re-configure the integration to add the proactive events scope.
2. For live (production) events the skill **must** be published.  During
   development, use the ``/v1/proactiveEvents/stages/development`` endpoint.
3. The user must have enabled the skill in the Alexa app for proactive
   events to be delivered to their devices.

References:
- https://developer.amazon.com/en-US/docs/alexa/smapi/proactive-events-api.html
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from homeassistant.exceptions import HomeAssistantError

from .api import LWAClient
from .const import (
    PROACTIVE_EVENTS_URL_DEV,
    PROACTIVE_EVENTS_URL_LIVE,
    SCOPE_PROACTIVE,
    SCOPE_SMAPI,
    SMAPI_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)

# Default proactive event schema for generic message alerts.
DEFAULT_EVENT_TYPE = "AMAZON.MessageAlert.Activated"

# Default expiry offset from time of sending.
_DEFAULT_EXPIRY_HOURS = 1


class ProactiveEventsError(HomeAssistantError):
    """Raised when a Proactive Events API call fails."""


class ProactiveEventsClient:
    """Send proactive events to Alexa devices via Amazon's REST API."""

    def __init__(self, lwa_client: LWAClient) -> None:
        self._lwa_client = lwa_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def async_send_notification(
        self,
        text: str,
        *,
        event_type: str = DEFAULT_EVENT_TYPE,
        locale: str = "en-US",
        reference_id: str | None = None,
        expire_at: datetime | None = None,
        development: bool = True,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a proactive notification event.

        Args:
            text: The notification text to display/speak.
            event_type: The proactive event schema type.
            locale: Target locale (default ``"en-US"``).
            reference_id: Unique ID for deduplication (auto-generated if
                omitted).
            expire_at: When the event expires (default 1 hour from now).
            development: Use the development endpoint (default ``True``).
            user_id: Amazon user ID for unicast delivery.  Required for
                live events; for development mode a placeholder is used
                when omitted.

        Returns:
            Parsed JSON response from the API.

        Raises:
            ProactiveEventsError: On auth or API errors.
        """
        if reference_id is None:
            reference_id = uuid.uuid4().hex
        if expire_at is None:
            expire_at = datetime.now(timezone.utc) + timedelta(
                hours=_DEFAULT_EXPIRY_HOURS
            )

        payload = self._build_event_payload(
            event_type=event_type,
            text=text,
            locale=locale,
            reference_id=reference_id,
            expire_at=expire_at,
            user_id=user_id,
        )

        path = PROACTIVE_EVENTS_URL_DEV if development else PROACTIVE_EVENTS_URL_LIVE
        return await self._async_request("POST", path, json_body=payload)

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------

    @staticmethod
    def build_message_alert_payload(
        *,
        text: str,
        status: str = "UNREAD",
        freshness: str = "NEW",
        label: str = "Home Assistant",
    ) -> dict[str, Any]:
        """Build a ``AMAZON.MessageAlert.Activated`` event payload body.

        This is the most generic proactive event schema and is suitable
        for free-form notification text.
        """
        return {
            "state": {
                "status": status,
                "freshness": freshness,
            },
            "message": {
                "name": label,
                "textContent": text,
            },
        }

    @staticmethod
    def build_media_content_payload(
        *,
        content_name: str,
        content_type: str = "BOOK",
        provider_name: str = "Home Assistant",
        uri: str | None = None,
    ) -> dict[str, Any]:
        """Build an ``AMAZON.MediaContent.Available`` event payload body.

        Useful for notifications about new media content (e.g. a new
        podcast episode, audiobook, or playlist).
        """
        payload: dict[str, Any] = {
            "availability": {
                "startTime": datetime.now(timezone.utc).isoformat(),
            },
            "content": {
                "name": content_name,
                "contentType": content_type,
            },
            "provider": {
                "name": provider_name,
            },
        }
        if uri is not None:
            payload["content"]["uri"] = uri
        return payload

    def _build_event_payload(
        self,
        *,
        event_type: str,
        text: str,
        locale: str,
        reference_id: str,
        expire_at: datetime,
        user_id: str | None,
    ) -> dict[str, Any]:
        """Build a complete proactive event JSON payload ready for the API."""
        if event_type == "AMAZON.MessageAlert.Activated":
            event_payload = self.build_message_alert_payload(text=text)
        elif event_type == "AMAZON.MediaContent.Available":
            event_payload = self.build_media_content_payload(
                content_name=text,
            )
        else:
            # Fallback: treat as a generic message alert.
            event_payload = self.build_message_alert_payload(text=text)

        audience_user_id = user_id or "amzn1.ask.account.placeholder"
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "referenceId": reference_id,
            "expiryTime": expire_at.isoformat(),
            "event": {
                "name": event_type,
                "payload": event_payload,
            },
            "localizedAttributes": [],
            "relevantAudience": {
                "type": "Unicast",
                "payload": {
                    "unlocalizedUserId": audience_user_id,
                },
            },
        }

    # ------------------------------------------------------------------
    # HTTP transport
    # ------------------------------------------------------------------

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an authenticated Proactive Events HTTP request."""
        # Try the proactive-events scope first; fall back to SMAPI scope.
        token = await self._async_get_token()

        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        session = await self._lwa_client.get_session()
        url = f"{SMAPI_BASE_URL}{path}"
        kwargs: dict[str, Any] = {"headers": headers}
        if json_body is not None:
            kwargs["json"] = json_body

        try:
            async with session.request(method, url, **kwargs) as resp:
                return await self._handle_response(resp)
        except ProactiveEventsError:
            raise
        except aiohttp.ClientError as err:
            raise ProactiveEventsError(
                f"Proactive Events request failed: {err}"
            ) from err

    async def _async_get_token(self) -> str:
        """Obtain an access token, preferring the proactive-events scope."""
        # Prefer the dedicated proactive-events scope if a refresh token
        # has been registered for it.
        rt = self._lwa_client.get_refresh_token(SCOPE_PROACTIVE)
        if rt:
            try:
                return await self._lwa_client.async_get_proactive_token()
            except HomeAssistantError:
                _LOGGER.debug(
                    "Proactive-events scope token failed, falling back to SMAPI scope"
                )
        # Fallback: reuse the SMAPI token (may work if Amazon granted a
        # broader scope during the original OAuth authorization).
        return await self._lwa_client.async_get_smapi_token()

    @staticmethod
    async def _handle_response(resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Interpret the HTTP response and raise on errors."""
        if resp.status == 202:
            return {"status": "accepted"}
        if resp.status == 204:
            return {"status": "no_content"}
        if resp.status == 401:
            body = await resp.text()
            raise ProactiveEventsError(
                "Proactive Events API unauthorized (401). "
                "The OAuth scope may not include 'alexa::proactive_events'. "
                "Re-configure the integration to add this scope. "
                f"Details: {body[:300]}"
            )
        if resp.status == 409:
            body = await resp.text()
            raise ProactiveEventsError(
                f"Duplicate proactive event (409): {body[:300]}"
            )
        if resp.status == 429:
            raise ProactiveEventsError(
                "Proactive Events rate limit exceeded (429)"
            )
        if resp.status >= 400:
            body = await resp.text()
            raise ProactiveEventsError(
                f"Proactive Events error ({resp.status}): {body[:300]}"
            )
        # 200 or other success codes.
        try:
            return await resp.json()
        except Exception:
            return {"status": "ok"}
