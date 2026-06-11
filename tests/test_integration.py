"""Integration tests for skill_handler.py — full pipeline from Alexa JSON
request through ``handle_alexa_request()`` to response verification.

These tests exercise the complete path: realistic Alexa request envelope
-> HA state read -> event bus fire -> Alexa response JSON, covering
SSML support, custom reprompt, graceful empty state, event bus, and
JSON round-trip scenarios.
"""

import json
import types
from unittest.mock import MagicMock

import pytest

import sys

# ---------------------------------------------------------------------------
# Set up minimal HA mock modules (same pattern as test_skill_handler.py)
# ---------------------------------------------------------------------------
_ha = types.ModuleType("homeassistant")


class _MockHA:
    """Minimal mock of HomeAssistant for testing."""

    def __init__(self):
        self.states = MagicMock()
        self.bus = MagicMock()
        self.data = {}


_ha.HomeAssistant = _MockHA
_ha.ServiceCall = MagicMock
_ha.callback = lambda f: f
_ha.ConfigEntry = MagicMock
_ha.exceptions = MagicMock()
sys.modules["homeassistant"] = _ha

_ha_core = types.ModuleType("homeassistant.core")
_ha_core.HomeAssistant = _MockHA
_ha_core.ServiceCall = MagicMock
_ha_core.callback = lambda f: f
sys.modules["homeassistant.core"] = _ha_core

for _mod_name in (
    "homeassistant.config_entries",
    "homeassistant.exceptions",
    "homeassistant.const",
):
    sys.modules.setdefault(_mod_name, MagicMock())
sys.modules.setdefault("voluptuous", MagicMock())

from custom_components.alexa_actions import skill_handler as sh


# ---------------------------------------------------------------------------
# Helpers
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


def _alexa_launch_request(locale: str = "en-US") -> dict:
    """Build a realistic Alexa LaunchRequest envelope."""
    return {
        "version": "1.0",
        "session": {"new": True, "sessionId": "amzn1.echo-api.session.test"},
        "context": {
            "System": {"application": {"applicationId": "amzn1.ask.skill.test"}},
        },
        "request": {
            "type": "LaunchRequest",
            "requestId": "amzn1.echo-api.request.test",
            "locale": locale,
            "timestamp": "2026-06-10T18:00:00Z",
        },
    }


def _alexa_intent_request(
    intent_name: str, slots: dict | None = None, locale: str = "en-US"
) -> dict:
    """Build a realistic Alexa IntentRequest envelope."""
    return {
        "version": "1.0",
        "session": {"new": False, "sessionId": "amzn1.echo-api.session.test"},
        "context": {
            "System": {
                "application": {"applicationId": "amzn1.ask.skill.test"},
                "person": None,
            },
        },
        "request": {
            "type": "IntentRequest",
            "requestId": "amzn1.echo-api.request.test",
            "locale": locale,
            "timestamp": "2026-06-10T18:00:00Z",
            "intent": {"name": intent_name, "slots": slots or {}},
        },
    }


# ===========================================================================
# Test class: SSML integration
# ===========================================================================


class TestSSMLIntegration:
    """Full-pipeline tests for SSML notification support."""

    @pytest.mark.asyncio
    async def test_ssml_notification_full_pipeline(self):
        ssml = "<speak>Paolo<break time='1s'/>hai preso la pastiglia?</speak>"
        hass = _make_ha(
            {"event": "evt_ssml_1", "text": ssml, "suppress_confirmation": False}
        )

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        # outputSpeech should be SSML with the full string unchanged
        assert resp["outputSpeech"]["type"] == "SSML"
        assert resp["outputSpeech"]["ssml"] == ssml
        # session stays open because event_id is present
        assert resp["shouldEndSession"] is False
        # reprompt falls back to text (which is SSML), so it should also be SSML
        assert resp["reprompt"]["outputSpeech"]["type"] == "SSML"
        assert resp["reprompt"]["outputSpeech"]["ssml"] == ssml

    @pytest.mark.asyncio
    async def test_plain_text_notification_full_pipeline(self):
        hass = _make_ha(
            {"event": "evt_plain", "text": "Did you take the pill?", "suppress_confirmation": False}
        )

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["type"] == "PlainText"
        assert resp["outputSpeech"]["text"] == "Did you take the pill?"
        # Reprompt should also be PlainText (fallback to text)
        assert resp["reprompt"]["outputSpeech"]["type"] == "PlainText"
        assert resp["reprompt"]["outputSpeech"]["text"] == "Did you take the pill?"

    @pytest.mark.asyncio
    async def test_ssml_with_yes_intent(self):
        ssml = "<speak>Paolo<break time='1s'/>hai preso la pastiglia?</speak>"
        hass = _make_ha(
            {"event": "evt_ssml_yes", "text": ssml, "suppress_confirmation": False}
        )

        # First, simulate the skill opening (LaunchRequest) — not strictly
        # required for the handler but mirrors real Alexa flow.
        await sh.handle_alexa_request(hass, _alexa_launch_request())

        # Now the user says "yes"
        result = await sh.handle_alexa_request(
            hass, _alexa_intent_request("AMAZON.YesIntent")
        )

        resp = result["response"]
        # Confirmation should be plain "Okay", not SSML
        assert resp["outputSpeech"]["type"] == "PlainText"
        assert resp["outputSpeech"]["text"] == "Okay"
        # Event bus should have received ResponseYes
        hass.bus.async_fire.assert_called()
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_response_type"] == "ResponseYes"


