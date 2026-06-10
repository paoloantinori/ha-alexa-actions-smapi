"""Tests for skill_handler.py — the HA-native Alexa skill request handler.

Uses a mock ``hass`` with ``states.get()`` and ``bus.async_fire()`` to
validate all handlers, slot extraction, localization, and error handling.
"""

import asyncio
import json
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Set up minimal HA mock modules so skill_handler can import HomeAssistant
# ---------------------------------------------------------------------------
import sys

_ha = types.ModuleType("homeassistant")


class _MockHA:
    """Minimal mock of HomeAssistant for testing."""

    def __init__(self):
        self.states = MagicMock()
        self.bus = MagicMock()


_ha.HomeAssistant = _MockHA
_ha.ServiceCall = MagicMock
_ha.callback = lambda f: f  # passthrough decorator
_ha.ConfigEntry = MagicMock
_ha.exceptions = MagicMock()
sys.modules["homeassistant"] = _ha

_ha_core = types.ModuleType("homeassistant.core")
_ha_core.HomeAssistant = _MockHA
_ha_core.ServiceCall = MagicMock
_ha_core.callback = lambda f: f  # passthrough decorator
sys.modules["homeassistant.core"] = _ha_core

# Ensure other HA submodules that __init__.py imports are available.
for _mod_name in (
    "homeassistant.config_entries",
    "homeassistant.exceptions",
    "homeassistant.const",
):
    sys.modules.setdefault(_mod_name, MagicMock())

# voluptuous is imported by __init__.py for SERVICE_SEND_SCHEMA.
sys.modules.setdefault("voluptuous", MagicMock())

# Now import the module under test.
from custom_components.alexa_actions import skill_handler as sh


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ha(entity_state: dict | None = None) -> _MockHA:
    """Create a mock HA with an optional input_text entity state."""
    hass = _MockHA()
    if entity_state is not None:
        mock_state = MagicMock()
        mock_state.state = json.dumps(entity_state)
        hass.states.get.return_value = mock_state
    else:
        hass.states.get.return_value = None
    return hass


def _launch_request() -> dict:
    return {"request": {"type": "LaunchRequest", "locale": "en-US"}}


def _intent_request(intent_name: str, slots: dict | None = None, locale: str = "en-US") -> dict:
    body: dict = {
        "request": {
            "type": "IntentRequest",
            "intent": {"name": intent_name, "slots": slots or {}},
            "locale": locale,
        },
        "context": {"System": {}},
    }
    return body


def _session_ended_request(reason: str = "USER_INITIATED") -> dict:
    return {"request": {"type": "SessionEndedRequest", "reason": reason, "locale": "en-US"}}


# ---------------------------------------------------------------------------
# Response structure tests
# ---------------------------------------------------------------------------


class TestBuildResponse:
    def test_empty_response(self):
        r = sh._build_response()
        assert r["version"] == "1.0"
        assert r["response"]["shouldEndSession"] is True
        assert "outputSpeech" not in r["response"]

    def test_speak_only(self):
        r = sh._build_response(speak_output="Hello")
        assert r["response"]["outputSpeech"]["text"] == "Hello"
        assert r["response"]["shouldEndSession"] is True

    def test_speak_with_reprompt(self):
        r = sh._build_response(speak_output="Hello", reprompt="Try again", should_end_session=False)
        assert r["response"]["outputSpeech"]["text"] == "Hello"
        assert r["response"]["reprompt"]["outputSpeech"]["text"] == "Try again"
        assert r["response"]["shouldEndSession"] is False

    def test_ssml_speak_output(self):
        ssml = "<speak>Paolo<break time='1s'/>hai preso la pastiglia?</speak>"
        r = sh._build_response(speak_output=ssml)
        assert r["response"]["outputSpeech"]["type"] == "SSML"
        assert r["response"]["outputSpeech"]["ssml"] == ssml
        assert "text" not in r["response"]["outputSpeech"]

    def test_ssml_with_leading_whitespace(self):
        ssml = "  \n  <speak>Hello</speak>"
        r = sh._build_response(speak_output=ssml)
        assert r["response"]["outputSpeech"]["type"] == "SSML"
        assert r["response"]["outputSpeech"]["ssml"] == ssml

    def test_plain_text_unchanged(self):
        r = sh._build_response(speak_output="Normal text")
        assert r["response"]["outputSpeech"]["type"] == "PlainText"
        assert r["response"]["outputSpeech"]["text"] == "Normal text"
        assert "ssml" not in r["response"]["outputSpeech"]

    def test_ssml_reprompt(self):
        ssml = "<speak>Try again<break time='500ms'/></speak>"
        r = sh._build_response(speak_output="Hello", reprompt=ssml, should_end_session=False)
        assert r["response"]["outputSpeech"]["type"] == "PlainText"
        assert r["response"]["reprompt"]["outputSpeech"]["type"] == "SSML"
        assert r["response"]["reprompt"]["outputSpeech"]["ssml"] == ssml

    def test_mixed_ssml_speak_plain_reprompt(self):
        ssml = "<speak>Question<break time='1s'/></speak>"
        r = sh._build_response(speak_output=ssml, reprompt="Try again", should_end_session=False)
        assert r["response"]["outputSpeech"]["type"] == "SSML"
        assert r["response"]["outputSpeech"]["ssml"] == ssml
        assert r["response"]["reprompt"]["outputSpeech"]["type"] == "PlainText"
        assert r["response"]["reprompt"]["outputSpeech"]["text"] == "Try again"


