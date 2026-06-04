"""HTTP views for the Alexa Actions SMAPI integration."""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

        return web.Response(
            text="<html><body><h2>Authorization successful!</h2>"
            "<p>Return to Home Assistant to complete setup.</p>"
            "</body></html>",
            content_type="text/html",
        )