# ===========================================================================
# Test class: Custom reprompt
# ===========================================================================


class TestCustomRepromptIntegration:
    """Full-pipeline tests for custom reprompt support."""

    @pytest.mark.asyncio
    async def test_custom_reprompt_pipeline(self):
        hass = _make_ha(
            {
                "event": "evt_reprompt",
                "text": "Did you take the pill?",
                "reprompt": "Say yes or no",
                "suppress_confirmation": False,
            }
        )

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "Did you take the pill?"
        assert resp["reprompt"]["outputSpeech"]["text"] == "Say yes or no"
        assert resp["shouldEndSession"] is False

    @pytest.mark.asyncio
    async def test_reprompt_fallback_pipeline(self):
        """When no custom reprompt is provided, it should fall back to text."""
        hass = _make_ha(
            {
                "event": "evt_fallback",
                "text": "Did you take the pill?",
                "suppress_confirmation": False,
            }
        )

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["reprompt"]["outputSpeech"]["text"] == "Did you take the pill?"

    @pytest.mark.asyncio
    async def test_ssml_reprompt_pipeline(self):
        ssml_reprompt = "<speak>Scusa<break time='500ms'/>rispondi si o no.</speak>"
        hass = _make_ha(
            {
                "event": "evt_ssml_rep",
                "text": "Hai preso la pastiglia?",
                "reprompt": ssml_reprompt,
                "suppress_confirmation": False,
            }
        )

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["type"] == "PlainText"
        assert resp["outputSpeech"]["text"] == "Hai preso la pastiglia?"
        assert resp["reprompt"]["outputSpeech"]["type"] == "SSML"
        assert resp["reprompt"]["outputSpeech"]["ssml"] == ssml_reprompt


# ===========================================================================
# Test class: Empty / missing state
# ===========================================================================


class TestEmptyStateIntegration:
    """Full-pipeline tests for graceful handling of missing or empty state."""

    @pytest.mark.asyncio
    async def test_missing_entity_speaks_message(self):
        hass = _make_ha(None)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "No pending notifications"
        assert resp["shouldEndSession"] is True
        assert "reprompt" not in resp

    @pytest.mark.asyncio
    async def test_no_event_id_speaks_message(self):
        hass = _make_ha({"text": "Hello!", "suppress_confirmation": False})

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "No pending notifications"
        assert resp["shouldEndSession"] is True

    @pytest.mark.asyncio
    async def test_locale_it_no_notifications(self):
        hass = _make_ha(None)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request("it-IT"))

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "Nessuna notifica in attesa"
        assert resp["shouldEndSession"] is True

    @pytest.mark.asyncio
    async def test_locale_de_no_notifications(self):
        hass = _make_ha(None)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request("de-DE"))

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "Keine ausstehenden Benachrichtigungen"
        assert resp["shouldEndSession"] is True


# ===========================================================================
# Test class: Event bus integration
# ===========================================================================


