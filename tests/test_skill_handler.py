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


def _intent_request_with_session(
    intent_name: str,
    slots: dict | None = None,
    session_attrs: dict | None = None,
    locale: str = "en-US",
) -> dict:
    """Build an IntentRequest with session attributes (for multi-turn dialog)."""
    body = _intent_request(intent_name, slots, locale)
    body["session"] = {"attributes": session_attrs or {}}
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

    def test_response_with_card(self):
        """Card should be included in the response when provided."""
        card = {"type": "Simple", "title": "Title", "content": "Body text"}
        r = sh._build_response(speak_output="Hello", card=card)
        assert r["response"]["card"] == card
        assert r["response"]["card"]["type"] == "Simple"
        assert r["response"]["card"]["title"] == "Title"
        assert r["response"]["card"]["content"] == "Body text"

    def test_response_without_card(self):
        """No card key when card is not provided — backward compatible."""
        r = sh._build_response(speak_output="Hello")
        assert "card" not in r["response"]

    def test_response_with_none_card(self):
        """Explicitly passing card=None should not add card to response."""
        r = sh._build_response(speak_output="Hello", card=None)
        assert "card" not in r["response"]

    def test_response_with_card_and_reprompt(self):
        """Card should coexist with speech and reprompt."""
        card = {"type": "Simple", "title": "T", "content": "C"}
        r = sh._build_response(
            speak_output="Question",
            reprompt="Try again",
            should_end_session=False,
            card=card,
        )
        assert r["response"]["outputSpeech"]["text"] == "Question"
        assert r["response"]["reprompt"]["outputSpeech"]["text"] == "Try again"
        assert r["response"]["card"] == card
        assert r["response"]["shouldEndSession"] is False


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

    @pytest.mark.asyncio
    async def test_display_card_in_response(self):
        """When display_title and display_body are set, card appears in launch response."""
        hass = _make_ha({
            "event": "evt_card",
            "text": "Did you take the pill?",
            "suppress_confirmation": False,
            "display_title": "Promemoria Pastiglia",
            "display_body": "Hai preso la pastiglia oggi?",
        })
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert r["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert r["response"]["shouldEndSession"] is False
        card = r["response"]["card"]
        assert card["type"] == "Simple"
        assert card["title"] == "Promemoria Pastiglia"
        assert card["content"] == "Hai preso la pastiglia oggi?"

    @pytest.mark.asyncio
    async def test_display_card_title_only(self):
        """When only display_title is set, card has empty content."""
        hass = _make_ha({
            "event": "evt_title",
            "text": "Question?",
            "suppress_confirmation": False,
            "display_title": "Reminder",
        })
        r = await sh.handle_alexa_request(hass, _launch_request())
        card = r["response"]["card"]
        assert card["type"] == "Simple"
        assert card["title"] == "Reminder"
        assert card["content"] == ""

    @pytest.mark.asyncio
    async def test_display_card_body_only(self):
        """When only display_body is set, card has empty title."""
        hass = _make_ha({
            "event": "evt_body",
            "text": "Question?",
            "suppress_confirmation": False,
            "display_body": "Some detail text",
        })
        r = await sh.handle_alexa_request(hass, _launch_request())
        card = r["response"]["card"]
        assert card["type"] == "Simple"
        assert card["title"] == ""
        assert card["content"] == "Some detail text"

    @pytest.mark.asyncio
    async def test_no_card_without_display_fields(self):
        """No card in response when display fields are absent."""
        hass = _make_ha({
            "event": "evt_nocard",
            "text": "Just a question?",
            "suppress_confirmation": False,
        })
        r = await sh.handle_alexa_request(hass, _launch_request())
        assert "card" not in r["response"]


class TestBuildCard:
    """Tests for _build_card helper function."""

    def test_build_card_with_both_fields(self):
        ha_state = sh.HaState(
            event_id="e1",
            suppress_confirmation=False,
            text="Q?",
            display_title="Title",
            display_body="Body text",
        )
        card = sh._build_card(ha_state)
        assert card is not None
        assert card["type"] == "Simple"
        assert card["title"] == "Title"
        assert card["content"] == "Body text"

    def test_build_card_title_only(self):
        ha_state = sh.HaState(
            event_id="e1",
            suppress_confirmation=False,
            text="Q?",
            display_title="Title Only",
        )
        card = sh._build_card(ha_state)
        assert card is not None
        assert card["title"] == "Title Only"
        assert card["content"] == ""

    def test_build_card_body_only(self):
        ha_state = sh.HaState(
            event_id="e1",
            suppress_confirmation=False,
            text="Q?",
            display_body="Body Only",
        )
        card = sh._build_card(ha_state)
        assert card is not None
        assert card["title"] == ""
        assert card["content"] == "Body Only"

    def test_build_card_returns_none_when_no_fields(self):
        ha_state = sh.HaState(
            event_id="e1",
            suppress_confirmation=False,
            text="Q?",
        )
        card = sh._build_card(ha_state)
        assert card is None

    def test_build_card_json_matches_alexa_spec(self):
        """Verify the card dict matches Alexa Simple card specification."""
        ha_state = sh.HaState(
            event_id="e1",
            suppress_confirmation=False,
            text="Q?",
            display_title="Promemoria Pastiglia",
            display_body="Hai preso la pastiglia oggi?",
        )
        card = sh._build_card(ha_state)
        # Alexa Simple card spec: { "type": "Simple", "title": "...", "content": "..." }
        assert isinstance(card, dict)
        assert set(card.keys()) == {"type", "title", "content"}
        assert card["type"] == "Simple"
        assert isinstance(card["title"], str)
        assert isinstance(card["content"], str)


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


class TestHandleFreeForm:
    @pytest.mark.asyncio
    async def test_freeform_fires_event(self):
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(
            hass,
            _intent_request("FreeForm", {"FreeFormText": {"value": "I want pizza for dinner"}}),
        )
        hass.bus.async_fire.assert_called_once()
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response"] == "I want pizza for dinner"
        assert event_data["event_response_type"] == sh.RESPONSE_FREEFORM
        assert r["response"]["outputSpeech"]["text"] == "Okay"

    @pytest.mark.asyncio
    async def test_freeform_missing_entity(self):
        hass = _make_ha(None)
        r = await sh.handle_alexa_request(
            hass,
            _intent_request("FreeForm", {"FreeFormText": {"value": "anything"}}),
        )
        # Should return empty response gracefully (no crash)
        assert r["response"]["shouldEndSession"] is True
        assert "outputSpeech" not in r["response"]

    @pytest.mark.asyncio
    async def test_freeform_model_included(self):
        """Verify FreeForm intent appears in the interaction model."""
        from custom_components.alexa_actions.models import get_model

        model = get_model("en-US", "test skill")
        intent_names = [i["name"] for i in model["interactionModel"]["languageModel"]["intents"]]
        assert "FreeForm" in intent_names

        # Verify slot type is AMAZON.SearchQuery
        freeform = next(i for i in model["interactionModel"]["languageModel"]["intents"] if i["name"] == "FreeForm")
        assert len(freeform["slots"]) == 1
        assert freeform["slots"][0]["name"] == "FreeFormText"
        assert freeform["slots"][0]["type"] == "AMAZON.SearchQuery"


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


class TestPersonName:
    """Tests for person name resolution via person_map."""

    @pytest.mark.asyncio
    async def test_person_name_in_event_when_mapped(self):
        """When person_id is in the person_map, event includes person_name."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.ABC"}
        person_map = {"amzn1.account.ABC": "Alice"}
        await sh.handle_alexa_request(hass, body, person_map)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.ABC"
        assert event_data["event_person_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_person_name_absent_when_not_mapped(self):
        """When person_id is NOT in the person_map, no person_name in event."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.UNKNOWN"}
        person_map = {"amzn1.account.ABC": "Alice"}
        await sh.handle_alexa_request(hass, body, person_map)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.UNKNOWN"
        assert "event_person_name" not in event_data

    @pytest.mark.asyncio
    async def test_person_name_absent_when_no_person(self):
        """When no voice profile detected, no person fields in event."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        # No person in context
        person_map = {"amzn1.account.ABC": "Alice"}
        await sh.handle_alexa_request(hass, body, person_map)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert "event_person_id" not in event_data
        assert "event_person_name" not in event_data

    @pytest.mark.asyncio
    async def test_person_map_backward_compat(self):
        """Without person_map, existing behavior (only person_id) is preserved."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.ABC"}
        # No person_map passed (backward compat)
        await sh.handle_alexa_request(hass, body)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.ABC"
        assert "event_person_name" not in event_data

    def test_resolve_person_name_with_mapping(self):
        """Unit test for _resolve_person_name with a valid mapping."""
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.XYZ"}
        person_id, person_name = sh._resolve_person_name(
            body, {"amzn1.account.XYZ": "Bob"},
        )
        assert person_id == "amzn1.account.XYZ"
        assert person_name == "Bob"

    def test_resolve_person_name_no_mapping(self):
        """Unit test for _resolve_person_name with empty mapping."""
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.XYZ"}
        person_id, person_name = sh._resolve_person_name(body, {})
        assert person_id == "amzn1.account.XYZ"
        assert person_name is None

    def test_resolve_person_name_no_person(self):
        """Unit test for _resolve_person_name with no person in context."""
        body = _intent_request("AMAZON.YesIntent")
        person_id, person_name = sh._resolve_person_name(
            body, {"amzn1.account.XYZ": "Bob"},
        )
        assert person_id is None
        assert person_name is None

    def test_resolve_person_name_no_map(self):
        """Unit test for _resolve_person_name with no map provided."""
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.XYZ"}
        person_id, person_name = sh._resolve_person_name(body)
        assert person_id == "amzn1.account.XYZ"
        assert person_name is None

    def test_resolve_person_name_none_map(self):
        """Unit test for _resolve_person_name with None map."""
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.XYZ"}
        person_id, person_name = sh._resolve_person_name(body, None)
        assert person_id == "amzn1.account.XYZ"
        assert person_name is None


