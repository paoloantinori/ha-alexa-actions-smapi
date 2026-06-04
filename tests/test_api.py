"""Tests for custom_components/alexa_actions/api.py — LWAClient and _TokenCache."""

import sys
import os
import time

# Ensure custom_components is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock

# Mock HA modules before importing api
sys.modules.setdefault("homeassistant.core", MagicMock())
sys.modules.setdefault("homeassistant.exceptions", MagicMock())

from custom_components.alexa_actions.api import LWAClient, _TokenCache
from custom_components.alexa_actions.const import LWA_AUTH_URL, SCOPE_SMAPI


class TestTokenCache:
    """Tests for the _TokenCache internal class."""

    def test_stores_token_and_expiry(self):
        expires = time.monotonic() + 3000
        cache = _TokenCache(token="abc123", expires_at=expires)
        assert cache.token == "abc123"
        assert cache.expires_at == expires

    def test_not_expired_when_future(self):
        cache = _TokenCache(token="tok", expires_at=time.monotonic() + 3000)
        assert cache.expires_at > time.monotonic()

    def test_expired_when_past(self):
        cache = _TokenCache(token="tok", expires_at=time.monotonic() - 1)
        assert cache.expires_at < time.monotonic()

    def test_has_expected_slots(self):
        assert _TokenCache.__slots__ == ("token", "expires_at")


class TestLWAClientAuthorizationUrl:
    """Tests for LWAClient.get_authorization_url()."""

    def _make_client(self):
        hass = MagicMock()
        return LWAClient(hass, "test_client_id", "test_secret")

    def test_contains_client_id(self):
        client = self._make_client()
        url = client.get_authorization_url("https://example.com/callback", SCOPE_SMAPI)
        assert "client_id=test_client_id" in url

    def test_contains_lwa_auth_base(self):
        client = self._make_client()
        url = client.get_authorization_url("https://example.com/callback", SCOPE_SMAPI)
        assert url.startswith(LWA_AUTH_URL + "?")

    def test_contains_response_type_code(self):
        client = self._make_client()
        url = client.get_authorization_url("https://example.com/callback", SCOPE_SMAPI)
        assert "response_type=code" in url

    def test_contains_redirect_uri(self):
        client = self._make_client()
        url = client.get_authorization_url("https://example.com/callback", SCOPE_SMAPI)
        assert "redirect_uri=" in url

    def test_contains_scope(self):
        client = self._make_client()
        url = client.get_authorization_url("https://example.com/callback", SCOPE_SMAPI)
        # Scope is URL-encoded; at minimum the alexa prefix should appear
        assert "scope=" in url

    def test_redirect_uri_is_url_encoded(self):
        client = self._make_client()
        url = client.get_authorization_url(
            "https://example.com/my callback", SCOPE_SMAPI
        )
        # Space in redirect_uri must be encoded
        assert "my%20callback" in url or "my+callback" in url

    def test_different_scopes_produce_different_urls(self):
        client = self._make_client()
        url1 = client.get_authorization_url("https://example.com/cb", "scope_a")
        url2 = client.get_authorization_url("https://example.com/cb", "scope_b")
        assert url1 != url2


class TestLWAClientRefreshToken:
    """Tests for refresh token storage on LWAClient."""

    def _make_client(self):
        hass = MagicMock()
        return LWAClient(hass, "cid", "csecret")

    def test_set_and_get_refresh_token(self):
        client = self._make_client()
        client.set_refresh_token("my_scope", "rt_12345")
        assert client.get_refresh_token("my_scope") == "rt_12345"

    def test_get_missing_refresh_token(self):
        client = self._make_client()
        assert client.get_refresh_token("nonexistent") is None

    def test_overwrite_refresh_token(self):
        client = self._make_client()
        client.set_refresh_token("scope", "old_token")
        client.set_refresh_token("scope", "new_token")
        assert client.get_refresh_token("scope") == "new_token"

    def test_multiple_scopes_independent(self):
        client = self._make_client()
        client.set_refresh_token("scope_a", "token_a")
        client.set_refresh_token("scope_b", "token_b")
        assert client.get_refresh_token("scope_a") == "token_a"
        assert client.get_refresh_token("scope_b") == "token_b"


class TestLWAClientInvalidateToken:
    """Tests for LWAClient.invalidate_token()."""

    def test_invalidate_clears_cached_token(self):
        hass = MagicMock()
        client = LWAClient(hass, "cid", "csecret")
        # Manually inject a cached token
        client._tokens["my_scope"] = _TokenCache(
            token="cached_tok", expires_at=time.monotonic() + 3600
        )
        assert "my_scope" in client._tokens
        client.invalidate_token("my_scope")
        assert "my_scope" not in client._tokens

    def test_invalidate_nonexistent_scope_no_error(self):
        hass = MagicMock()
        client = LWAClient(hass, "cid", "csecret")
        # Should not raise
        client.invalidate_token("nonexistent")


class TestLWAClientStoreToken:
    """Tests for the internal _store_token method."""

    def test_stores_access_token(self):
        hass = MagicMock()
        client = LWAClient(hass, "cid", "csecret")
        data = {"access_token": "at_123", "expires_in": 3600}
        client._store_token("scope_a", data)
        assert "scope_a" in client._tokens
        assert client._tokens["scope_a"].token == "at_123"

    def test_stores_refresh_token_from_response(self):
        hass = MagicMock()
        client = LWAClient(hass, "cid", "csecret")
        data = {
            "access_token": "at_456",
            "refresh_token": "rt_456",
            "expires_in": 3600,
        }
        client._store_token("scope_b", data)
        assert client.get_refresh_token("scope_b") == "rt_456"

    def test_expiry_includes_buffer(self):
        hass = MagicMock()
        client = LWAClient(hass, "cid", "csecret")
        data = {"access_token": "at_buf", "expires_in": 3600}
        before = time.monotonic()
        client._store_token("scope_c", data)
        after = time.monotonic()
        # expires_at should be approximately 3600 - 60 = 3540 seconds from now
        cached = client._tokens["scope_c"]
        # Allow 5 seconds of tolerance for test execution
        expected_min = before + 3600 - 60
        expected_max = after + 3600 - 60
        assert expected_min <= cached.expires_at <= expected_max + 5

    def test_stores_individual_scope_parts(self):
        """When scope has multiple parts, each part gets its own cache entry."""
        hass = MagicMock()
        client = LWAClient(hass, "cid", "csecret")
        scope = "alexa::ask:skills:readwrite alexa::ask:models:readwrite"
        data = {
            "access_token": "at_parts",
            "refresh_token": "rt_parts",
            "expires_in": 3600,
        }
        client._store_token(scope, data)
        # Individual parts should also be stored
        assert "alexa::ask:skills:readwrite" in client._tokens
        assert "alexa::ask:models:readwrite" in client._tokens
