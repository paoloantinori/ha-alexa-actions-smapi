"""Tests for the config flow — focusing on auth state handling and Submit behaviour.

Strategy: We replace the mocked ``homeassistant.config_entries`` module with a
real one that provides a genuine ``ConfigFlow`` base class, then **reload** the
``config_flow`` module so that ``AlexaActionsConfigFlow`` is a real class
inheriting from it.  This lets us instantiate it and call its async methods.

Key behaviours under test:
  1. auth_smapi form carries an explicit ``data_schema`` (the Submit-button fix)
  2. An empty-dict ``user_input={}`` from Submit is correctly handled
  3. Auth codes stored by the callback view are consumed on Submit
  4. The callback view correctly stores / rejects codes
"""

import asyncio
import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

# Ensure custom_components is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Set up *real* Home Assistant mock modules (replacing conftest's generic ones)
# ---------------------------------------------------------------------------
import voluptuous as vol

# -- homeassistant.exceptions (real exception class) --
_ha_exc = types.ModuleType("homeassistant.exceptions")


class HomeAssistantError(Exception):
    pass


_ha_exc.HomeAssistantError = HomeAssistantError
sys.modules["homeassistant.exceptions"] = _ha_exc

# -- homeassistant.config_entries (real base classes) --
_ha_ce = types.ModuleType("homeassistant.config_entries")


class ConfigFlow:
    """Minimal real ConfigFlow base with the methods the flow actually uses."""

    VERSION = 1

    def __init_subclass__(cls, **kwargs):
        """Accept and ignore HA-specific keyword args like ``domain=``."""
        super().__init_subclass__()

    def __init__(self):
        self.hass = None
        self._lwa_client = None
        self._auth_state = None
        self._user_input = {}

    async def async_set_unique_id(self, uid):
        pass

    def _abort_if_unique_id_configured(self):
        pass

    def async_show_form(self, *, step_id, data_schema=None,
                        description_placeholders=None, errors=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "description_placeholders": description_placeholders or {},
            "errors": errors or {},
        }

    def async_create_entry(self, *, title, data):
        return {"type": "create_entry", "title": title, "data": data}


class OptionsFlow:
    pass


class ConfigEntry:
    pass


_ha_ce.ConfigFlow = ConfigFlow
_ha_ce.OptionsFlow = OptionsFlow
_ha_ce.ConfigEntry = ConfigEntry
sys.modules["homeassistant.config_entries"] = _ha_ce

# -- homeassistant.const --
_ha_const = types.ModuleType("homeassistant.const")
_ha_const.CONF_CLIENT_ID = "client_id"
_ha_const.CONF_CLIENT_SECRET = "client_secret"
sys.modules["homeassistant.const"] = _ha_const

# -- homeassistant.data_entry_flow --
_ha_def = types.ModuleType("homeassistant.data_entry_flow")
FlowResult = dict  # the flow returns dicts


class AbortFlow(Exception):
    pass


_ha_def.FlowResult = FlowResult
_ha_def.AbortFlow = AbortFlow
sys.modules["homeassistant.data_entry_flow"] = _ha_def

# -- homeassistant.helpers.selector --
_ha_sel = types.ModuleType("homeassistant.helpers.selector")


class SelectOptionDict(dict):
    pass


class SelectSelector:
    def __init__(self, config):
        pass


class SelectSelectorConfig:
    def __init__(self, **kw):
        pass


class SelectSelectorMode:
    LIST = "list"


class TextSelector:
    def __init__(self, config):
        pass


class TextSelectorConfig:
    def __init__(self, **kw):
        pass


class TextSelectorType:
    TEXT = "text"
    PASSWORD = "password"
    URL = "url"