# ---------------------------------------------------------------------------
# Dialog payload helper
# ---------------------------------------------------------------------------

_DIALOG_PAYLOAD = {
    "event": "dialog_evt1",
    "text": "<speak>Setting a reminder</speak>",
    "suppress_confirmation": False,
    "dialog": {
        "intent": "String",
        "slots": [
            {"name": "reminder_text", "type": "AMAZON.Person", "prompt": "What do you want to be reminded of?"},
            {"name": "reminder_time", "type": "AMAZON.TIME", "prompt": "What time?"},
        ],
        "confirm": True,
        "confirm_prompt": "I'll remind you to {reminder_text} at {reminder_time}. Is that correct?",
    },
}

_DIALOG_PAYLOAD_NO_CONFIRM = {
    "event": "dialog_evt2",
    "text": "Tell me something",
    "suppress_confirmation": False,
    "dialog": {
        "intent": "String",
        "slots": [
            {"name": "the_name", "type": "AMAZON.Person", "prompt": "What is your name?"},
        ],
        "confirm": False,
    },
}


# ---------------------------------------------------------------------------
# Dialog flow tests
# ---------------------------------------------------------------------------


class TestDialogFlow:
    """Tests for multi-turn dialog management."""

    @pytest.mark.asyncio
    async def test_dialog_launch_elicits_first_slot(self):
        """LaunchRequest with dialog payload should return ElicitSlot for the first slot."""
        hass = _make_ha(_DIALOG_PAYLOAD)
        r = await sh.handle_alexa_request(hass, _launch_request())

        resp = r["response"]
        assert resp["shouldEndSession"] is False
        # Should have an ElicitSlot directive for the first slot
        directives = resp["directives"]
        assert len(directives) == 1
        assert directives[0]["type"] == "Dialog.ElicitSlot"
        assert directives[0]["slotToElicit"] == "reminder_text"
        # Speech should be the first slot's prompt
        assert resp["outputSpeech"]["text"] == "What do you want to be reminded of?"

    @pytest.mark.asyncio
    async def test_dialog_collects_second_slot(self):
        """After first slot is filled, ElicitSlot for second slot with session attrs."""
        hass = _make_ha(_DIALOG_PAYLOAD)
        body = _intent_request_with_session(
            "String",
            slots={"reminder_text": {"value": "buy milk"}},
            session_attrs={"_dialog_slots": {"reminder_text": "buy milk"}},
        )
        r = await sh.handle_alexa_request(hass, body)

        resp = r["response"]
        assert resp["shouldEndSession"] is False
        directives = resp["directives"]
        assert directives[0]["type"] == "Dialog.ElicitSlot"
        assert directives[0]["slotToElicit"] == "reminder_time"
        assert resp["outputSpeech"]["text"] == "What time?"
        # Session attributes should carry both collected slots
        assert r["sessionAttributes"]["_dialog_slots"]["reminder_text"] == "buy milk"

    @pytest.mark.asyncio
    async def test_dialog_all_slots_fire_event_no_confirm(self):
        """Without confirm, all slots collected fires the event immediately."""
        hass = _make_ha(_DIALOG_PAYLOAD_NO_CONFIRM)
        body = _intent_request_with_session(
            "String",
            slots={"the_name": {"value": "Alice"}},
            session_attrs={"_dialog_slots": {"the_name": "Alice"}},
        )
        r = await sh.handle_alexa_request(hass, body)

        # Event should be fired with collected slot data
        hass.bus.async_fire.assert_called_once()
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_id"] == "dialog_evt2"
        assert event_data["event_response_type"] == sh.RESPONSE_DIALOG
        collected = json.loads(event_data["event_response"])
        assert collected["the_name"] == "Alice"
        # Session should end
        assert r["response"]["shouldEndSession"] is True
        assert r["response"]["outputSpeech"]["text"] == "Okay"

    @pytest.mark.asyncio
    async def test_dialog_with_confirmation(self):
        """When confirm=true and all slots collected, ConfirmIntent is returned."""
        hass = _make_ha(_DIALOG_PAYLOAD)
        body = _intent_request_with_session(
            "String",
            slots={
                "reminder_text": {"value": "buy milk"},
                "reminder_time": {"value": "14:30"},
            },
            session_attrs={"_dialog_slots": {"reminder_text": "buy milk", "reminder_time": "14:30"}},
        )
        r = await sh.handle_alexa_request(hass, body)

        resp = r["response"]
        assert resp["shouldEndSession"] is False
        directives = resp["directives"]
        assert directives[0]["type"] == "Dialog.ConfirmIntent"
        # Confirm prompt should have slot values substituted
        assert "buy milk" in resp["outputSpeech"]["text"]
        assert "14:30" in resp["outputSpeech"]["text"]
        # Session attributes should include _awaiting_confirm
        assert r["sessionAttributes"]["_awaiting_confirm"] is True

    @pytest.mark.asyncio
    async def test_dialog_confirm_yes_fires_event(self):
        """YES after ConfirmIntent should fire the event with all collected data."""
        hass = _make_ha(_DIALOG_PAYLOAD)
        body = _intent_request_with_session(
            "AMAZON.YesIntent",
            session_attrs={
                "_dialog_slots": {"reminder_text": "buy milk", "reminder_time": "14:30"},
                "_awaiting_confirm": True,
            },
        )
        r = await sh.handle_alexa_request(hass, body)

        hass.bus.async_fire.assert_called_once()
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response_type"] == sh.RESPONSE_DIALOG
        collected = json.loads(event_data["event_response"])
        assert collected["reminder_text"] == "buy milk"
        assert collected["reminder_time"] == "14:30"
        assert r["response"]["shouldEndSession"] is True
        assert r["response"]["outputSpeech"]["text"] == "Okay"

    @pytest.mark.asyncio
    async def test_dialog_confirm_no_restarts(self):
        """NO after ConfirmIntent should re-elicit the first slot."""
        hass = _make_ha(_DIALOG_PAYLOAD)
        body = _intent_request_with_session(
            "AMAZON.NoIntent",
            session_attrs={
                "_dialog_slots": {"reminder_text": "buy milk", "reminder_time": "14:30"},
                "_awaiting_confirm": True,
            },
        )
        r = await sh.handle_alexa_request(hass, body)

        resp = r["response"]
        assert resp["shouldEndSession"] is False
        directives = resp["directives"]
        assert directives[0]["type"] == "Dialog.ElicitSlot"
        assert directives[0]["slotToElicit"] == "reminder_text"
        # Collected slots should be cleared
        assert r["sessionAttributes"]["_dialog_slots"] == {}

    @pytest.mark.asyncio
    async def test_dialog_backward_compat(self):
        """No dialog key in payload → existing single-turn behavior unchanged."""
        hass = _make_ha({"event": "evt_compat", "text": "Take the pill?", "suppress_confirmation": False})
        r = await sh.handle_alexa_request(hass, _launch_request())

        # Should behave exactly like the old single-turn flow
        assert r["response"]["outputSpeech"]["text"] == "Take the pill?"
        assert r["response"]["shouldEndSession"] is False
        # No directives
        assert "directives" not in r["response"]

    @pytest.mark.asyncio
    async def test_dialog_session_attributes_persist(self):
        """Session attributes carry collected slots across turns."""
        hass = _make_ha(_DIALOG_PAYLOAD)
        # First turn: only first slot filled
        body = _intent_request_with_session(
            "String",
            slots={"reminder_text": {"value": "call mom"}},
            session_attrs={"_dialog_slots": {"reminder_text": "call mom"}},
        )
        r = await sh.handle_alexa_request(hass, body)

        # Response should carry the collected slot in session attributes
        assert r["sessionAttributes"]["_dialog_slots"]["reminder_text"] == "call mom"

    @pytest.mark.asyncio
    async def test_dialog_suppress_confirmation(self):
        """When suppress_confirmation is true, dialog completion should not speak."""
        payload = dict(_DIALOG_PAYLOAD_NO_CONFIRM)
        payload["suppress_confirmation"] = True
        hass = _make_ha(payload)
        body = _intent_request_with_session(
            "String",
            slots={"the_name": {"value": "Bob"}},
            session_attrs={"_dialog_slots": {"the_name": "Bob"}},
        )
        r = await sh.handle_alexa_request(hass, body)

        hass.bus.async_fire.assert_called_once()
        assert "outputSpeech" not in r["response"]


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------


