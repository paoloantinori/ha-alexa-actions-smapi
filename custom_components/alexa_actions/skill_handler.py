"""Alexa skill request handler — runs inside Home Assistant.

Replaces the AWS Lambda function.  Receives Alexa POST requests directly
via the ``AlexaSkillView`` webhook, processes them using HA's internal
APIs (no HTTP roundtrips), and returns Alexa-format JSON responses.

Business logic is ported from ``lambda/lambda_function.py``.  The
``ask_sdk_core`` framework is replaced with plain JSON dispatch.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    EVENT_ALEXA_ACTIONABLE_NOTIFICATION,
    INPUT_TEXT_ENTITY,
    RESPONSE_DATE_TIME,
    RESPONSE_DURATION,
    RESPONSE_NO,
    RESPONSE_NONE,
    RESPONSE_NUMERIC,
    RESPONSE_SELECT,
    RESPONSE_STRING,
    RESPONSE_YES,
)
_COMPONENT_DIR = Path(__file__).resolve().parent

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lambda module loading (lazy, zero ask_sdk deps)
# ---------------------------------------------------------------------------

_language_strings: dict[str, dict[str, str]] | None = None

# Prompt key constants (inlined from lambda/prompts.py to avoid import dance)
ERROR_ACOUSTIC = "ERROR_ACOUSTIC"
ERROR_CONFIG = "ERROR_CONFIG"
OKAY = "OKAY"
SELECTED = "SELECTED"
STOP_MESSAGE = "STOP_MESSAGE"
NO_NOTIFICATIONS = "NO_NOTIFICATIONS"


class HaState:
    """Parsed state from the actionable-notification entity."""

    __slots__ = ("event_id", "reprompt", "suppress_confirmation", "text")

    def __init__(
        self,
        event_id: str | None,
        suppress_confirmation: bool,
        text: str | None,
        reprompt: str | None = None,
    ) -> None:
        self.event_id = event_id
        self.reprompt = reprompt
        self.suppress_confirmation = suppress_confirmation
        self.text = text


def _string_to_bool(value: Any, default: bool = False) -> bool:
    """Convert a string/bool value to a boolean."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return default
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return default


def _load_language_strings() -> dict[str, dict[str, str]]:
    """Load language_strings.json from the component directory (cached)."""
    global _language_strings
    if _language_strings is None:
        path = _COMPONENT_DIR / "language_strings.json"
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
            # Pre-compute merged locale strings (two-tier: language prefix + exact).
            merged: dict[str, dict[str, str]] = {}
            for key, strings in raw.items():
                base = key[:2]
                # Start with language-prefix base (e.g. "en" from "en-US").
                if base not in merged:
                    merged[base] = dict(strings)
                else:
                    merged[base].update(strings)
                # Then overlay the exact locale on top.
                if key != base:
                    merged[key] = dict(merged[base])
                    merged[key].update(strings)
                elif key not in merged:
                    merged[key] = dict(strings)
            _language_strings = merged
    return _language_strings


def _get_locale_strings(locale: str) -> dict[str, str]:
    """Resolve locale-specific strings (pre-computed at load time)."""
    all_strings = _load_language_strings()
    return all_strings.get(locale) or all_strings.get(locale[:2], {})


# ---------------------------------------------------------------------------
# Slot extraction helpers (replace ask_sdk_core.utils)
# ---------------------------------------------------------------------------


def _get_slot_value(request_body: dict, slot_name: str) -> str | None:
    """Extract raw slot value from an intent request."""
    slots = (
        request_body.get("request", {})
        .get("intent", {})
        .get("slots", {})
    )
    return slots.get(slot_name, {}).get("value")


def _get_resolved_slot_value(request_body: dict, slot_name: str) -> str | None:
    """Extract first ER_SUCCESS_MATCH resolution value name."""
    slot = (
        request_body.get("request", {})
        .get("intent", {})
        .get("slots", {})
        .get(slot_name, {})
    )
    resolutions = slot.get("resolutions", {}).get("resolutionsPerAuthority", [])
    for authority in resolutions:
        if authority.get("status", {}).get("code") == "ER_SUCCESS_MATCH":
            for value in authority.get("values", []):
                name = value.get("value", {}).get("name")
                if name:
                    return name
    return None