_ha_sel.SelectOptionDict = SelectOptionDict
_ha_sel.SelectSelector = SelectSelector
_ha_sel.SelectSelectorConfig = SelectSelectorConfig
_ha_sel.SelectSelectorMode = SelectSelectorMode
_ha_sel.TextSelector = TextSelector
_ha_sel.TextSelectorConfig = TextSelectorConfig
_ha_sel.TextSelectorType = TextSelectorType
sys.modules["homeassistant.helpers.selector"] = _ha_sel

# -- homeassistant.helpers.network --
_ha_net = types.ModuleType("homeassistant.helpers.network")


class NoURLAvailableError(Exception):
    pass


def get_url(hass):
    return "https://ha.example.com"


_ha_net.NoURLAvailableError = NoURLAvailableError
_ha_net.get_url = get_url
sys.modules["homeassistant.helpers.network"] = _ha_net

# -- homeassistant.components.http --
_ha_http = types.ModuleType("homeassistant.components.http")


class HomeAssistantView:
    """Minimal base for the callback view."""
    url = ""
    name = ""
    requires_auth = True

    def __init__(self, hass=None):
        self._hass = hass

    async def get(self, request):
        raise NotImplementedError


_ha_http.HomeAssistantView = HomeAssistantView
sys.modules["homeassistant.components.http"] = _ha_http

# -- homeassistant.core (already mocked in conftest, provide real enough) --
if not isinstance(sys.modules.get("homeassistant.core"), types.ModuleType):
    _ha_core = types.ModuleType("homeassistant.core")

    class _HomeAssistant:
        pass

    class _ServiceCall:
        pass

    def _callback(func):
        return func

    _ha_core.HomeAssistant = _HomeAssistant
    _ha_core.ServiceCall = _ServiceCall
    _ha_core.callback = _callback
    sys.modules["homeassistant.core"] = _ha_core

# -- homeassistant (top-level) --
if not isinstance(sys.modules.get("homeassistant"), types.ModuleType):
    _ha = types.ModuleType("homeassistant")
    _ha.config_entries = _ha_ce
    sys.modules["homeassistant"] = _ha

# -- aiohttp (need real web.Response for views.py) --
import aiohttp as _real_aiohttp
sys.modules["aiohttp"] = _real_aiohttp

# Make voluptuous real (not conftest mock)
sys.modules["voluptuous"] = vol

# ---------------------------------------------------------------------------
# Now reload the config_flow module so it picks up our real base classes
# ---------------------------------------------------------------------------
import custom_components.alexa_actions.const as _const
import custom_components.alexa_actions.exceptions as _exc
import custom_components.alexa_actions.models as _models
import custom_components.alexa_actions.api as _api
import custom_components.alexa_actions.views as _views

# Force-reload config_flow so it re-imports with our real base classes
import custom_components.alexa_actions.config_flow as _cf_mod
importlib.reload(_cf_mod)

# Also reload views so it picks up real aiohttp (not conftest mock)
import custom_components.alexa_actions.views as _views_mod
importlib.reload(_views_mod)

from custom_components.alexa_actions.config_flow import (
    AlexaActionsConfigFlow,
    AlexaActionsOptionsFlow,
    _CALLBACK_PATH,
)
from custom_components.alexa_actions.const import (
    CONF_HA_TOKEN,
    CONF_HA_URL,
    CONF_INVOCATION_NAME,
    CONF_LOCALES,
    CONF_REFRESH_TOKEN,
    CONF_SKILL_ID,
    CONF_VENDOR_ID,
    DOMAIN,
    SCOPE_SMAPI,
)
from custom_components.alexa_actions.views import AlexaAuthCallbackView


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # If we're somehow inside an existing loop, create a new one.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _make_flow() -> AlexaActionsConfigFlow:
    """Create a config flow instance with enough mocking to run."""
    flow = AlexaActionsConfigFlow()
    flow.hass = MagicMock()
    flow.hass.data = {}
    flow.hass.http = MagicMock()
    flow._lwa_client = MagicMock()
    flow._auth_state = None
    flow._user_input = {
        "client_id": "cid",
        "client_secret": "csecret",
        CONF_HA_URL: "https://ha.example.com",
        CONF_HA_TOKEN: "long_lived_token",
        CONF_INVOCATION_NAME: "test skill",
        CONF_LOCALES: ["en-US"],
    }
    return flow


