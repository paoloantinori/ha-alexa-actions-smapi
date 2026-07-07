"""HTTP views for the Alexa Actions SMAPI integration."""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .const import CONF_PERSON_MAP, DOMAIN

_LOGGER = logging.getLogger(__name__)


class AlexaSkillView(HomeAssistantView):
    """Handle incoming Alexa skill requests (POST webhook).

    Alexa sends POST requests here when the skill is invoked.  The request
    body is the standard Alexa JSON envelope.  We delegate to
    ``skill_handler.handle_alexa_request`` which processes it natively
    inside Home Assistant (no Lambda needed).
    """

    url = "/api/alexa_actions/skill"
    name = "api:alexa_actions:skill"
    requires_auth = False

    def __init__(self, hass) -> None:
        """Initialize the skill view."""
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        """Process an incoming Alexa skill request."""
        from .skill_handler import handle_alexa_request

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Failed to parse Alexa request body")
            return web.json_response(
                {"version": "1.0", "response": {"shouldEndSession": True}},
                status=400,
            )

        # Diagnostic: log every incoming Alexa skill request (DEBUG, so it is silent
        # unless `custom_components.alexa_actions` is set to debug via logger.set_level).
        _req = body.get("request", {}) if isinstance(body, dict) else {}
        _sess = body.get("session") if isinstance(body, dict) else {}
        _LOGGER.debug(
            "ALEXA_WEBHOOK_IN type=%s intent=%s reason=%s session_new=%s session_attrs=%s locale=%s",
            _req.get("type"),
            (_req.get("intent") or {}).get("name"),
            _req.get("reason"),
            _sess.get("new"),
            list((_sess.get("attributes") or {}).keys()),
            _req.get("locale"),
        )

        # Resolve person_map from the first config entry's options.
        person_map: dict[str, str] | None = None
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if entries:
            person_map = entries[0].options.get(CONF_PERSON_MAP)

        response = await handle_alexa_request(self._hass, body, person_map)
        # Diagnostic: log the response we return (DEBUG).
        _resp = response.get("response", {}) if isinstance(response, dict) else {}
        _LOGGER.debug(
            "ALEXA_WEBHOOK_OUT shouldEndSession=%s has_output=%s has_reprompt=%s directives=%s",
            _resp.get("shouldEndSession"),
            bool(_resp.get("outputSpeech")),
            bool(_resp.get("reprompt")),
            [d.get("type") for d in (_resp.get("directives") or [])],
        )
        return web.json_response(response)


class AlexaAuthCallbackView(HomeAssistantView):
    """Handle the OAuth2 callback from Amazon LWA."""

    url = "/auth/alexa_actions/callback"
    name = "auth:alexa_actions:callback"
    requires_auth = False

    def __init__(self, hass) -> None:
        """Initialize the callback view."""
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Process the LWA OAuth2 callback.

        Amazon redirects here with ``code`` and ``state`` query parameters
        after the user grants access.  We store the code under the state key
        so the config flow can pick it up.
        """
        code = request.query.get("code")
        state = request.query.get("state")
        error = request.query.get("error")

        if error:
            _LOGGER.warning("LWA auth callback received error: %s", error)
            return web.Response(
                text="<html><body><h2>Authorization failed.</h2>"
                f"<p>Error: {error}</p></body></html>",
                content_type="text/html",
                status=400,
            )

        if not code or not state:
            _LOGGER.warning(
                "LWA auth callback missing params: code=%s, state=%s",
                "present" if code else "MISSING",
                "present" if state else "MISSING",
            )
            return web.Response(
                text="<html><body><h2>Missing parameters.</h2>"
                "<p>Required parameters code and/or state are missing.</p>"
                "</body></html>",
                content_type="text/html",
                status=400,
            )

        # Store the auth code keyed by state so the config flow can retrieve it.
        self._hass.data.setdefault(DOMAIN, {}).setdefault("auth_codes", {})[
            state
        ] = code
        _LOGGER.info(
            "LWA auth callback stored code for state=%s", state,
        )

        return web.Response(
            text="<html><body><h2>Authorization successful!</h2>"
            "<p>Return to Home Assistant to complete setup.</p>"
            "</body></html>",
            content_type="text/html",
        )
