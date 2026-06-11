"""Tests for proactive_events.py — Proactive Events API client.

Covers payload building, token acquisition fallback, HTTP error handling,
and the send-notification integration with mocked HTTP.
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Override the conftest MagicMock for homeassistant.exceptions with a real
# exception class so that ProactiveEventsError is a proper Exception
# subclass (required for pytest.raises).
# ---------------------------------------------------------------------------
_ha_exc = types.ModuleType("homeassistant.exceptions")


class _MockHomeAssistantError(Exception):
    """Minimal stand-in for HomeAssistantError."""


_ha_exc.HomeAssistantError = _MockHomeAssistantError
sys.modules["homeassistant.exceptions"] = _ha_exc

# ---------------------------------------------------------------------------
# Augment the existing conftest aiohttp mock (MagicMock) with real classes
# that our tests need.  We do NOT replace sys.modules["aiohttp"] — that
# would break test_config_contract.py and test_dynamic_slots.py which
# depend on aiohttp.web being available.
# ---------------------------------------------------------------------------


class _ClientError(Exception):
    """Real exception class for aiohttp.ClientError."""


class _ClientResponse:
    """Simulates an aiohttp.ClientResponse."""

    def __init__(self, status: int, json_data=None, text_data: str = ""):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data

    async def json(self):
        if self._json_data is not None:
            return self._json_data
        raise ValueError("No JSON body")

    async def text(self):
        return self._text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _ClientSession:
    """Simulates aiohttp.ClientSession."""

    def __init__(self, response=None):
        self._response = response
        self.last_request: dict | None = None

    def request(self, method, url, **kwargs):
        self.last_request = {"method": method, "url": url, **kwargs}
        resp = self._response or _ClientResponse(200, json_data={})
        return resp

    async def close(self):
        pass


# Patch the existing mock module — add real classes without replacing it.
_aiohttp_mock = sys.modules.get("aiohttp", MagicMock())
_aiohttp_mock.ClientError = _ClientError
_aiohttp_mock.ClientSession = _ClientSession
sys.modules["aiohttp"] = _aiohttp_mock

# Force-reload so the module picks up our real exception classes.
for _mod in (
    "custom_components.alexa_actions.proactive_events",
    "custom_components.alexa_actions.api",
    "custom_components.alexa_actions.const",
):
    if _mod in sys.modules:
        importlib.reload(sys.modules[_mod])

from custom_components.alexa_actions.api import LWAClient
from custom_components.alexa_actions.const import (
    PROACTIVE_EVENTS_URL_DEV,
    PROACTIVE_EVENTS_URL_LIVE,
    SCOPE_PROACTIVE,
    SCOPE_SMAPI,
    SMAPI_BASE_URL,
)
from custom_components.alexa_actions.proactive_events import (
    DEFAULT_EVENT_TYPE,
    ProactiveEventsClient,
    ProactiveEventsError,
    _DEFAULT_EXPIRY_HOURS,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_lwa_client(
    smapi_refresh_token: str = "rt_smapi",
    proactive_refresh_token: str | None = None,
    access_token: str = "at_test",
) -> MagicMock:
    """Create a mock LWAClient with canned token methods."""
    lwa = MagicMock(spec=LWAClient)
    lwa.get_refresh_token.side_effect = lambda scope: {
        SCOPE_SMAPI: smapi_refresh_token,
        SCOPE_PROACTIVE: proactive_refresh_token,
    }.get(scope)
    lwa.async_get_smapi_token = AsyncMock(return_value=access_token)
    lwa.async_get_proactive_token = AsyncMock(return_value=access_token)
    return lwa


def _mock_session(response: _ClientResponse) -> _ClientSession:
    """Create a mock session that returns *response*."""
    return _ClientSession(response=response)


# ---------------------------------------------------------------------------
# Payload building tests
# ---------------------------------------------------------------------------


class TestBuildMessageAlertPayload:
    """Tests for ProactiveEventsClient.build_message_alert_payload."""

    def test_default_structure(self):
        payload = ProactiveEventsClient.build_message_alert_payload(
            text="Hello world",
        )
        assert payload["state"]["status"] == "UNREAD"
        assert payload["state"]["freshness"] == "NEW"
        assert payload["message"]["name"] == "Home Assistant"
        assert payload["message"]["textContent"] == "Hello world"

    def test_custom_label(self):
        payload = ProactiveEventsClient.build_message_alert_payload(
            text="Test",
            label="My App",
        )
        assert payload["message"]["name"] == "My App"

    def test_custom_status(self):
        payload = ProactiveEventsClient.build_message_alert_payload(
            text="",
            status="READ",
            freshness="OLD",
        )
        assert payload["state"]["status"] == "READ"
        assert payload["state"]["freshness"] == "OLD"


class TestBuildMediaContentPayload:
    """Tests for ProactiveEventsClient.build_media_content_payload."""

    def test_default_structure(self):
        payload = ProactiveEventsClient.build_media_content_payload(
            content_name="Podcast Ep 42",
        )
        assert payload["content"]["name"] == "Podcast Ep 42"
        assert payload["content"]["contentType"] == "BOOK"
        assert payload["provider"]["name"] == "Home Assistant"
        assert "startTime" in payload["availability"]

    def test_custom_content_type(self):
        payload = ProactiveEventsClient.build_media_content_payload(
            content_name="Song",
            content_type="SONG",
        )
        assert payload["content"]["contentType"] == "SONG"

    def test_optional_uri(self):
        payload = ProactiveEventsClient.build_media_content_payload(
            content_name="Video",
            uri="https://example.com/v/123",
        )
        assert payload["content"]["uri"] == "https://example.com/v/123"

    def test_no_uri_when_omitted(self):
        payload = ProactiveEventsClient.build_media_content_payload(
            content_name="Audio",
        )
        assert "uri" not in payload["content"]


class TestBuildEventPayload:
    """Tests for the internal _build_event_payload method."""

    def _make_client(self):
        return ProactiveEventsClient(_make_lwa_client())

    def test_message_alert_event_type(self):
        client = self._make_client()
        result = client._build_event_payload(
            event_type="AMAZON.MessageAlert.Activated",
            text="Alert!",
            locale="en-US",
            reference_id="ref123",
            expire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id=None,
        )
        assert result["event"]["name"] == "AMAZON.MessageAlert.Activated"
        assert result["event"]["payload"]["message"]["textContent"] == "Alert!"
        assert result["referenceId"] == "ref123"

    def test_media_content_event_type(self):
        client = self._make_client()
        result = client._build_event_payload(
            event_type="AMAZON.MediaContent.Available",
            text="New Episode",
            locale="en-US",
            reference_id="ref456",
            expire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id=None,
        )
        assert result["event"]["name"] == "AMAZON.MediaContent.Available"
        assert result["event"]["payload"]["content"]["name"] == "New Episode"

    def test_unknown_event_type_falls_back_to_message_alert(self):
        client = self._make_client()
        result = client._build_event_payload(
            event_type="AMAZON.Unknown.Event",
            text="Fallback",
            locale="en-US",
            reference_id="ref789",
            expire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id=None,
        )
        # Unknown types fall back to message alert payload
        assert result["event"]["payload"]["message"]["textContent"] == "Fallback"

    def test_expires_at_set_correctly(self):
        client = self._make_client()
        expire = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = client._build_event_payload(
            event_type=DEFAULT_EVENT_TYPE,
            text="Test",
            locale="en-US",
            reference_id="ref_exp",
            expire_at=expire,
            user_id=None,
        )
        assert result["expiryTime"] == expire.isoformat()

    def test_placeholder_user_id_when_none(self):
        client = self._make_client()
        result = client._build_event_payload(
            event_type=DEFAULT_EVENT_TYPE,
            text="Test",
            locale="en-US",
            reference_id="ref_user",
            expire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id=None,
        )
        assert result["relevantAudience"]["payload"]["unlocalizedUserId"] == (
            "amzn1.ask.account.placeholder"
        )

    def test_custom_user_id(self):
        client = self._make_client()
        result = client._build_event_payload(
            event_type=DEFAULT_EVENT_TYPE,
            text="Test",
            locale="en-US",
            reference_id="ref_user2",
            expire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id="amzn1.ask.account.ABC123",
        )
        uid = result["relevantAudience"]["payload"]["unlocalizedUserId"]
        assert uid == "amzn1.ask.account.ABC123"

    def test_unicast_audience_type(self):
        client = self._make_client()
        result = client._build_event_payload(
            event_type=DEFAULT_EVENT_TYPE,
            text="Test",
            locale="en-US",
            reference_id="ref_aud",
            expire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id=None,
        )
        assert result["relevantAudience"]["type"] == "Unicast"

    def test_timestamp_is_iso_format(self):
        client = self._make_client()
        result = client._build_event_payload(
            event_type=DEFAULT_EVENT_TYPE,
            text="Test",
            locale="en-US",
            reference_id="ref_ts",
            expire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id=None,
        )
        # Should be parseable as ISO format
        datetime.fromisoformat(result["timestamp"])


# ---------------------------------------------------------------------------
# Token acquisition tests
# ---------------------------------------------------------------------------


class TestTokenAcquisition:
    """Tests for ProactiveEventsClient._async_get_token."""

    @pytest.mark.asyncio
    async def test_prefers_proactive_scope_token(self):
        lwa = _make_lwa_client(proactive_refresh_token="rt_proactive")
        client = ProactiveEventsClient(lwa)
        token = await client._async_get_token()
        assert token == "at_test"
        lwa.async_get_proactive_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_smapi_when_no_proactive_refresh(self):
        lwa = _make_lwa_client(proactive_refresh_token=None)
        client = ProactiveEventsClient(lwa)
        token = await client._async_get_token()
        assert token == "at_test"
        # Should not have tried proactive token (no refresh token)
        lwa.async_get_proactive_token.assert_not_awaited()
        lwa.async_get_smapi_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_smapi_on_proactive_failure(self):
        lwa = _make_lwa_client(proactive_refresh_token="rt_proactive")
        lwa.async_get_proactive_token = AsyncMock(
            side_effect=_MockHomeAssistantError("token fail")
        )
        client = ProactiveEventsClient(lwa)
        token = await client._async_get_token()
        assert token == "at_test"
        lwa.async_get_smapi_token.assert_awaited_once()


# ---------------------------------------------------------------------------
# HTTP response handling tests
# ---------------------------------------------------------------------------


class TestHandleResponse:
    """Tests for ProactiveEventsClient._handle_response."""

    @pytest.mark.asyncio
    async def test_202_returns_accepted(self):
        resp = _ClientResponse(202)
        result = await ProactiveEventsClient._handle_response(resp)
        assert result == {"status": "accepted"}

    @pytest.mark.asyncio
    async def test_204_returns_no_content(self):
        resp = _ClientResponse(204)
        result = await ProactiveEventsClient._handle_response(resp)
        assert result == {"status": "no_content"}

    @pytest.mark.asyncio
    async def test_401_raises_proactive_events_error(self):
        resp = _ClientResponse(401, text_data="Unauthorized")
        with pytest.raises(ProactiveEventsError, match="unauthorized"):
            await ProactiveEventsClient._handle_response(resp)

    @pytest.mark.asyncio
    async def test_401_mentions_scope(self):
        resp = _ClientResponse(401, text_data="Bad token")
        with pytest.raises(ProactiveEventsError, match="alexa::proactive_events"):
            await ProactiveEventsClient._handle_response(resp)

    @pytest.mark.asyncio
    async def test_409_raises_duplicate_error(self):
        resp = _ClientResponse(409, text_data="Conflict dup")
        with pytest.raises(ProactiveEventsError, match="Duplicate"):
            await ProactiveEventsClient._handle_response(resp)

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit(self):
        resp = _ClientResponse(429, text_data="")
        with pytest.raises(ProactiveEventsError, match="rate limit"):
            await ProactiveEventsClient._handle_response(resp)

    @pytest.mark.asyncio
    async def test_500_raises_generic_error(self):
        resp = _ClientResponse(500, text_data="Internal Server Error")
        with pytest.raises(ProactiveEventsError, match="500"):
            await ProactiveEventsClient._handle_response(resp)

    @pytest.mark.asyncio
    async def test_200_returns_json(self):
        resp = _ClientResponse(200, json_data={"key": "value"})
        result = await ProactiveEventsClient._handle_response(resp)
        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# Integration-style send tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestSendNotification:
    """Tests for ProactiveEventsClient.async_send_notification."""

    @pytest.mark.asyncio
    async def test_send_uses_development_path_by_default(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(text="Test message")

        assert session.last_request is not None
        assert PROACTIVE_EVENTS_URL_DEV in session.last_request["url"]

    @pytest.mark.asyncio
    async def test_send_uses_live_path_when_development_false(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(
            text="Live message", development=False,
        )

        assert session.last_request is not None
        assert PROACTIVE_EVENTS_URL_LIVE in session.last_request["url"]

    @pytest.mark.asyncio
    async def test_send_includes_authorization_header(self):
        lwa = _make_lwa_client(access_token="my_secret_token")
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(text="Auth test")

        headers = session.last_request["headers"]
        assert headers["Authorization"] == "Bearer my_secret_token"

    @pytest.mark.asyncio
    async def test_send_includes_json_body(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(
            text="Body test",
            event_type="AMAZON.MessageAlert.Activated",
        )

        body = session.last_request["json"]
        assert body["event"]["name"] == "AMAZON.MessageAlert.Activated"
        assert body["event"]["payload"]["message"]["textContent"] == "Body test"

    @pytest.mark.asyncio
    async def test_send_auto_generates_reference_id(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(text="Ref test")

        body = session.last_request["json"]
        assert body["referenceId"]  # Non-empty auto-generated ID

    @pytest.mark.asyncio
    async def test_send_uses_provided_reference_id(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(
            text="Custom ref", reference_id="my-ref-123",
        )

        body = session.last_request["json"]
        assert body["referenceId"] == "my-ref-123"

    @pytest.mark.asyncio
    async def test_send_auto_sets_expiry(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(text="Expiry test")

        body = session.last_request["json"]
        expiry = datetime.fromisoformat(body["expiryTime"])
        # Should be approximately 1 hour from now (within 2 minutes tolerance)
        expected_min = datetime.now(timezone.utc) + timedelta(
            hours=_DEFAULT_EXPIRY_HOURS, minutes=-2,
        )
        expected_max = datetime.now(timezone.utc) + timedelta(
            hours=_DEFAULT_EXPIRY_HOURS, minutes=2,
        )
        assert expected_min <= expiry <= expected_max

    @pytest.mark.asyncio
    async def test_send_uses_custom_expiry(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        custom_expire = datetime(2026, 6, 11, 15, 0, 0, tzinfo=timezone.utc)
        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(
            text="Custom expiry", expire_at=custom_expire,
        )

        body = session.last_request["json"]
        assert body["expiryTime"] == custom_expire.isoformat()

    @pytest.mark.asyncio
    async def test_send_raises_on_401(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(401, text_data="Bad scope"))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        with pytest.raises(ProactiveEventsError, match="unauthorized"):
            await client.async_send_notification(text="Fail")

    @pytest.mark.asyncio
    async def test_send_raises_on_client_error(self):
        lwa = _make_lwa_client()
        # Session that raises ClientError
        error_session = MagicMock()
        error_session.request.side_effect = _ClientError("connection reset")
        lwa.get_session = AsyncMock(return_value=error_session)

        client = ProactiveEventsClient(lwa)
        with pytest.raises(ProactiveEventsError, match="request failed"):
            await client.async_send_notification(text="Network fail")

    @pytest.mark.asyncio
    async def test_send_returns_accepted_on_202(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        result = await client.async_send_notification(text="Success")
        assert result == {"status": "accepted"}

    @pytest.mark.asyncio
    async def test_send_uses_post_method(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(text="Method test")

        assert session.last_request["method"] == "POST"

    @pytest.mark.asyncio
    async def test_send_url_starts_with_base(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(text="URL test")

        url = session.last_request["url"]
        assert url.startswith(SMAPI_BASE_URL)

    @pytest.mark.asyncio
    async def test_send_with_media_content_event_type(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(
            text="New Podcast",
            event_type="AMAZON.MediaContent.Available",
        )

        body = session.last_request["json"]
        assert body["event"]["name"] == "AMAZON.MediaContent.Available"
        assert body["event"]["payload"]["content"]["name"] == "New Podcast"

    @pytest.mark.asyncio
    async def test_send_passes_user_id(self):
        lwa = _make_lwa_client()
        session = _mock_session(_ClientResponse(202))
        lwa.get_session = AsyncMock(return_value=session)

        client = ProactiveEventsClient(lwa)
        await client.async_send_notification(
            text="User test",
            user_id="amzn1.ask.account.XYZ",
        )

        body = session.last_request["json"]
        assert (
            body["relevantAudience"]["payload"]["unlocalizedUserId"]
            == "amzn1.ask.account.XYZ"
        )