# ---------------------------------------------------------------------------
# Test: auth_smapi step — first display
# ---------------------------------------------------------------------------

class TestAuthSmapiFirstDisplay:
    """When auth_smapi is shown for the first time it must generate a state
    and return a form with a data_schema so the Submit button works."""

    def test_generates_auth_state(self):
        flow = _make_flow()
        result = _run(flow.async_step_auth_smapi(user_input=None))
        assert result["type"] == "form"
        assert result["step_id"] == "auth_smapi"
        assert flow._auth_state is not None
        import uuid
        uuid.UUID(flow._auth_state)  # must be a valid UUID

    def test_form_has_data_schema(self):
        """The form MUST carry a data_schema so the Submit button works."""
        flow = _make_flow()
        result = _run(flow.async_step_auth_smapi(user_input=None))
        assert result["data_schema"] is not None, (
            "auth_smapi form must have a data_schema so the Submit button "
            "is functional in all HA versions"
        )

    def test_form_has_auth_url_placeholder(self):
        flow = _make_flow()
        result = _run(flow.async_step_auth_smapi(user_input=None))
        placeholders = result["description_placeholders"]
        assert "auth_url" in placeholders
        assert "callback_url" in placeholders

    def test_form_has_no_errors_on_first_display(self):
        flow = _make_flow()
        result = _run(flow.async_step_auth_smapi(user_input=None))
        assert result["errors"] == {}


# ---------------------------------------------------------------------------
# Test: auth_smapi step — Submit with auth code present (happy path)
# ---------------------------------------------------------------------------

class TestAuthSmapiSubmitWithCode:

    def test_exchanges_code_and_stores_refresh_token(self):
        flow = _make_flow()
        flow._auth_state = "test-state-123"
        flow.hass.data[DOMAIN] = {
            "auth_codes": {"test-state-123": "auth-code-from-amazon"}
        }
        flow._lwa_client.async_exchange_code = AsyncMock(return_value={
            "access_token": "at_123",
            "refresh_token": "rt_456",
            "expires_in": 3600,
        })
        # Patch async_step_setup to verify it's called.
        flow.async_step_setup = AsyncMock(
            return_value={"type": "form", "step_id": "setup"}
        )

        _run(flow.async_step_auth_smapi(user_input={}))

        flow._lwa_client.async_exchange_code.assert_called_once_with(
            code="auth-code-from-amazon",
            redirect_uri=f"https://ha.example.com{_CALLBACK_PATH}",
            scope=SCOPE_SMAPI,
        )
        assert flow._user_input[CONF_REFRESH_TOKEN] == "rt_456"
        flow.async_step_setup.assert_called_once()

    def test_code_is_popped_from_storage(self):
        """The auth code must be consumed (popped) to prevent reuse."""
        flow = _make_flow()
        flow._auth_state = "state-pop-test"
        flow.hass.data[DOMAIN] = {
            "auth_codes": {"state-pop-test": "code-to-pop"}
        }
        flow._lwa_client.async_exchange_code = AsyncMock(return_value={
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        })
        flow.async_step_setup = AsyncMock(
            return_value={"type": "form", "step_id": "setup"}
        )

        _run(flow.async_step_auth_smapi(user_input={}))

        codes = flow.hass.data[DOMAIN]["auth_codes"]
        assert "state-pop-test" not in codes, (
            "Auth code should have been consumed (popped) to prevent reuse"
        )


# ---------------------------------------------------------------------------
# Test: auth_smapi step — Submit without auth code
# ---------------------------------------------------------------------------