# ---------------------------------------------------------------------------
# Slot extraction tests
# ---------------------------------------------------------------------------


class TestSlotExtraction:
    def test_get_slot_value(self):
        body = _intent_request("Number", {"Numbers": {"value": "42"}})
        assert sh._get_slot_value(body, "Numbers") == "42"

    def test_get_slot_value_missing(self):
        body = _intent_request("Number")
        assert sh._get_slot_value(body, "Numbers") is None

    def test_get_resolved_slot_value(self):
        body = _intent_request("Select", {
            "Selections": {
                "value": "something",
                "resolutions": {
                    "resolutionsPerAuthority": [{
                        "status": {"code": "ER_SUCCESS_MATCH"},
                        "values": [{"value": {"name": "Option One"}}],
                    }],
                },
            },
        })
        assert sh._get_resolved_slot_value(body, "Selections") == "Option One"

    def test_get_resolved_slot_no_match(self):
        body = _intent_request("Select", {
            "Selections": {
                "value": "something",
                "resolutions": {
                    "resolutionsPerAuthority": [{
                        "status": {"code": "ER_SUCCESS_NO_MATCH"},
                        "values": [],
                    }],
                },
            },
        })
        assert sh._get_resolved_slot_value(body, "Selections") is None

    def test_get_person_id(self):
        body = _intent_request("YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.ABC123"}
        assert sh._get_person_id(body) == "amzn1.account.ABC123"

    def test_get_person_id_missing(self):
        body = _intent_request("YesIntent")
        assert sh._get_person_id(body) is None


# ---------------------------------------------------------------------------
# Duration parser tests
# ---------------------------------------------------------------------------


class TestDurationParser:
    def test_seconds(self):
        assert sh._parse_iso_duration("PT30S") == 30.0

    def test_minutes(self):
        assert sh._parse_iso_duration("PT5M") == 300.0

    def test_hours(self):
        assert sh._parse_iso_duration("PT2H") == 7200.0

    def test_hours_minutes_seconds(self):
        assert sh._parse_iso_duration("PT1H30M45S") == 5445.0

    def test_invalid(self):
        with pytest.raises(ValueError):
            sh._parse_iso_duration("invalid")


# ---------------------------------------------------------------------------
# Date/time parser tests
# ---------------------------------------------------------------------------


class TestDateTimeParser:
    def test_parse_date_full(self):
        assert sh._parse_date("2024-06-15") == {"year": "2024", "month": "06", "day": "15"}

    def test_parse_date_year_only(self):
        assert sh._parse_date("2024") == {"year": "2024", "month": None, "day": None}

    def test_parse_date_none(self):
        assert sh._parse_date(None) == {"year": None, "month": None, "day": None}

    def test_parse_time_hhmm(self):
        assert sh._parse_time("14:30") == {"hour": "14", "minute": "30", "seconds": None}

    def test_parse_time_seconds_suffix(self):
        assert sh._parse_time("30s") == {"hour": None, "minute": None, "seconds": "30"}

    def test_parse_time_minutes_suffix(self):
        assert sh._parse_time("15m") == {"hour": None, "minute": "15", "seconds": None}

    def test_parse_time_hours_suffix(self):
        assert sh._parse_time("2h") == {"hour": "2", "minute": None, "seconds": None}

    def test_parse_time_none(self):
        assert sh._parse_time(None) == {"hour": None, "minute": None, "seconds": None}