def _get_person_id(request_body: dict) -> str | None:
    """Extract Alexa person ID from request context (voice profiles)."""
    person = request_body.get("context", {}).get("System", {}).get("person")
    return person.get("personId") if person else None


def _get_locale(request_body: dict) -> str:
    """Extract locale from the Alexa request."""
    return request_body.get("request", {}).get("locale", "en-US")


# ---------------------------------------------------------------------------
# ISO 8601 duration parser (replaces isodate dependency)
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?"
)


def _parse_iso_duration(duration: str) -> float:
    """Parse ISO 8601 duration (PT[nH][nM][nS]) to total seconds."""
    m = _DURATION_RE.fullmatch(duration)
    if not m:
        raise ValueError(f"Cannot parse duration: {duration}")
    hours = float(m.group(1) or 0)
    minutes = float(m.group(2) or 0)
    seconds = float(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


# ---------------------------------------------------------------------------
# HA state access (replaces the HomeAssistant HTTP client class)
# ---------------------------------------------------------------------------


def _get_ha_state(hass: HomeAssistant) -> HaState | None:
    """Read the actionable-notification state directly from HA."""
    state = hass.states.get(INPUT_TEXT_ENTITY)
    if state is None:
        _LOGGER.warning("Entity %s not found", INPUT_TEXT_ENTITY)
        return None
    try:
        decoded = json.loads(state.state)
    except (json.JSONDecodeError, TypeError):
        _LOGGER.error("Cannot parse state of %s: %s", INPUT_TEXT_ENTITY, state.state)
        return None
    return HaState(
        event_id=decoded.get("event"),
        reprompt=decoded.get("reprompt"),
        suppress_confirmation=_string_to_bool(decoded.get("suppress_confirmation")),
        text=decoded.get("text"),
    )


def _post_ha_event(
    hass: HomeAssistant,
    ha_state: HaState,
    response: Any,
    response_type: str,
    locale_strings: dict[str, str],
    request_body: dict,
) -> str:
    """Fire the response event on the HA event bus.  Returns speak output."""
    body: dict[str, Any] = {
        "event_id": ha_state.event_id,
        "event_response": response,
        "event_response_type": response_type,
    }
    person_id = _get_person_id(request_body)
    if person_id:
        body["event_person_id"] = person_id

    hass.bus.async_fire(EVENT_ALEXA_ACTIONABLE_NOTIFICATION, body)

    if not ha_state.suppress_confirmation:
        return locale_strings.get(OKAY, "Okay")
    return ""


# ---------------------------------------------------------------------------
# Response builder (replaces handler_input.response_builder)
# ---------------------------------------------------------------------------


def _is_ssml(text: str) -> bool:
    """Return True if *text* starts with ``<speak>`` after stripping whitespace."""
    return text.lstrip().startswith("<speak>")


def _build_speech(text: str) -> dict:
    """Return an Alexa outputSpeech dict, using SSML or PlainText as appropriate."""
    if _is_ssml(text):
        return {"type": "SSML", "ssml": text}
    return {"type": "PlainText", "text": text}


def _build_response(
    speak_output: str | None = None,
    reprompt: str | None = None,
    should_end_session: bool = True,
) -> dict:
    """Build a standard Alexa skill response JSON envelope."""
    response: dict[str, Any] = {}
    if speak_output:
        response["outputSpeech"] = _build_speech(speak_output)
    if reprompt:
        response["reprompt"] = {"outputSpeech": _build_speech(reprompt)}
    response["shouldEndSession"] = should_end_session
    return {"version": "1.0", "response": response}


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------


async def _handle_launch(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """LaunchRequest — speak the notification text."""
    ha_state = _get_ha_state(hass)
    if not ha_state or not ha_state.event_id:
        speak = ls.get(NO_NOTIFICATIONS, "No pending notifications")
        return _build_response(speak_output=speak)
    reprompt = ha_state.reprompt or ha_state.text
    return _build_response(
        speak_output=ha_state.text, reprompt=reprompt, should_end_session=False,
    )


def _make_simple_response_handler(response_const: str):
    """Create a handler that reads state, posts an event, and responds."""

    async def _handler(hass: HomeAssistant, body: dict, ls: dict) -> dict:
        ha_state = _get_ha_state(hass)
        if not ha_state:
            return _build_response()
        speak = _post_ha_event(hass, ha_state, response_const, response_const, ls, body)
        return _build_response(speak_output=speak)

    return _handler


_handle_yes = _make_simple_response_handler(RESPONSE_YES)
_handle_no = _make_simple_response_handler(RESPONSE_NO)


async def _handle_number(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """Number intent — extract numeric slot, fire ResponseNumeric."""
    ha_state = _get_ha_state(hass)
    if not ha_state:
        return _build_response()
    number = _get_slot_value(body, "Numbers")
    if number == "?" or not number:
        raise ValueError("Numeric slot value could not be resolved")
    speak = _post_ha_event(hass, ha_state, number, RESPONSE_NUMERIC, ls, body)
    return _build_response(speak_output=speak)


async def _handle_string(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """String intent — extract string slot, fire ResponseString."""
    ha_state = _get_ha_state(hass)
    if not ha_state:
        return _build_response()
    strings = _get_slot_value(body, "Strings")
    speak = _post_ha_event(hass, ha_state, strings, RESPONSE_STRING, ls, body)
    return _build_response(speak_output=speak)


async def _handle_select(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """Select intent — resolve slot, fire ResponseSelect, speak selection."""
    ha_state = _get_ha_state(hass)
    if not ha_state:
        return _build_response()
    selection = _get_resolved_slot_value(body, "Selections")
    if not selection:
        raise ValueError("Selection slot value could not be resolved")
    _post_ha_event(hass, ha_state, selection, RESPONSE_SELECT, ls, body)
    template = ls.get(SELECTED, "You selected {}")
    speak = template.format(selection)
    return _build_response(speak_output=speak)


async def _handle_duration(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """Duration intent — parse ISO 8601, fire ResponseDuration with seconds."""
    ha_state = _get_ha_state(hass)
    if not ha_state:
        return _build_response()
    duration = _get_slot_value(body, "Durations")
    seconds = _parse_iso_duration(duration)
    speak = _post_ha_event(hass, ha_state, seconds, RESPONSE_DURATION, ls, body)
    return _build_response(speak_output=speak)


async def _handle_date(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """Date intent — parse date/time slots, fire ResponseDateTime."""
    ha_state = _get_ha_state(hass)
    if not ha_state:
        return _build_response()
    date_val = _get_slot_value(body, "Dates")
    time_val = _get_slot_value(body, "Times")
    if not date_val and not time_val:
        raise ValueError("Both date and time slot values are empty")
    result = {**_parse_date(date_val), **_parse_time(time_val)}
    speak = _post_ha_event(
        hass, ha_state, json.dumps(result), RESPONSE_DATE_TIME, ls, body,
    )
    return _build_response(speak_output=speak)


async def _handle_cancel_stop(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """Cancel/Stop intents — speak stop message."""
    return _build_response(speak_output=ls.get(STOP_MESSAGE, "Goodbye"))


async def _handle_fallback(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """Fallback intent — fire ResponseNone."""
    ha_state = _get_ha_state(hass)
    if ha_state:
        _post_ha_event(hass, ha_state, RESPONSE_NONE, RESPONSE_NONE, ls, body)
    return _build_response()


async def _handle_session_ended(hass: HomeAssistant, body: dict, ls: dict) -> dict:
    """SessionEndedRequest — fire ResponseNone on timeout/user-initiated."""
    reason = body.get("request", {}).get("reason", "")
    if reason in ("EXCEEDED_MAX_REPROMPTS", "USER_INITIATED"):
        ha_state = _get_ha_state(hass)
        if ha_state:
            _post_ha_event(hass, ha_state, RESPONSE_NONE, RESPONSE_NONE, ls, body)
    return _build_response()


# ---------------------------------------------------------------------------
# Date/time parsing helpers
# ---------------------------------------------------------------------------


def _parse_date(date: str | None) -> dict[str, str | None]:
    """Parse an Alexa date string ``YYYY-MM-DD`` into components."""
    result: dict[str, str | None] = {"day": None, "month": None, "year": None}
    if not date:
        return result
    parts = date.split("-")
    result["year"] = parts[0] if len(parts) >= 1 else None
    result["month"] = parts[1] if len(parts) >= 2 else None
    result["day"] = parts[2] if len(parts) >= 3 else None
    return result


def _parse_time(time: str | None) -> dict[str, str | None]:
    """Parse an Alexa time string (``HH:MM`` or suffixed) into components."""
    result: dict[str, str | None] = {"seconds": None, "minute": None, "hour": None}
    if not time:
        return result
    lower = time.lower()
    if "s" in lower:
        result["seconds"] = lower.replace("s", "")
        return result
    if "m" in lower:
        result["minute"] = lower.replace("m", "")
        return result
    if "h" in lower:
        result["hour"] = lower.replace("h", "")
        return result
    parts = time.split(":")
    result["hour"] = parts[0] if len(parts) >= 1 else None
    result["minute"] = parts[1] if len(parts) >= 2 else None
    result["seconds"] = parts[2] if len(parts) >= 3 else None
    return result


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

_INTENT_HANDLERS: dict[str, Any] = {
    "AMAZON.YesIntent": _handle_yes,
    "AMAZON.NoIntent": _handle_no,
    "Number": _handle_number,
    "String": _handle_string,
    "Select": _handle_select,
    "Duration": _handle_duration,
    "Date": _handle_date,
    "AMAZON.CancelIntent": _handle_cancel_stop,
    "AMAZON.StopIntent": _handle_cancel_stop,
    "AMAZON.FallbackIntent": _handle_fallback,
}

_REQUEST_TYPE_HANDLERS: dict[str, Any] = {
    "LaunchRequest": _handle_launch,
    "SessionEndedRequest": _handle_session_ended,
}


async def handle_alexa_request(hass: HomeAssistant, request_body: dict) -> dict:
    """Main entry point — dispatch an incoming Alexa request.

    Args:
        hass: Home Assistant instance for state access and event firing.
        request_body: The raw JSON body of the Alexa POST request.

    Returns:
        Alexa-format response dict.
    """
    locale_strings = _get_locale_strings(_get_locale(request_body))

    try:
        req = request_body.get("request", {})
        req_type = req.get("type", "")

        if req_type == "IntentRequest":
            intent_name = req.get("intent", {}).get("name", "")
            handler = _INTENT_HANDLERS.get(intent_name)
            if handler:
                return await handler(hass, request_body, locale_strings)
            _LOGGER.warning("Unhandled intent: %s", intent_name)
            return _build_response()

        handler = _REQUEST_TYPE_HANDLERS.get(req_type)
        if handler:
            return await handler(hass, request_body, locale_strings)

        _LOGGER.warning("Unhandled request type: %s", req_type)
        return _build_response()

    except Exception:  # noqa: BLE001
        _LOGGER.exception("Error processing Alexa request")
        try:
            ha_state = _get_ha_state(hass)
            if ha_state and ha_state.text:
                speak = locale_strings.get(
                    ERROR_ACOUSTIC,
                    "There was an error with the acoustic request: {text}",
                ).format(text=ha_state.text)
                return _build_response(
                    speak_output=speak, reprompt="", should_end_session=False,
                )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not read HA state for error recovery")
        speak = locale_strings.get(
            ERROR_CONFIG, "There was an error with the skill configuration.",
        )
        return _build_response(speak_output=speak)