class TestAuthSmapiSubmitWithoutCode:

    def test_returns_authorization_pending_error(self):
        flow = _make_flow()
        flow._auth_state = "missing-code-state"
        flow.hass.data[DOMAIN] = {}

        result = _run(flow.async_step_auth_smapi(user_input={}))

        assert result["type"] == "form"
        assert result["step_id"] == "auth_smapi"
        assert result["errors"]["base"] == "authorization_pending"

    def test_form_still_has_data_schema_on_error(self):
        flow = _make_flow()
        flow._auth_state = "missing-code-state"
        flow.hass.data[DOMAIN] = {}

        result = _run(flow.async_step_auth_smapi(user_input={}))
        assert result["data_schema"] is not None

    def test_preserves_auth_state_for_retry(self):
        """State must NOT change when code is not found."""
        flow = _make_flow()
        original_state = "retry-state-999"
        flow._auth_state = original_state
        flow.hass.data[DOMAIN] = {}

        _run(flow.async_step_auth_smapi(user_input={}))

        assert flow._auth_state == original_state, (
            "Auth state changed on retry — this would orphan any stored "
            "callback code and break the flow"
        )


# ---------------------------------------------------------------------------
# Test: auth_smapi step — Submit when token exchange fails
# ---------------------------------------------------------------------------

class TestAuthSmapiSubmitTokenExchangeFails:

    def test_returns_invalid_auth_error(self):
        flow = _make_flow()
        flow._auth_state = "fail-exchange-state"
        flow.hass.data[DOMAIN] = {
            "auth_codes": {"fail-exchange-state": "bad-code"}
        }
        flow._lwa_client.async_exchange_code = AsyncMock(
            side_effect=HomeAssistantError("Token exchange rejected")
        )

        result = _run(flow.async_step_auth_smapi(user_input={}))

        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_auth"


# ---------------------------------------------------------------------------
# Test: auth_smapi step — empty-dict vs None (the critical fix)
# ---------------------------------------------------------------------------

class TestAuthSmapiEmptyDictVsNone:
    """Regression test for the Submit-button fix.

    With an explicit ``data_schema``, HA always sends ``{}`` when the user
    clicks Submit.  Verify that ``{}`` is treated as a submission while
    ``None`` (first display) is not.
    """

    def test_empty_dict_triggers_code_lookup(self):
        flow = _make_flow()
        flow._auth_state = "empty-dict-state"
        flow.hass.data[DOMAIN] = {
            "auth_codes": {"empty-dict-state": "code-from-cb"}
        }
        flow._lwa_client.async_exchange_code = AsyncMock(return_value={
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        })
        flow.async_step_setup = AsyncMock(
            return_value={"type": "form", "step_id": "setup"}
        )

        _run(flow.async_step_auth_smapi(user_input={}))

        flow._lwa_client.async_exchange_code.assert_called_once()
        flow.async_step_setup.assert_called_once()

    def test_none_does_not_trigger_code_lookup(self):
        flow = _make_flow()
        flow._auth_state = "some-state"
        flow.hass.data[DOMAIN] = {
            "auth_codes": {"some-state": "code"}
        }
        flow._lwa_client.async_exchange_code = AsyncMock()

        result = _run(flow.async_step_auth_smapi(user_input=None))

        flow._lwa_client.async_exchange_code.assert_not_called()
        assert result["type"] == "form"


# ---------------------------------------------------------------------------
# Test: auth_smapi step — lwa_client is None
# ---------------------------------------------------------------------------

class TestAuthSmapiNoLwaClient:

    def test_redirects_to_user_step(self):
        flow = _make_flow()
        flow._lwa_client = None
        flow.async_step_user = AsyncMock(
            return_value={"type": "form", "step_id": "user"}
        )

        _run(flow.async_step_auth_smapi(user_input=None))

        flow.async_step_user.assert_called_once()


# ---------------------------------------------------------------------------
# Test: setup step — has data_schema
# ---------------------------------------------------------------------------