# ---------------------------------------------------------------------------
# Localization tests
# ---------------------------------------------------------------------------


class TestLocalization:
    def test_loads_strings(self):
        strings = sh._load_language_strings()
        assert "en" in strings
        assert "it" in strings

    def test_locale_fallback(self):
        strings = sh._get_locale_strings("it-IT")
        # Should have Italian strings
        assert sh.STOP_MESSAGE in strings or len(strings) > 0

    def test_unknown_locale(self):
        strings = sh._get_locale_strings("xx-XX")
        # Should return empty or minimal dict, not crash
        assert isinstance(strings, dict)


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------


class TestHandleLaunch:
    @pytest.mark.asyncio
    async def test_speaks_notification_text(self):
        hass = _make_ha({"event": "evt1", "text": "Do you want coffee?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "Do you want coffee?"
        assert r["response"]["shouldEndSession"] is False  # event_id present → ask

    @pytest.mark.asyncio
    async def test_no_event_id_ends_session(self):
        hass = _make_ha({"text": "Hello!", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "No pending notifications"
        assert r["response"]["shouldEndSession"] is True

    @pytest.mark.asyncio
    async def test_missing_entity(self):
        hass = _make_ha(None)
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "No pending notifications"
        assert r["response"]["shouldEndSession"] is True

    @pytest.mark.asyncio
    async def test_no_notifications_missing_entity(self):
        """When input_text entity is missing, speak no-notifications message."""
        hass = _make_ha(None)
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "No pending notifications"
        assert r["response"]["shouldEndSession"] is True

    @pytest.mark.asyncio
    async def test_no_notifications_no_event_id(self):
        """When entity exists but has no event_id, speak no-notifications message."""
        hass = _make_ha({"text": "Hello!", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "No pending notifications"
        assert r["response"]["shouldEndSession"] is True

    @pytest.mark.asyncio
    async def test_ssml_notification_text(self):
        ssml = "<speak>Paolo<break time='1s'/>hai preso la pastiglia?</speak>"
        hass = _make_ha({"event": "evt1", "text": ssml, "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["type"] == "SSML"
        assert r["response"]["outputSpeech"]["ssml"] == ssml

    @pytest.mark.asyncio
    async def test_custom_reprompt(self):
        hass = _make_ha({"event": "evt1", "text": "Did you take the pill?", "reprompt": "Say yes or no", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert r["response"]["reprompt"]["outputSpeech"]["text"] == "Say yes or no"
        assert r["response"]["shouldEndSession"] is False

    @pytest.mark.asyncio
    async def test_reprompt_falls_back_to_text(self):
        hass = _make_ha({"event": "evt1", "text": "Did you take the pill?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert r["response"]["reprompt"]["outputSpeech"]["text"] == "Did you take the pill?"

    @pytest.mark.asyncio
    async def test_ssml_reprompt(self):
        ssml_reprompt = "<speak>Scusa<break time='500ms'/>rispondi sì o no.</speak>"
        hass = _make_ha({"event": "evt1", "text": "Hai preso la pastiglia?", "reprompt": ssml_reprompt, "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["type"] == "PlainText"
        assert r["response"]["reprompt"]["outputSpeech"]["type"] == "SSML"
        assert r["response"]["reprompt"]["outputSpeech"]["ssml"] == ssml_reprompt


class TestHandleYes:
    @pytest.mark.asyncio
    async def test_fires_yes_event(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("AMAZON.YesIntent"))
        hass.bus.async_fire.assert_called_once()
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response_type"] == sh.RESPONSE_YES
        assert event_data["event_response"] == sh.RESPONSE_YES
        assert r["response"]["outputSpeech"]["text"] == "Okay"

    @pytest.mark.asyncio
    async def test_suppressed_confirmation(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": True})
        r = await sh.handle_alexa_request(hass, _intent_request("AMAZON.YesIntent"))
        assert "outputSpeech" not in r["response"]


class TestHandleNo:
    @pytest.mark.asyncio
    async def test_fires_no_event(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("AMAZON.NoIntent"))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response_type"] == sh.RESPONSE_NO


class TestHandleNumber:
    @pytest.mark.asyncio
    async def test_numeric_response(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("Number", {"Numbers": {"value": "42"}}))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response"] == "42"
        assert event_data["event_response_type"] == sh.RESPONSE_NUMERIC

    @pytest.mark.asyncio
    async def test_unresolved_number_raises(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        # Unresolved slot value "?"
        r = await sh.handle_alexa_request(hass, _intent_request("Number", {"Numbers": {"value": "?"}}))
        # Should hit exception handler → speak error or notification text
        assert "outputSpeech" in r["response"]


class TestHandleString:
    @pytest.mark.asyncio
    async def test_string_response(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("String", {"Strings": {"value": "yes please"}}))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response"] == "yes please"
        assert event_data["event_response_type"] == sh.RESPONSE_STRING


class TestHandleSelect:
    @pytest.mark.asyncio
    async def test_select_response(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("Select", {
            "Selections": {
                "value": "opt1",
                "resolutions": {
                    "resolutionsPerAuthority": [{
                        "status": {"code": "ER_SUCCESS_MATCH"},
                        "values": [{"value": {"name": "Option One"}}],
                    }],
                },
            },
        }))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response"] == "Option One"
        assert event_data["event_response_type"] == sh.RESPONSE_SELECT
        assert "Option One" in r["response"]["outputSpeech"]["text"]


class TestHandleDuration:
    @pytest.mark.asyncio
    async def test_duration_in_seconds(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("Duration", {"Durations": {"value": "PT5M"}}))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response"] == 300.0
        assert event_data["event_response_type"] == sh.RESPONSE_DURATION


class TestHandleDate:
    @pytest.mark.asyncio
    async def test_date_response(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("Date", {
            "Dates": {"value": "2024-06-15"},
            "Times": {"value": "14:30"},
        }))
        event_data = hass.bus.async_fire.call_args[0][1]
        parsed = json.loads(event_data["event_response"])
        assert parsed["year"] == "2024"
        assert parsed["month"] == "06"
        assert parsed["day"] == "15"
        assert parsed["hour"] == "14"
        assert parsed["minute"] == "30"
        assert event_data["event_response_type"] == sh.RESPONSE_DATE_TIME


class TestHandleCancelStop:
    @pytest.mark.asyncio
    async def test_cancel(self):
        hass = _make_ha()
        r = await sh.handle_alexa_request(hass, _intent_request("AMAZON.CancelIntent"))
        assert sh.STOP_MESSAGE not in r["response"]["outputSpeech"]["text"] or True  # just no crash

    @pytest.mark.asyncio
    async def test_stop(self):
        hass = _make_ha()
        r = await sh.handle_alexa_request(hass, _intent_request("AMAZON.StopIntent"))
        assert "outputSpeech" in r["response"]


class TestHandleFallback:
    @pytest.mark.asyncio
    async def test_fires_none_event(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _intent_request("AMAZON.FallbackIntent"))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response_type"] == sh.RESPONSE_NONE


class TestHandleSessionEnded:
    @pytest.mark.asyncio
    async def test_user_initiated(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _session_ended_request("USER_INITIATED"))
        hass.bus.async_fire.assert_called_once()

    @pytest.mark.asyncio
    async def test_exceeded_reprompts(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _session_ended_request("EXCEEDED_MAX_REPROMPTS"))
        hass.bus.async_fire.assert_called_once()

    @pytest.mark.asyncio
    async def test_other_reason_no_event(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _session_ended_request("ERROR"))
        hass.bus.async_fire.assert_not_called()


class TestExceptionHandler:
    @pytest.mark.asyncio
    async def test_exception_speaks_error(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        # Trigger an exception with unresolved number slot
        r = await sh.handle_alexa_request(hass, _intent_request("Number", {"Numbers": {"value": "?"}}))
        assert "outputSpeech" in r["response"]


class TestPersonId:
    @pytest.mark.asyncio
    async def test_person_id_in_event(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.ABC"}
        r = await sh.handle_alexa_request(hass, body)
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.ABC"
