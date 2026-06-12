"""Alexa Actionable Notifications integration."""
from __future__ import annotations

import json
import logging
import uuid

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback

from .const import (
    CONF_LOCALES,
    CONF_REFRESH_TOKEN,
    CONF_SKILL_ID,
    DOMAIN,
    EVENT_ALEXA_ACTIONABLE_NOTIFICATION,
    INPUT_TEXT_ENTITY,
    SCOPE_PROACTIVE,
    SCOPE_SMAPI,
    SERVICE_SEND,
    SERVICE_SEND_PROACTIVE,
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
        vol.Optional("display_title"): str,
        vol.Optional("display_body"): str,
        # HA UI may inject entity_id into data; tolerate it silently.
        vol.Optional("entity_id"): str,
    }
)

SERVICE_SEND_PROACTIVE_SCHEMA = vol.Schema(
    {
        vol.Required("text"): str,
        vol.Optional("event_type", default="AMAZON.MessageAlert.Activated"): str,
        vol.Optional("locale"): str,
        vol.Optional("reference_id"): str,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alexa Actions from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    async def async_send_notification(call: ServiceCall) -> None:
        """Handle the alexa_actions.send service call.

        Appends the notification to a JSON-array queue stored in the
        input_text entity.  The first element is the "active" notification;
        additional elements are queued and delivered sequentially after the
        active one receives a response.

        Backward compatible: if the existing entity state is a single dict
        (pre-queue format), it is wrapped into a one-element array before
        appending.
        """
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
            "alexa_device": alexa_device,
        }
        if options:
            payload["options"] = options

        # Conditionally include optional payload fields.
        # Each field is read from the validated service call data and
        # added to the payload only when truthy (keeps payload lean
        # and avoids storing empty strings / empty dicts in the entity).
        for _key in ("reprompt", "dialog", "display_title", "display_body"):
            _val = call.data.get(_key)
            if _val:
                payload[_key] = _val

        # --- Queue-based storage ---
        # Read the current entity state and parse it as a JSON array.
        # Backward compat: a single dict (pre-queue format) is wrapped.
        queue: list[dict] = []
        current_state = hass.states.get(INPUT_TEXT_ENTITY)
        if current_state and current_state.state:
            try:
                decoded = json.loads(current_state.state)
                if isinstance(decoded, list):
                    queue = decoded
                elif isinstance(decoded, dict):
                    # Legacy single-notification format — wrap in array.
                    # Inject alexa_device if missing so it can be used for
                    # auto-trigger when this notification is eventually
                    # processed after the new one completes.
                    decoded.setdefault("alexa_device", alexa_device)
                    queue = [decoded]
            except (json.JSONDecodeError, TypeError):
                _LOGGER.warning("Could not parse existing queue state, overwriting")

        was_empty = len(queue) == 0
        queue.append(payload)
        await _async_set_input_text_state(hass, json.dumps(queue))

        # Dynamic slot update: fire-and-forget SMAPI call to update the
        # Selections slot type with the provided options.  The model build
        # on Amazon's side takes 30-180 s; we do NOT wait for it.  If the
        # build completes before the user speaks, Alexa matches exactly.
        # Otherwise, skill_handler falls back to text matching.
        if options:
            try:
                from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET

                from .api import LWAClient
                from .smapi import SMAPI

                refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
                locales = entry.data.get(CONF_LOCALES, ["en-US"])
                client_id = entry.data.get(CONF_CLIENT_ID)
                client_secret = entry.data.get(CONF_CLIENT_SECRET)

                if refresh_token and client_id and client_secret and locales:
                    lwa = LWAClient(hass, client_id, client_secret)
                    lwa.set_refresh_token(SCOPE_SMAPI, refresh_token)
                    smapi_client = SMAPI(lwa)
                    # Update first locale only (all locales share the slot type)
                    hass.async_create_task(
                        smapi_client.async_update_slot_type(
                            skill_id, locales[0], options,
                        )
                    )
                    _LOGGER.debug(
                        "Triggered SMAPI slot update for %d options", len(options),
                    )
            except Exception:
                _LOGGER.warning(
                    "SMAPI slot update failed (non-fatal)", exc_info=True,
                )

        # Only trigger play_media when the queue was empty (this is the
        # first / only notification).  When items are already queued, the
        # auto-trigger in handle_response will invoke subsequent ones.
        if was_empty:
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
        else:
            _LOGGER.info(
                "Queued notification (position %d): event_id=%s, device=%s",
                len(queue), event_id, alexa_device,
            )

    hass.services.async_register(
        DOMAIN, SERVICE_SEND, async_send_notification, schema=SERVICE_SEND_SCHEMA
    )

    # ------------------------------------------------------------------
    # Proactive Events service
    # ------------------------------------------------------------------

    async def async_send_proactive(call: ServiceCall) -> None:
        """Handle the alexa_actions.send_proactive service call.

        Sends a proactive notification via the Alexa Proactive Events API.
        This does NOT require ``play_media`` or ``alexa_media`` — Amazon
        pushes the event directly to the user's devices.
        """
        text = call.data["text"]
        event_type = call.data["event_type"]
        locale = call.data.get(
            "locale",
            entry.data.get(CONF_LOCALES, ["en-US"])[0],
        )
        reference_id = call.data.get("reference_id")

        try:
            from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET

            from .api import LWAClient
            from .proactive_events import ProactiveEventsClient

            refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
            client_id = entry.data.get(CONF_CLIENT_ID)
            client_secret = entry.data.get(CONF_CLIENT_SECRET)

            if not all([refresh_token, client_id, client_secret]):
                _LOGGER.error(
                    "Cannot send proactive event — missing LWA credentials. "
                    "Re-configure the integration."
                )
                return

            lwa = LWAClient(hass, client_id, client_secret)
            # Register refresh token for both scopes so the client can try
            # the proactive-events scope first, then fall back to SMAPI.
            lwa.set_refresh_token(SCOPE_SMAPI, refresh_token)
            lwa.set_refresh_token(SCOPE_PROACTIVE, refresh_token)

            client = ProactiveEventsClient(lwa)
            result = await client.async_send_notification(
                text=text,
                event_type=event_type,
                locale=locale,
                reference_id=reference_id,
            )
            _LOGGER.info(
                "Proactive event sent: event_type=%s, result=%s",
                event_type,
                result,
            )
        except Exception:
            _LOGGER.error(
                "Failed to send proactive event", exc_info=True,
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_PROACTIVE,
        async_send_proactive,
        schema=SERVICE_SEND_PROACTIVE_SCHEMA,
    )

    # Register the Alexa skill webhook view.
    from .views import AlexaSkillView
    hass.http.register_view(AlexaSkillView(hass))

    @callback
    def handle_response(event) -> None:
        """Handle response events from the skill handler.

        After a notification receives its response, check whether more
        notifications remain in the queue and trigger the next one.
        """
        _LOGGER.info(
            "Received Alexa response: event_id=%s, type=%s, response=%s",
            event.data.get("event_id"),
            event.data.get("event_response_type"),
            str(event.data.get("event_response", ""))[:100],
        )

        # Auto-trigger next queued notification.
        # After _advance_queue removes the completed item, the new first
        # element (if any) is the next notification to deliver.
        current = hass.states.get(INPUT_TEXT_ENTITY)
        if not current or not current.state:
            return
        try:
            queue = json.loads(current.state)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(queue, list) or not queue:
            return

        next_item = queue[0]
        device = next_item.get("alexa_device", "")
        skill_id = entry.data.get(CONF_SKILL_ID, "")
        if device and skill_id:
            hass.async_create_task(
                hass.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": device,
                        "media_content_id": skill_id,
                        "media_content_type": "skill",
                    },
                    blocking=False,
                )
            )
            _LOGGER.info(
                "Auto-triggering next queued notification: event_id=%s, device=%s",
                next_item.get("event"), device,
            )

    remove_listener = hass.bus.async_listen(EVENT_ALEXA_ACTIONABLE_NOTIFICATION, handle_response)

    hass.data[DOMAIN][f"{entry.entry_id}_unload"] = [
        lambda: hass.services.async_remove(DOMAIN, SERVICE_SEND),
        lambda: hass.services.async_remove(DOMAIN, SERVICE_SEND_PROACTIVE),
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
        hass.services.async_remove(DOMAIN, SERVICE_SEND_PROACTIVE)

    return True


async def _async_set_input_text_state(hass: HomeAssistant, value: str) -> None:
    """Set the state of the input_text entity."""
    hass.states.async_set(INPUT_TEXT_ENTITY, value)