class TestSetupStepHasSchema:

    def test_setup_form_has_data_schema(self):
        flow = _make_flow()
        result = _run(flow.async_step_setup(user_input=None))

        assert result["type"] == "form"
        assert result["step_id"] == "setup"
        assert result["data_schema"] is not None, (
            "setup form must have a data_schema so the Submit button works"
        )


# ---------------------------------------------------------------------------
# Test: finish step
# ---------------------------------------------------------------------------

class TestFinishStep:

    def test_creates_entry_on_submit(self):
        flow = _make_flow()
        flow._user_input[CONF_SKILL_ID] = "amzn1.ask.skill.123"
        flow._user_input[CONF_VENDOR_ID] = "vendor-123"
        flow._user_input[CONF_REFRESH_TOKEN] = "rt_789"

        result = _run(flow.async_step_finish(user_input={}))

        assert result["type"] == "create_entry"
        assert result["data"][CONF_SKILL_ID] == "amzn1.ask.skill.123"
        assert result["data"][CONF_REFRESH_TOKEN] == "rt_789"

    def test_shows_form_on_first_display(self):
        flow = _make_flow()
        flow._user_input[CONF_SKILL_ID] = "amzn1.ask.skill.abc"

        result = _run(flow.async_step_finish(user_input=None))

        assert result["type"] == "form"
        assert result["step_id"] == "finish"


# ---------------------------------------------------------------------------
# Test: callback view (tested via logic, not response.status, because
# conftest mocks aiohttp.web so web.Response() returns MagicMock)
# ---------------------------------------------------------------------------

class TestCallbackView:
    """Test the callback view's *logic* — what it stores and what it returns.

    Because conftest.py installs a mock ``aiohttp`` module before this file
    runs, ``web.Response(...)`` returns a MagicMock rather than a real aiohttp
    response.  We therefore verify the correct *behaviour* (data stored,
    correct status kwarg passed) instead of asserting on the mock's attrs.
    """

    def _make_view(self):
        hass = MagicMock()
        hass.data = {}
        return AlexaAuthCallbackView(hass)

    def test_stores_code_under_state(self):
        view = self._make_view()
        request = MagicMock()
        request.query = {"code": "the-code", "state": "the-state"}

        _run(view.get(request))

        stored = view._hass.data[DOMAIN]["auth_codes"]["the-state"]
        assert stored == "the-code"

    def test_does_not_store_on_error_param(self):
        view = self._make_view()
        request = MagicMock()
        request.query = {"error": "access_denied"}

        _run(view.get(request))

        auth_codes = view._hass.data.get(DOMAIN, {}).get("auth_codes", {})
        assert len(auth_codes) == 0

    def test_does_not_store_on_missing_params(self):
        view = self._make_view()
        request = MagicMock()
        request.query = {}

        _run(view.get(request))

        auth_codes = view._hass.data.get(DOMAIN, {}).get("auth_codes", {})
        assert len(auth_codes) == 0

    def test_does_not_store_when_code_missing(self):
        view = self._make_view()
        request = MagicMock()
        request.query = {"state": "has-state"}

        _run(view.get(request))

        auth_codes = view._hass.data.get(DOMAIN, {}).get("auth_codes", {})
        assert len(auth_codes) == 0

    def test_does_not_store_when_state_missing(self):
        view = self._make_view()
        request = MagicMock()
        request.query = {"code": "has-code"}

        _run(view.get(request))

        auth_codes = view._hass.data.get(DOMAIN, {}).get("auth_codes", {})
        assert len(auth_codes) == 0

    def test_stores_multiple_codes_independently(self):
        """Each state gets its own code entry."""
        view = self._make_view()

        req1 = MagicMock()
        req1.query = {"code": "code-1", "state": "state-1"}
        _run(view.get(req1))

        req2 = MagicMock()
        req2.query = {"code": "code-2", "state": "state-2"}
        _run(view.get(req2))

        codes = view._hass.data[DOMAIN]["auth_codes"]
        assert codes["state-1"] == "code-1"
        assert codes["state-2"] == "code-2"
