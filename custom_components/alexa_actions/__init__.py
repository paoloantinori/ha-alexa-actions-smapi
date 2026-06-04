"""Alexa Actionable Notifications integration."""
from __future__ import annotations

import json
import logging
import uuid

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback

from .const import (
    DOMAIN,
    INPUT_TEXT_ENTITY,
    SERVICE_SEND,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_SCHEMA = vol.Schema(
    {
        vol.Required("text"): str,
        vol.Optional("event_id"): str,
        vol.Optional("suppress_confirmation", default=False): bool,
        vol.Optional("options"): [str],
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alexa Actions from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    async def async_send_notification(call: ServiceCall) -> None:
        """Handle the alexa_actions.send service call."""
        text = call.data["text"]
        event_id = call.data.get("event_id", str(uuid.uuid4()))
        suppress_confirmation = call.data["suppress_confirmation"]
        options = call.data.get("options", [])

        payload = {
            "text": text,
            "event": event_id,
            "suppress_confirmation": str(suppress_confirmation).lower(),
        }
        if options:
            payload["options"] = options

        # Write payload to input_text entity
        await _async_set_input_text_state(hass, json.dumps(payload))

        # Trigger Alexa skill via media_player.play_media
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "media_content_id": "alexActionsSkillAutomation",
                "media_content_type": "custom",
            },
            blocking=False,
        )
        _LOGGER.info("Sent actionable notification: event_id=%s", event_id)

    hass.services.async_register(
        DOMAIN, SERVICE_SEND, async_send_notification, schema=SERVICE_SEND_SCHEMA
    )

    @callback
    def handle_response(event) -> None:
        """Handle response events from Lambda."""
        _LOGGER.info(
            "Received Alexa response: event_id=%s, type=%s, response=%s",
            event.data.get("event_id"),
            event.data.get("event_response_type"),
            str(event.data.get("event_response", ""))[:100],
        )

    remove_listener = hass.bus.async_listen("alexa_actionable_notification", handle_response)

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