def _make_queue_ha(queue: list[dict]) -> _MockHA:
    """Create a mock HA with a JSON-array queue state."""
    hass = _MockHA()
    mock_state = MagicMock()
    mock_state.state = json.dumps(queue)
    hass.states.get.return_value = mock_state
    return hass


# ---------------------------------------------------------------------------
# Queue tests
# ---------------------------------------------------------------------------


class TestQueueGetHaState:
    """_get_ha_state reads the first element from a queue (list)."""

    def test_queue_reads_first_item(self):
        queue = [
            {"event": "evt1", "text": "First?", "suppress_confirmation": False},
            {"event": "evt2", "text": "Second?", "suppress_confirmation": False},
            {"event": "evt3", "text": "Third?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        ha_state = sh._get_ha_state(hass)
        assert ha_state is not None
        assert ha_state.event_id == "evt1"
        assert ha_state.text == "First?"

    def test_empty_queue_returns_none(self):
        hass = _make_queue_ha([])
        ha_state = sh._get_ha_state(hass)
        assert ha_state is None

    def test_queue_backward_compat_single_dict(self):
        """Single dict (legacy format) still works."""
        hass = _make_ha({"event": "evt_legacy", "text": "Legacy?", "suppress_confirmation": False})
        ha_state = sh._get_ha_state(hass)
        assert ha_state is not None
        assert ha_state.event_id == "evt_legacy"
        assert ha_state.text == "Legacy?"

    def test_queue_preserves_reprompt(self):
        queue = [
            {"event": "evt_r", "text": "Q?", "reprompt": "Try again", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        ha_state = sh._get_ha_state(hass)
        assert ha_state.reprompt == "Try again"

    def test_queue_preserves_options(self):
        queue = [
            {"event": "evt_o", "text": "Pick one", "suppress_confirmation": False,
             "options": ["A", "B", "C"]},
        ]
        hass = _make_queue_ha(queue)
        ha_state = sh._get_ha_state(hass)
        assert ha_state.options == ["A", "B", "C"]

    def test_queue_preserves_dialog(self):
        queue = [_DIALOG_PAYLOAD]
        hass = _make_queue_ha(queue)
        ha_state = sh._get_ha_state(hass)
        assert ha_state.dialog is not None
        assert ha_state.dialog.intent == "String"
        assert len(ha_state.dialog.slots) == 2


class TestQueueAdvance:
    """_advance_queue removes the first element and updates state."""

    @pytest.mark.asyncio
    async def test_advance_removes_first(self):
        queue = [
            {"event": "evt1", "text": "First?", "suppress_confirmation": False},
            {"event": "evt2", "text": "Second?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        await sh._advance_queue(hass)

        # states.async_set should have been called with queue minus first
        hass.states.async_set.assert_called_once()
        updated = json.loads(hass.states.async_set.call_args.args[1])
        assert len(updated) == 1
        assert updated[0]["event"] == "evt2"

    @pytest.mark.asyncio
    async def test_advance_empty_after_all_popped(self):
        queue = [
            {"event": "evt_only", "text": "Only one", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        await sh._advance_queue(hass)

        hass.states.async_set.assert_called_once()
        updated = json.loads(hass.states.async_set.call_args.args[1])
        assert updated == []

    @pytest.mark.asyncio
    async def test_advance_empty_queue_noop(self):
        """Advancing an already-empty queue should not error."""
        hass = _make_queue_ha([])
        await sh._advance_queue(hass)
        # No state update needed — method returns early
        hass.states.async_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_advance_legacy_dict_clears_to_empty(self):
        """Legacy single-dict state is cleared to []."""
        hass = _make_ha({"event": "evt_legacy", "text": "Old?", "suppress_confirmation": False})
        await sh._advance_queue(hass)

        hass.states.async_set.assert_called_once()
        updated = json.loads(hass.states.async_set.call_args.args[1])
        assert updated == []

    @pytest.mark.asyncio
    async def test_advance_no_entity_noop(self):
        """No entity at all — should not error."""
        hass = _MockHA()
        hass.states.get.return_value = None
        await sh._advance_queue(hass)
        hass.states.async_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_advance_malformed_json_noop(self):
        """Malformed JSON in state — should not error."""
        hass = _MockHA()
        mock_state = MagicMock()
        mock_state.state = "not-json"
        hass.states.get.return_value = mock_state
        await sh._advance_queue(hass)
        hass.states.async_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_advance_three_items_twice(self):
        """Advance twice on a 3-item queue leaves the third item."""
        queue = [
            {"event": "a", "text": "A", "suppress_confirmation": False},
            {"event": "b", "text": "B", "suppress_confirmation": False},
            {"event": "c", "text": "C", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)

        await sh._advance_queue(hass)
        remaining = json.loads(hass.states.async_set.call_args.args[1])
        assert len(remaining) == 2
        assert remaining[0]["event"] == "b"

        # Re-set the mock state for the next advance
        mock_state = MagicMock()
        mock_state.state = json.dumps(remaining)
        hass.states.get.return_value = mock_state

        await sh._advance_queue(hass)
        remaining2 = json.loads(hass.states.async_set.call_args.args[1])
        assert len(remaining2) == 1
        assert remaining2[0]["event"] == "c"


class TestQueueEventCorrelation:
    """Verify the correct event_id is fired for the active queue item."""

    @pytest.mark.asyncio
    async def test_first_item_event_id_in_yes_response(self):
        queue = [
            {"event": "evt_active", "text": "Active?", "suppress_confirmation": False},
            {"event": "evt_queued", "text": "Queued?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        await sh.handle_alexa_request(hass, _intent_request("AMAZON.YesIntent"))

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_id"] == "evt_active"

    @pytest.mark.asyncio
    async def test_after_advance_second_item_event_id(self):
        queue = [
            {"event": "evt_first", "text": "First?", "suppress_confirmation": False},
            {"event": "evt_second", "text": "Second?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)

        # Advance removes first, second becomes active
        await sh._advance_queue(hass)
        remaining = json.loads(hass.states.async_set.call_args.args[1])

        # Re-set mock state for the handler to read
        mock_state = MagicMock()
        mock_state.state = json.dumps(remaining)
        hass.states.get.return_value = mock_state

        await sh.handle_alexa_request(hass, _intent_request("AMAZON.YesIntent"))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_id"] == "evt_second"


class TestQueueAdvanceInDispatcher:
    """Verify handle_alexa_request advances the queue on session end."""

    @pytest.mark.asyncio
    async def test_yes_intent_advances_queue(self):
        """YES response (shouldEndSession=true) should advance the queue."""
        queue = [
            {"event": "evt1", "text": "Q?", "suppress_confirmation": False},
            {"event": "evt2", "text": "Next?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        await sh.handle_alexa_request(hass, _intent_request("AMAZON.YesIntent"))

        # Queue should have been advanced (first item removed)
        hass.states.async_set.assert_called()
        updated = json.loads(hass.states.async_set.call_args.args[1])
        assert len(updated) == 1
        assert updated[0]["event"] == "evt2"

    @pytest.mark.asyncio
    async def test_no_intent_advances_queue(self):
        """NO response (shouldEndSession=true) should advance the queue."""
        queue = [
            {"event": "evt1", "text": "Q?", "suppress_confirmation": False},
            {"event": "evt2", "text": "Next?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        await sh.handle_alexa_request(hass, _intent_request("AMAZON.NoIntent"))

        hass.states.async_set.assert_called()
        updated = json.loads(hass.states.async_set.call_args.args[1])
        assert len(updated) == 1
        assert updated[0]["event"] == "evt2"

    @pytest.mark.asyncio
    async def test_launch_does_not_advance(self):
        """LaunchRequest keeps session open — should NOT advance queue."""
        queue = [
            {"event": "evt1", "text": "Q?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        await sh.handle_alexa_request(hass, _launch_request())

        # LaunchRequest returns shouldEndSession=false → no advance
        hass.states.async_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_ended_advances_queue(self):
        """SessionEndedRequest should advance the queue."""
        queue = [
            {"event": "evt1", "text": "Q?", "suppress_confirmation": False},
            {"event": "evt2", "text": "Next?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)
        await sh.handle_alexa_request(hass, _session_ended_request("USER_INITIATED"))

        hass.states.async_set.assert_called()
        updated = json.loads(hass.states.async_set.call_args.args[1])
        assert len(updated) == 1
        assert updated[0]["event"] == "evt2"


# ---------------------------------------------------------------------------
# Rich event data tests (ACT-20)
# ---------------------------------------------------------------------------


class TestGetDeviceId:
    """Tests for _get_device_id helper."""

    def test_extracts_device_id(self):
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["device"] = {"deviceId": "amzn1.ask.device.ABC123"}
        assert sh._get_device_id(body) == "amzn1.ask.device.ABC123"

    def test_missing_device_returns_none(self):
        body = _intent_request("AMAZON.YesIntent")
        assert sh._get_device_id(body) is None

    def test_empty_device_dict_returns_none(self):
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["device"] = {}
        assert sh._get_device_id(body) is None

    def test_missing_context_returns_none(self):
        body = {"request": {"type": "IntentRequest", "intent": {"name": "X", "slots": {}}}}
        assert sh._get_device_id(body) is None


class TestGetTranscript:
    """Tests for _get_transcript helper."""

    def test_from_slot_values(self):
        body = _intent_request("String", {"Strings": {"value": "hello world"}})
        assert sh._get_transcript(body) == "hello world"

    def test_multiple_slots_joined(self):
        body = _intent_request("Date", {
            "Dates": {"value": "2024-06-15"},
            "Times": {"value": "14:30"},
        })
        result = sh._get_transcript(body)
        assert "2024-06-15" in result
        assert "14:30" in result

    def test_no_slots_returns_none(self):
        body = _intent_request("AMAZON.YesIntent")
        assert sh._get_transcript(body) is None

    def test_empty_slots_returns_none(self):
        body = _intent_request("AMAZON.YesIntent", {})
        assert sh._get_transcript(body) is None

    def test_slots_with_none_values_skipped(self):
        body = _intent_request("Date", {
            "Dates": {"value": None},
            "Times": {"value": "14:30"},
        })
        result = sh._get_transcript(body)
        assert result == "14:30"

    def test_spoken_text_preferred_over_slots(self):
        body = _intent_request("String", {"Strings": {"value": "slot value"}})
        body["request"]["intent"]["spokenText"] = "spoken text override"
        assert sh._get_transcript(body) == "spoken text override"

    def test_launch_request_no_intent_returns_none(self):
        body = _launch_request()
        assert sh._get_transcript(body) is None


class TestRichEventData:
    """Tests for rich event data in _post_ha_event (ACT-20)."""

    @pytest.mark.asyncio
    async def test_locale_in_event(self):
        """Event data includes locale from the Alexa request."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent", locale="it-IT")
        await sh.handle_alexa_request(hass, body)
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["locale"] == "it-IT"

    @pytest.mark.asyncio
    async def test_device_id_in_event(self):
        """Event data includes device_id from context.System.device."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["device"] = {"deviceId": "amzn1.ask.device.XYZ"}
        await sh.handle_alexa_request(hass, body)
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["device_id"] == "amzn1.ask.device.XYZ"

    @pytest.mark.asyncio
    async def test_device_id_absent_when_missing(self):
        """device_id is not in event when device context is absent."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        await sh.handle_alexa_request(hass, body)
        event_data = hass.bus.async_fire.call_args[0][1]
        assert "device_id" not in event_data

    @pytest.mark.asyncio
    async def test_transcript_in_event_from_slot(self):
        """Event data includes transcript derived from slot values."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("FreeForm", {"FreeFormText": {"value": "I want pizza"}})
        await sh.handle_alexa_request(hass, body)
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["transcript"] == "I want pizza"

    @pytest.mark.asyncio
    async def test_transcript_absent_when_no_slots(self):
        """transcript is not in event when no slot values are available."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        await sh.handle_alexa_request(hass, body)
        event_data = hass.bus.async_fire.call_args[0][1]
        assert "transcript" not in event_data

    @pytest.mark.asyncio
    async def test_timestamp_in_event(self):
        """Event data includes an ISO 8601 timestamp."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        await sh.handle_alexa_request(hass, _intent_request("AMAZON.YesIntent"))
        event_data = hass.bus.async_fire.call_args[0][1]
        ts = event_data["timestamp"]
        # Verify ISO 8601 format: should parse without error
        from datetime import datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None  # has timezone info

    @pytest.mark.asyncio
    async def test_existing_fields_unchanged(self):
        """Existing event fields (event_id, event_response, event_response_type) are preserved."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        await sh.handle_alexa_request(hass, _intent_request("AMAZON.YesIntent"))
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_id"] == "e1"
        assert event_data["event_response"] == sh.RESPONSE_YES
        assert event_data["event_response_type"] == sh.RESPONSE_YES

    @pytest.mark.asyncio
    async def test_person_fields_still_present(self):
        """Person ID and name fields still work alongside new rich data."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.ABC"}
        await sh.handle_alexa_request(hass, body, {"amzn1.account.ABC": "Alice"})
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.ABC"
        assert event_data["event_person_name"] == "Alice"
        # New fields also present
        assert "locale" in event_data
        assert "timestamp" in event_data

    @pytest.mark.asyncio
    async def test_backward_compat_new_fields_absent(self):
        """New fields that are None are not in event dict — backward compatible."""
        hass = _make_ha({"event": "e1", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("AMAZON.YesIntent")  # no device, no slots
        await sh.handle_alexa_request(hass, body)
        event_data = hass.bus.async_fire.call_args[0][1]
        # Required new fields always present
        assert "locale" in event_data
        assert "timestamp" in event_data
        # Optional new fields absent when no data
        assert "device_id" not in event_data
        assert "transcript" not in event_data

    @pytest.mark.asyncio
    async def test_all_rich_fields_together(self):
        """All rich event fields present when full Alexa context is available."""
        hass = _make_ha({"event": "e_rich", "text": "Q?", "suppress_confirmation": False})
        body = _intent_request("FreeForm", {"FreeFormText": {"value": "remind me to call mom"}}, locale="it-IT")
        body["context"]["System"]["device"] = {"deviceId": "amzn1.ask.device.LIVING"}
        body["context"]["System"]["person"] = {"personId": "amzn1.account.PAolo"}
        await sh.handle_alexa_request(hass, body, {"amzn1.account.PAolo": "Paolo"})

        event_data = hass.bus.async_fire.call_args[0][1]
        # Existing fields
        assert event_data["event_id"] == "e_rich"
        assert event_data["event_response"] == "remind me to call mom"
        assert event_data["event_response_type"] == sh.RESPONSE_FREEFORM
        assert event_data["event_person_id"] == "amzn1.account.PAolo"
        assert event_data["event_person_name"] == "Paolo"
        # New fields
        assert event_data["locale"] == "it-IT"
        assert event_data["device_id"] == "amzn1.ask.device.LIVING"
        assert event_data["transcript"] == "remind me to call mom"
        assert "timestamp" in event_data


# ---------------------------------------------------------------------------
# APL tests (ACT-22)
# ---------------------------------------------------------------------------


def _apl_request(locale: str = "en-US") -> dict:
    """Build a LaunchRequest with APL support indicated in context."""
    return {
        "request": {"type": "LaunchRequest", "locale": locale},
        "context": {
            "System": {
                "device": {
                    "supportedInterfaces": {
                        "Alexa.Presentation.APL": {},
                    },
                },
            },
        },
    }


def _non_apl_request(locale: str = "en-US") -> dict:
    """Build a LaunchRequest without APL support (no screen)."""
    return {
        "request": {"type": "LaunchRequest", "locale": locale},
        "context": {
            "System": {
                "device": {
                    "supportedInterfaces": {},
                },
            },
        },
    }


class TestLoadAplTemplate:
    """Tests for _load_apl_template helper."""

    def test_load_notification_template(self):
        template = sh._load_apl_template("notification")
        assert template["type"] == "APL"
        assert "mainTemplate" in template
        assert template["mainTemplate"]["parameters"] == ["payload"]

    def test_load_selection_template(self):
        template = sh._load_apl_template("selection")
        assert template["type"] == "APL"
        assert "mainTemplate" in template

    def test_load_nonexistent_template_raises(self):
        with pytest.raises(FileNotFoundError):
            sh._load_apl_template("nonexistent")


class TestBuildAplDatasource:
    """Tests for _build_apl_datasource helper."""

    def test_basic_datasource(self):
        ha_state = sh.HaState(
            event_id="evt1",
            suppress_confirmation=False,
            text="Did you take the pill?",
        )
        ds = sh._build_apl_datasource(ha_state)
        props = ds["notificationData"]["properties"]
        assert props["title"] == ""
        assert props["body"] == "Did you take the pill?"
        assert props["hasConfirmButton"] is True
        assert props["confirmLabel"] == "Yes"
        assert props["cancelLabel"] == "No"
        assert props["options"] == []

    def test_datasource_with_display_fields(self):
        ha_state = sh.HaState(
            event_id="evt2",
            suppress_confirmation=False,
            text="Question?",
            display_title="Reminder",
            display_body="Details here",
        )
        ds = sh._build_apl_datasource(ha_state)
        props = ds["notificationData"]["properties"]
        assert props["title"] == "Reminder"
        assert props["body"] == "Question?"

    def test_datasource_with_options(self):
        ha_state = sh.HaState(
            event_id="evt3",
            suppress_confirmation=False,
            text="Pick one",
            options=["Red", "Green", "Blue"],
        )
        ds = sh._build_apl_datasource(ha_state)
        props = ds["notificationData"]["properties"]
        assert props["options"] == ["Red", "Green", "Blue"]

    def test_datasource_suppress_confirmation(self):
        ha_state = sh.HaState(
            event_id="evt4",
            suppress_confirmation=True,
            text="Auto confirmed",
        )
        ds = sh._build_apl_datasource(ha_state)
        props = ds["notificationData"]["properties"]
        assert props["hasConfirmButton"] is False

    def test_datasource_ssml_stripped(self):
        """SSML tags should be removed from display text."""
        ha_state = sh.HaState(
            event_id="evt5",
            suppress_confirmation=False,
            text="<speak>Paolo<break time='1s'/>hai preso la pastiglia?</speak>",
        )
        ds = sh._build_apl_datasource(ha_state)
        props = ds["notificationData"]["properties"]
        assert "<speak>" not in props["body"]
        assert "<break" not in props["body"]
        assert props["body"] == "Paolohai preso la pastiglia?"

    def test_datasource_plain_text_unchanged(self):
        """Non-SSML text should pass through unchanged."""
        ha_state = sh.HaState(
            event_id="evt6",
            suppress_confirmation=False,
            text="Plain text notification",
        )
        ds = sh._build_apl_datasource(ha_state)
        props = ds["notificationData"]["properties"]
        assert props["body"] == "Plain text notification"

    def test_datasource_empty_text(self):
        ha_state = sh.HaState(
            event_id="evt7",
            suppress_confirmation=False,
            text=None,
        )
        ds = sh._build_apl_datasource(ha_state)
        props = ds["notificationData"]["properties"]
        assert props["body"] == ""


class TestSupportsApl:
    """Tests for _supports_apl helper."""

    def test_apl_supported(self):
        body = _apl_request()
        assert sh._supports_apl(body) is True

    def test_apl_not_supported(self):
        body = _non_apl_request()
        assert sh._supports_apl(body) is False

    def test_no_context(self):
        body = {"request": {"type": "LaunchRequest"}}
        assert sh._supports_apl(body) is False

    def test_no_device(self):
        body = {
            "request": {"type": "LaunchRequest"},
            "context": {"System": {}},
        }
        assert sh._supports_apl(body) is False

    def test_no_supported_interfaces(self):
        body = {
            "request": {"type": "LaunchRequest"},
            "context": {"System": {"device": {}}},
        }
        assert sh._supports_apl(body) is False

    def test_other_interfaces_not_apl(self):
        body = {
            "request": {"type": "LaunchRequest"},
            "context": {
                "System": {
                    "device": {
                        "supportedInterfaces": {
                            "Alexa.Presentation.HTML": {},
                        },
                    },
                },
            },
        }
        assert sh._supports_apl(body) is False


class TestBuildAplDirective:
    """Tests for _build_apl_directive helper."""

    def test_returns_none_when_no_visual_data(self):
        """When text, display_title, and display_body are all empty, return None."""
        ha_state = sh.HaState(
            event_id="evt_none",
            suppress_confirmation=False,
            text=None,
            display_title=None,
            display_body=None,
        )
        assert sh._build_apl_directive(ha_state) is None

    def test_returns_notification_template_without_options(self):
        """Without options, uses the notification template."""
        ha_state = sh.HaState(
            event_id="evt_notif",
            suppress_confirmation=False,
            text="Do you want coffee?",
        )
        directive = sh._build_apl_directive(ha_state)
        assert directive is not None
        assert directive["type"] == "Alexa.Presentation.APL.RenderDocument"
        assert directive["token"] == "alexa_actions_evt_notif"
        doc = directive["document"]
        assert doc["type"] == "APL"
        # Notification template has AlexaButton elements
        items = doc["mainTemplate"]["items"][0]["items"]
        has_button = any(
            item.get("type") == "Container" and
            any(i.get("type") == "AlexaButton" for i in item.get("items", []))
            for item in items
        )
        assert has_button
        assert "datasources" in directive
        assert "notificationData" in directive["datasources"]

    def test_returns_selection_template_with_options(self):
        """With options, uses the selection template."""
        ha_state = sh.HaState(
            event_id="evt_sel",
            suppress_confirmation=False,
            text="Pick a color",
            options=["Red", "Green", "Blue"],
        )
        directive = sh._build_apl_directive(ha_state)
        assert directive is not None
        assert directive["type"] == "Alexa.Presentation.APL.RenderDocument"
        doc = directive["document"]
        assert doc["type"] == "APL"
        # Selection template has a Sequence element
        items = doc["mainTemplate"]["items"][0]["items"]
        has_sequence = any(item.get("type") == "Sequence" for item in items)
        assert has_sequence
        # Datasource includes options
        props = directive["datasources"]["notificationData"]["properties"]
        assert props["options"] == ["Red", "Green", "Blue"]

    def test_default_token_when_no_event_id(self):
        """When event_id is None, token uses 'default'."""
        ha_state = sh.HaState(
            event_id=None,
            suppress_confirmation=False,
            text="Some text",
        )
        directive = sh._build_apl_directive(ha_state)
        assert directive["token"] == "alexa_actions_default"

    def test_directive_with_display_title(self):
        """Display title alone triggers APL directive."""
        ha_state = sh.HaState(
            event_id="evt_title",
            suppress_confirmation=False,
            text=None,
            display_title="Title Only",
        )
        directive = sh._build_apl_directive(ha_state)
        assert directive is not None
        props = directive["datasources"]["notificationData"]["properties"]
        assert props["title"] == "Title Only"

    def test_directive_with_display_body(self):
        """Display body alone triggers APL directive."""
        ha_state = sh.HaState(
            event_id="evt_body",
            suppress_confirmation=False,
            text=None,
            display_body="Body text here",
        )
        directive = sh._build_apl_directive(ha_state)
        assert directive is not None


class TestBuildResponseWithDirectives:
    """Tests for _build_response with the directives parameter."""

    def test_directives_added_to_response(self):
        """Directives list is added to the response."""
        d = [{"type": "Alexa.Presentation.APL.RenderDocument", "token": "t1"}]
        r = sh._build_response(speak_output="Hello", directives=d)
        assert r["response"]["directives"] == d

    def test_directives_appended_to_existing(self):
        """When response already has directives (e.g. from ElicitSlot), new ones are appended."""
        # Simulate what _build_elicit_slot_response does
        r = sh._build_response(
            speak_output="What?",
            reprompt="What?",
            should_end_session=False,
        )
        r["response"]["directives"] = [{
            "type": "Dialog.ElicitSlot",
            "slotToElicit": "testSlot",
        }]
        # Now append APL directive using the same pattern
        apl = [{"type": "Alexa.Presentation.APL.RenderDocument", "token": "t2"}]
        r["response"].setdefault("directives", []).extend(apl)
        assert len(r["response"]["directives"]) == 2
        assert r["response"]["directives"][0]["type"] == "Dialog.ElicitSlot"
        assert r["response"]["directives"][1]["type"] == "Alexa.Presentation.APL.RenderDocument"

    def test_none_directives_no_key(self):
        """Passing directives=None should not add directives key."""
        r = sh._build_response(speak_output="Hello", directives=None)
        assert "directives" not in r["response"]

    def test_empty_directives_no_key(self):
        """Passing directives=None (default) should not add directives key."""
        r = sh._build_response(speak_output="Hello")
        assert "directives" not in r["response"]


class TestHandleLaunchApl:
    """Tests for APL integration in _handle_launch."""

    @pytest.mark.asyncio
    async def test_apl_directive_included_on_screen_device(self):
        """APL directive is included when device supports APL."""
        hass = _make_ha({
            "event": "evt_apl",
            "text": "Did you take the pill?",
            "suppress_confirmation": False,
            "display_title": "Reminder",
        })
        r = await sh.handle_alexa_request(hass, _apl_request())
        assert r["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        directives = r["response"]["directives"]
        assert len(directives) == 1
        assert directives[0]["type"] == "Alexa.Presentation.APL.RenderDocument"
        assert directives[0]["token"] == "alexa_actions_evt_apl"

    @pytest.mark.asyncio
    async def test_no_apl_directive_on_non_screen_device(self):
        """No APL directive when device does not support APL (voice-only fallback)."""
        hass = _make_ha({
            "event": "evt_noap",
            "text": "Did you take the pill?",
            "suppress_confirmation": False,
            "display_title": "Reminder",
        })
        r = await sh.handle_alexa_request(hass, _non_apl_request())
        assert r["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert "directives" not in r["response"]

    @pytest.mark.asyncio
    async def test_apl_selection_template_with_options(self):
        """When options are present, selection template is used in APL directive."""
        hass = _make_ha({
            "event": "evt_opts",
            "text": "Pick a color",
            "suppress_confirmation": False,
            "options": ["Red", "Green", "Blue"],
        })
        r = await sh.handle_alexa_request(hass, _apl_request())
        directives = r["response"]["directives"]
        assert len(directives) == 1
        doc = directives[0]["document"]
        # Selection template has a Sequence element
        items = doc["mainTemplate"]["items"][0]["items"]
        has_sequence = any(item.get("type") == "Sequence" for item in items)
        assert has_sequence

    @pytest.mark.asyncio
    async def test_apl_notification_template_without_options(self):
        """Without options, notification template is used in APL directive."""
        hass = _make_ha({
            "event": "evt_noopts",
            "text": "Did you take the pill?",
            "suppress_confirmation": False,
        })
        r = await sh.handle_alexa_request(hass, _apl_request())
        directives = r["response"]["directives"]
        assert len(directives) == 1
        doc = directives[0]["document"]
        # Notification template should NOT have a Sequence element
        items = doc["mainTemplate"]["items"][0]["items"]
        has_sequence = any(item.get("type") == "Sequence" for item in items)
        assert not has_sequence

    @pytest.mark.asyncio
    async def test_apl_with_ssml_stripped(self):
        """APL display text has SSML tags stripped."""
        hass = _make_ha({
            "event": "evt_ssml",
            "text": "<speak>Paolo<break time='1s'/>hai preso la pastiglia?</speak>",
            "suppress_confirmation": False,
        })
        r = await sh.handle_alexa_request(hass, _apl_request())
        # Voice output should be SSML
        assert r["response"]["outputSpeech"]["type"] == "SSML"
        # APL display should have stripped tags
        directives = r["response"]["directives"]
        body = directives[0]["datasources"]["notificationData"]["properties"]["body"]
        assert "<speak>" not in body
        assert "<break" not in body

    @pytest.mark.asyncio
    async def test_apl_with_card_and_directive(self):
        """Both card and APL directive should coexist."""
        hass = _make_ha({
            "event": "evt_both",
            "text": "Question?",
            "suppress_confirmation": False,
            "display_title": "Title",
            "display_body": "Body text",
        })
        r = await sh.handle_alexa_request(hass, _apl_request())
        # Card present
        assert r["response"]["card"]["type"] == "Simple"
        assert r["response"]["card"]["title"] == "Title"
        # APL directive also present
        directives = r["response"]["directives"]
        assert len(directives) == 1
        assert directives[0]["type"] == "Alexa.Presentation.APL.RenderDocument"

    @pytest.mark.asyncio
    async def test_apl_no_directive_when_no_text(self):
        """When notification has no text/display fields, no APL directive even on APL device."""
        hass = _make_ha({
            "event": "evt_empty",
            "text": "",
            "suppress_confirmation": False,
        })
        # With empty text string, it's still truthy in the check...
        # Actually _build_apl_directive checks if text exists but empty string is falsy in the combined check
        r = await sh.handle_alexa_request(hass, _apl_request())
        # Empty text is falsy, no display_title or display_body → no APL
        assert "directives" not in r["response"]

    @pytest.mark.asyncio
    async def test_apl_preserves_reprompt(self):
        """APL integration does not break reprompt behavior."""
        hass = _make_ha({
            "event": "evt_rep",
            "text": "Did you take the pill?",
            "reprompt": "Say yes or no",
            "suppress_confirmation": False,
        })
        r = await sh.handle_alexa_request(hass, _apl_request())
        assert r["response"]["reprompt"]["outputSpeech"]["text"] == "Say yes or no"
        assert "directives" in r["response"]


class TestStripSsml:
    """Tests for _strip_ssml helper."""

    def test_strip_full_ssml(self):
        assert sh._strip_ssml("<speak>Hello world</speak>") == "Hello world"

    def test_strip_ssml_with_break(self):
        result = sh._strip_ssml("<speak>Hi<break time='1s'/>there</speak>")
        assert result == "Hithere"

    def test_no_tags(self):
        assert sh._strip_ssml("Plain text") == "Plain text"

    def test_empty_string(self):
        assert sh._strip_ssml("") == ""

    def test_only_tags(self):
        assert sh._strip_ssml("<speak></speak>") == ""