class TestEventBusIntegration:
    """Full-pipeline tests verifying HA event bus interactions."""

    @pytest.mark.asyncio
    async def test_event_fired_on_yes_response(self):
        hass = _make_ha(
            {"event": "evt_bus_yes", "text": "Take the pill?", "suppress_confirmation": False}
        )

        await sh.handle_alexa_request(
            hass, _alexa_intent_request("AMAZON.YesIntent")
        )

        hass.bus.async_fire.assert_called_once()
        call_args = hass.bus.async_fire.call_args
        event_name = call_args[0][0]
        event_data = call_args[0][1]

        assert event_name == sh.EVENT_ALEXA_ACTIONABLE_NOTIFICATION
        assert event_data["event_id"] == "evt_bus_yes"
        assert event_data["event_response"] == "ResponseYes"
        assert event_data["event_response_type"] == "ResponseYes"

    @pytest.mark.asyncio
    async def test_event_includes_person_id(self):
        hass = _make_ha(
            {"event": "evt_person", "text": "Take the pill?", "suppress_confirmation": False}
        )

        body = _alexa_intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {
            "personId": "amzn1.account.TEST123"
        }

        await sh.handle_alexa_request(hass, body)

        hass.bus.async_fire.assert_called_once()
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.TEST123"


# ===========================================================================
# Test class: JSON round-trip
# ===========================================================================


class TestJsonRoundTrip:
    """Tests that notification payloads survive the HA state JSON round-trip."""

    @pytest.mark.asyncio
    async def test_state_json_round_trip(self):
        payload = {
            "text": "Did you lock the door?",
            "reprompt": "Please answer yes or no",
            "event": "evt_roundtrip",
            "suppress_confirmation": False,
        }

        hass = _make_ha(payload)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "Did you lock the door?"
        assert resp["reprompt"]["outputSpeech"]["text"] == "Please answer yes or no"
        assert resp["shouldEndSession"] is False

    @pytest.mark.asyncio
    async def test_special_characters_in_text(self):
        payload = {
            "text": 'He said "ciao!" and left — approximation: 90% ~correct ☃',
            "reprompt": "Try again: Renée & Co. <not_ssml>",
            "event": "evt_special",
            "suppress_confirmation": False,
        }

        hass = _make_ha(payload)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == payload["text"]
        assert resp["reprompt"]["outputSpeech"]["text"] == payload["reprompt"]


# ===========================================================================
# Test class: Queue integration
# ===========================================================================


def _make_queue_ha(queue: list[dict]) -> _MockHA:
    """Create a mock HA with a JSON-array queue state."""
    hass = _MockHA()
    mock_state = MagicMock()
    mock_state.state = json.dumps(queue)
    hass.states.get.return_value = mock_state
    return hass


