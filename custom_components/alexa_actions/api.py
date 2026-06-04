"""OAuth2 client for Amazon Login with Amazon (LWA)."""
from __future__ import annotations

import logging
import time
from urllib.parse import quote, urlencode

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import LWA_AUTH_URL, LWA_TOKEN_URL, SCOPE_SMAPI

_LOGGER = logging.getLogger(__name__)

_TOKEN_BUFFER_SECONDS = 60

_SMAPI_SCOPE_PARTS = frozenset(SCOPE_SMAPI.split())


class _TokenCache:
    """Simple cache entry holding an access token and its monotonic expiry."""

    __slots__ = ("token", "expires_at")

    def __init__(self, token: str, expires_at: float) -> None:
        self.token = token
        self.expires_at = expires_at


class LWAClient:
    """Manages LWA access tokens for SMAPI."""

    def __init__(
        self,
        hass: HomeAssistant,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._hass = hass
        self._client_id = client_id
        self._client_secret = client_secret
        self._session: aiohttp.ClientSession | None = None
        self._tokens: dict[str, _TokenCache] = {}
        self._refresh_tokens: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def set_refresh_token(self, scope: str, refresh_token: str) -> None:
        """Persist a refresh token for the given scope."""
        self._refresh_tokens[scope] = refresh_token

    def get_refresh_token(self, scope: str) -> str | None:
        """Return the stored refresh token for *scope*, if any."""
        return self._refresh_tokens.get(scope)

    def invalidate_token(self, scope: str) -> None:
        """Clear the cached access token so the next call fetches a new one."""
        self._tokens.pop(scope, None)

    def get_authorization_url(self, redirect_uri: str, scope: str) -> str:
        """Build the LWA authorization URL for the OAuth2 code flow."""
        params = {
            "client_id": self._client_id,
            "scope": scope,
            "response_type": "code",
            "redirect_uri": redirect_uri,
        }
        return f"{LWA_AUTH_URL}?{urlencode(params, quote_via=quote)}"

    # ------------------------------------------------------------------
    # Token acquisition
    # ------------------------------------------------------------------

    async def async_exchange_code(
        self, code: str, redirect_uri: str, scope: str
    ) -> dict:
        """Exchange an authorisation code for tokens."""
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": redirect_uri,
        }
        data = await self._async_token_request(payload, "code exchange")
        self._store_token(scope, data)
        return data

    async def async_get_smapi_token(self) -> str:
        """Return a valid SMAPI access token, refreshing when necessary."""
        return await self._async_get_token(SCOPE_SMAPI)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def async_close(self) -> None:
        """Close the underlying HTTP session (call on integration unload)."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return a reusable aiohttp session, creating one if needed."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                resolver=aiohttp.resolver.ThreadedResolver(),
            )
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def _async_get_token(self, scope: str) -> str:
        """Return a cached token or obtain a fresh one via refresh."""
        cached = self._tokens.get(scope)
        if cached and time.monotonic() < cached.expires_at:
            return cached.token

        refresh_token = self._refresh_tokens.get(scope)
        if refresh_token:
            await self._async_refresh(scope, refresh_token)
            cached = self._tokens.get(scope)
            if cached:
                return cached.token

        raise HomeAssistantError(
            f"No token for scope {scope} — reconfigure the integration"
        )

    async def _async_refresh(self, scope: str, refresh_token: str) -> None:
        """Refresh the access token using a stored refresh token."""
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        data = await self._async_token_request(payload, "refresh")
        self._store_token(scope, data)

    async def _async_token_request(self, payload: dict, label: str) -> dict:
        """POST to the LWA token endpoint and return the parsed JSON."""
        session = await self._get_session()
        try:
            async with session.post(LWA_TOKEN_URL, data=payload) as resp:
                data = await resp.json()
        except (aiohttp.ClientError, OSError) as err:
            raise HomeAssistantError(
                f"Cannot connect to Amazon LWA: {err}"
            ) from err

        error = data.get("error")
        if error:
            _LOGGER.error(
                "LWA %s failed: %s — %s",
                label,
                error,
                data.get("error_description", ""),
            )
            raise HomeAssistantError(
                f"LWA error: {error} — {data.get('error_description', '')}"
            )

        if "access_token" not in data:
            raise HomeAssistantError("Invalid LWA token response")

        return data

    def _store_token(self, scope: str, data: dict) -> None:
        """Cache the access token and refresh token from an LWA response."""
        entry = _TokenCache(
            token=data["access_token"],
            expires_at=time.monotonic()
            + int(data.get("expires_in", 3600))
            - _TOKEN_BUFFER_SECONDS,
        )
        self._tokens[scope] = entry
        if "refresh_token" in data:
            self._refresh_tokens[scope] = data["refresh_token"]

        # Also store under each individual scope part so lookups by a
        # single scope component also succeed.
        scope_parts = scope.split()
        for part in scope_parts:
            if part != scope:
                self._tokens[part] = entry
                if "refresh_token" in data:
                    self._refresh_tokens[part] = data["refresh_token"]

        # Ensure the canonical SMAPI scope key always points to the token
        # when the requested scope covers all SMAPI permissions.
        if _SMAPI_SCOPE_PARTS.issubset(scope_parts):
            self._tokens[SCOPE_SMAPI] = entry
            if "refresh_token" in data:
                self._refresh_tokens[SCOPE_SMAPI] = data["refresh_token"]
