"""Alexa Actionable Notifications integration."""
from __future__ import annotations

import json
import logging
import uuid

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback

from .const import (
    CONF_SKILL_ID,
    DOMAIN,
    EVENT_ALEXA_ACTIONABLE_NOTIFICATION,
    INPUT_TEXT_ENTITY,
    SERVICE_SEND,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_SCHEMA = vol.Schema(
    {
        vol.Required("text"): str,
        vol.Optional("alexa_device"): str,
        vol.Optional("event_id"): str,
        vol.Optional("reprompt"): str,
        vol.Optional("suppress_confirmation", default=False): bool,
        vol.Optional("options"): [str],
        vol.Optional("dialog"): dict,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alexa Actions from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    async def async_send_notification(call: ServiceCall) -> None:
        """Handle the alexa_actions.send service call."""
        text = call.data["text"]
        # Support both: data.alexa_device (direct YAML) and
        # target.entity_id (blueprints / HA UI service calls).
        alexa_device = call.data.get("alexa_device") or ""
        if not alexa_device and call.target and call.target.entity_id:
            alexa_device = next(iter(call.target.entity_id), "")
        if not alexa_device:
            _LOGGER.error("alexa_device is required — pass via data or target")
            return

        event_id = call.data.get("event_id", str(uuid.uuid4()))
        suppress_confirmation = call.data["suppress_confirmation"]
        options = call.data.get("options", [])

        # Validate skill_id early, before writing state.
        skill_id = entry.data.get(CONF_SKILL_ID, "")
        if not skill_id:
            _LOGGER.error("No skill_id configured — cannot invoke skill")
            return

        payload = {
            "text": text,
            "event": event_id,
            "suppress_confirmation": str(suppress_confirmation).lower(),
        }
        if options:
            payload["options"] = options

        reprompt = call.data.get("reprompt")
        if reprompt:
            payload["reprompt"] = reprompt

        dialog = call.data.get("dialog")
        if dialog:
            payload["dialog"] = dialog

        # Write payload to input_text entity
        await _async_set_input_text_state(hass, json.dumps(payload))

        # Trigger Alexa skill via SkillConnections.Launch (direct by ID).
        # Uses media_content_type "skill" so alexa_media uses its
        # run_skill() path (POST to /api/behaviors/preview with
        # Alexa.Operation.SkillConnections.Launch), which sends a
        # LaunchRequest directly to our HTTPS webhook endpoint.
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": alexa_device,
                "media_content_id": skill_id,
                "media_content_type": "skill",
            },
            blocking=False,
        )
        _LOGGER.info(
            "Sent actionable notification: event_id=%s, device=%s",
            event_id, alexa_device,
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SEND, async_send_notification, schema=SERVICE_SEND_SCHEMA
    )

    # Register the Alexa skill webhook view.
    from .views import AlexaSkillView
    hass.http.register_view(AlexaSkillView(hass))

    @callback
    def handle_response(event) -> None:
        """Handle response events from the skill handler."""
        _LOGGER.info(
            "Received Alexa response: event_id=%s, type=%s, response=%s",
            event.data.get("event_id"),
            event.data.get("event_response_type"),
            str(event.data.get("event_response", ""))[:100],
        )

    remove_listener = hass.bus.async_listen(EVENT_ALEXA_ACTIONABLE_NOTIFICATION, handle_response)

    hass.data[DOMAIN][f"{entry.entry_id}_unload"] = [
        lambda: hass.services.async_remove(DOMAIN, SERVICE_SEND),
        remove_listener,
    ]

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_funcs = hass.data[DOMAIN].pop(f"{entry.entry_id}_unload", [])
    for func in unload_funcs:
        func()

    hass.data[DOMAIN].pop(entry.entry_id, None)

    # Remove service if no more entries
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_SEND)

    return True


async def _async_set_input_text_state(hass: HomeAssistant, value: str) -> None:
    """Set the state of the input_text entity."""
    hass.states.async_set(INPUT_TEXT_ENTITY, value)