class TestQueueIntegration:
    """Full-pipeline tests for the notification queue."""

    @pytest.mark.asyncio
    async def test_queue_launch_reads_first(self):
        """LaunchRequest reads only the first item from the queue."""
        queue = [
            {"event": "q1", "text": "First question?", "suppress_confirmation": False},
            {"event": "q2", "text": "Second question?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "First question?"
        assert resp["shouldEndSession"] is False

    @pytest.mark.asyncio
    async def test_queue_yes_advances(self):
        """YES intent on a queued notification advances the queue."""
        queue = [
            {"event": "q1", "text": "First?", "suppress_confirmation": False},
            {"event": "q2", "text": "Second?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)

        result = await sh.handle_alexa_request(
            hass, _alexa_intent_request("AMAZON.YesIntent")
        )

        # Event should be for the first item
        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_id"] == "q1"

        # Queue should have been advanced
        hass.states.async_set.assert_called()
        remaining = json.loads(hass.states.async_set.call_args.args[1])
        assert len(remaining) == 1
        assert remaining[0]["event"] == "q2"

    @pytest.mark.asyncio
    async def test_queue_empty_queue_launch(self):
        """Empty queue produces no-notifications message."""
        hass = _make_queue_ha([])

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "No pending notifications"
        assert resp["shouldEndSession"] is True

    @pytest.mark.asyncio
    async def test_queue_backward_compat_launch(self):
        """Legacy single-dict state still works end-to-end."""
        hass = _make_ha({"event": "legacy", "text": "Legacy question?", "suppress_confirmation": False})

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "Legacy question?"
        assert resp["shouldEndSession"] is False

    @pytest.mark.asyncio
    async def test_queue_backward_compat_yes_advances(self):
        """YES on legacy single-dict clears state to []."""
        hass = _make_ha({"event": "legacy", "text": "Q?", "suppress_confirmation": False})

        await sh.handle_alexa_request(
            hass, _alexa_intent_request("AMAZON.YesIntent")
        )

        hass.states.async_set.assert_called()
        remaining = json.loads(hass.states.async_set.call_args.args[1])
        assert remaining == []

    @pytest.mark.asyncio
    async def test_queue_ssml_first_item(self):
        """SSML in the first queue item is handled correctly."""
        ssml = "<speak>First<break time='1s'/>question?</speak>"
        queue = [
            {"event": "q_ssml", "text": ssml, "suppress_confirmation": False},
            {"event": "q2", "text": "Plain second", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["type"] == "SSML"
        assert resp["outputSpeech"]["ssml"] == ssml
        assert resp["shouldEndSession"] is False

    @pytest.mark.asyncio
    async def test_queue_select_with_options_first_item(self):
        """Select intent resolves options from the first queue item."""
        queue = [
            {
                "event": "q_sel",
                "text": "Pick one",
                "suppress_confirmation": False,
                "options": ["Pizza", "Pasta", "Salad"],
            },
            {"event": "q2", "text": "Next?", "suppress_confirmation": False},
        ]
        hass = _make_queue_ha(queue)

        body = _alexa_intent_request("Select", {
            "Selections": {
                "value": "pizza",
                "resolutions": {
                    "resolutionsPerAuthority": [{
                        "status": {"code": "ER_SUCCESS_MATCH"},
                        "values": [{"value": {"name": "Pizza"}}],
                    }],
                },
            },
        })

        result = await sh.handle_alexa_request(hass, body)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_id"] == "q_sel"
        assert event_data["event_response"] == "Pizza"
        assert event_data["event_response_type"] == sh.RESPONSE_SELECT

        # Queue advanced
        remaining = json.loads(hass.states.async_set.call_args.args[1])
        assert len(remaining) == 1
        assert remaining[0]["event"] == "q2"

    @pytest.mark.asyncio
    async def test_queue_custom_reprompt_first_item(self):
        """Custom reprompt in first queue item works correctly."""
        queue = [
            {
                "event": "q_rep",
                "text": "Did you take the pill?",
                "reprompt": "Say yes or no",
                "suppress_confirmation": False,
            },
        ]
        hass = _make_queue_ha(queue)

        result = await sh.handle_alexa_request(hass, _alexa_launch_request())

        resp = result["response"]
        assert resp["outputSpeech"]["text"] == "Did you take the pill?"
        assert resp["reprompt"]["outputSpeech"]["text"] == "Say yes or no"


# ===========================================================================
# Test class: Person name resolution integration
# ===========================================================================


class TestPersonNameIntegration:
    """Full-pipeline tests for person name resolution."""

    @pytest.mark.asyncio
    async def test_person_name_in_yes_event(self):
        """Yes response with person_map includes person_name in event."""
        hass = _make_ha(
            {"event": "evt_pn_yes", "text": "Take the pill?", "suppress_confirmation": False}
        )
        body = _alexa_intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.ALICE"}
        person_map = {"amzn1.account.ALICE": "Alice", "amzn1.account.BOB": "Bob"}

        await sh.handle_alexa_request(hass, body, person_map)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.ALICE"
        assert event_data["event_person_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_person_name_absent_when_unmapped(self):
        """Unknown person_id gets person_id but no person_name."""
        hass = _make_ha(
            {"event": "evt_pn_unmapped", "text": "Take the pill?", "suppress_confirmation": False}
        )
        body = _alexa_intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.UNKNOWN"}
        person_map = {"amzn1.account.ALICE": "Alice"}

        await sh.handle_alexa_request(hass, body, person_map)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.UNKNOWN"
        assert "event_person_name" not in event_data

    @pytest.mark.asyncio
    async def test_person_name_backward_compat_no_map(self):
        """Without person_map, only person_id appears (backward compat)."""
        hass = _make_ha(
            {"event": "evt_pn_compat", "text": "Take the pill?", "suppress_confirmation": False}
        )
        body = _alexa_intent_request("AMAZON.YesIntent")
        body["context"]["System"]["person"] = {"personId": "amzn1.account.ALICE"}

        # No person_map passed
        await sh.handle_alexa_request(hass, body)

        event_data = hass.bus.async_fire.call_args[0][1]
        assert event_data["event_person_id"] == "amzn1.account.ALICE"
        assert "event_person_name" not in event_data

