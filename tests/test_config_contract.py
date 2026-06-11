"""Contract tests — verify service layer and handler agree on data fields.

These tests validate that the fields flowing through the pipeline
(service call -> input_text entity -> skill_handler) are consistent
and that the reprompt field reaches every layer.
"""

import importlib
import json
import re
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# conftest.py pre-populates sys.modules["voluptuous"] with a MagicMock via
# setdefault.  Because conftest runs *before* test modules, the MagicMock
# is already in place when this file is imported.  We swap it back to the
# real package, then force-reload the integration package so that
# __init__.py re-executes its top-level ``vol.Schema(...)`` against the
# real voluptuous.
#
# We must also reload ``views`` so that ``AlexaSkillView.__init__`` uses a
# fresh MagicMock base-class (HomeAssistantView) instead of one whose
# internal iterator has been exhausted by prior calls.
# ---------------------------------------------------------------------------
del sys.modules["voluptuous"]
import voluptuous as _real_vol  # noqa: E402

sys.modules["voluptuous"] = _real_vol

for _mod_name in (
    "custom_components.alexa_actions.const",
    "custom_components.alexa_actions.skill_handler",
    "custom_components.alexa_actions.views",
    "custom_components.alexa_actions",
):
    if _mod_name in sys.modules:
        importlib.reload(sys.modules[_mod_name])

init_mod = importlib.import_module("custom_components.alexa_actions")
from custom_components.alexa_actions import skill_handler as sh  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BLUEPRINT_PATH = "blueprints/alexa_actions_notification.yaml"


def _make_mock_hass() -> MagicMock:
    """Build a mock HomeAssistant suitable for async_setup_entry."""
    hass = MagicMock()
    hass.data = {}
    hass.states.async_set = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=MagicMock())
    hass.http = MagicMock()
    hass.http.register_view = MagicMock()
    hass.services.async_register = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.services.async_remove = MagicMock()
    return hass


async def _setup_entry_and_get_handler(hass: MagicMock):
    """Run async_setup_entry and return the registered service handler.

    The ``views`` module is reloaded each time because ``AlexaSkillView``
    inherits from the MagicMock ``HomeAssistantView``.  Instantiating it
    consumes an internal side-effect iterator; reloading resets it.
    """
    import custom_components.alexa_actions.views  # ensure loaded

    importlib.reload(sys.modules["custom_components.alexa_actions.views"])
    importlib.reload(sys.modules["custom_components.alexa_actions"])

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"skill_id": "amzn1.ask.skill.test123"}
    await init_mod.async_setup_entry(hass, entry)
    return hass.services.async_register.call_args.args[2]


def _make_service_call(data: dict, target_entity: str = "media_player.echo") -> MagicMock:
    """Create a mock ServiceCall with the given validated data."""
    call = MagicMock()
    call.data = data
    call.target = MagicMock()
    call.target.entity_id = {target_entity}
    return call


def _read_blueprint_text() -> str:
    """Read the raw blueprint YAML as text (avoids !input tag issues)."""
    with open(_BLUEPRINT_PATH) as f:
        return f.read()


# ===========================================================================
# Test class: SERVICE_SEND_SCHEMA
# ===========================================================================


class TestServiceSchemaContract:
    """Verify SERVICE_SEND_SCHEMA accepts all fields that skill_handler reads."""

    def test_schema_accepts_reprompt(self):
        """The schema must accept 'reprompt' so it reaches the handler."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        result = schema(
            {
                "text": "Test question",
                "alexa_device": "media_player.echo",
                "event_id": "test_event",
                "reprompt": "Say yes or no",
                "suppress_confirmation": False,
                "options": ["yes", "no"],
            }
        )
        assert result["reprompt"] == "Say yes or no"

    def test_schema_accepts_text_without_reprompt(self):
        """Backward compat: reprompt is optional."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        result = schema({"text": "Hello", "suppress_confirmation": True})
        assert "reprompt" not in result

    def test_schema_rejects_unknown_keys(self):
        """Schema must reject keys that the service does not understand."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        with pytest.raises(Exception):
            schema(
                {
                    "text": "Hello",
                    "suppress_confirmation": False,
                    "unknown_field": "oops",
                }
            )

    def test_schema_requires_text(self):
        """Schema must require the 'text' field."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        with pytest.raises(Exception):
            schema({"suppress_confirmation": False})


# ===========================================================================
# Test class: Payload builder
# ===========================================================================


class TestPayloadContract:
    """Verify the payload built by async_send_notification includes reprompt."""

    @pytest.mark.asyncio
    async def test_payload_includes_reprompt(self):
        """When reprompt is provided, it must appear in the entity payload."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "reprompt": "Say yes or no",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        entity_id = set_call.args[0]
        payload = json.loads(set_call.args[1])

        assert entity_id == sh.INPUT_TEXT_ENTITY
        assert payload["text"] == "Did you take the pill?"
        assert payload["reprompt"] == "Say yes or no"
        assert "event" in payload

    @pytest.mark.asyncio
    async def test_payload_omits_empty_reprompt(self):
        """Empty/missing reprompt should NOT appear in the payload."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Hello",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        payload = json.loads(set_call.args[1])

        assert "reprompt" not in payload

    @pytest.mark.asyncio
    async def test_payload_includes_options(self):
        """Options must survive into the payload when provided."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Pick one",
                "options": ["pizza", "pasta", "salad"],
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        payload = json.loads(set_call.args[1])

        assert payload["options"] == ["pizza", "pasta", "salad"]


# ===========================================================================
# Test class: Blueprint (text-based to avoid !input tag issues)
# ===========================================================================


class TestBlueprintContract:
    """Verify blueprint references match the service schema.

    The blueprint uses HA-specific ``!input`` YAML tags which
    ``yaml.safe_load`` cannot parse.  Tests read the raw file as text
    and use regex / structural checks instead.
    """

    def test_blueprint_calls_correct_service(self):
        """Blueprint must call alexa_actions.send."""
        text = _read_blueprint_text()
        assert re.search(r"action:\s*alexa_actions\.send", text), (
            "Blueprint does not call alexa_actions.send"
        )

    def test_blueprint_includes_reprompt_in_service_data(self):
        """Blueprint must pass reprompt in the service call data."""
        text = _read_blueprint_text()
        assert re.search(r"^\s+reprompt:", text, re.MULTILINE), (
            "Blueprint service call data does not include 'reprompt' key"
        )

    def test_blueprint_has_reprompt_text_input(self):
        """Blueprint must define a reprompt_text input."""
        text = _read_blueprint_text()
        assert re.search(r"^\s+reprompt_text:", text, re.MULTILINE), (
            "Blueprint does not define 'reprompt_text' input"
        )

    def test_blueprint_reprompt_input_has_default(self):
        """reprompt_text input should default to empty string."""
        text = _read_blueprint_text()
        match = re.search(
            r"reprompt_text:.*?default:\s*[\"']{2}", text, re.DOTALL
        )
        assert match, "reprompt_text input should have default: ''"

    def test_blueprint_has_all_service_schema_fields(self):
        """Every user-facing field in SERVICE_SEND_SCHEMA should be
        represented in the blueprint's service call data."""
        text = _read_blueprint_text()
        for field in ("text", "suppress_confirmation", "reprompt"):
            assert re.search(
                rf"^\s+{field}\s*:", text, re.MULTILINE
            ), f"Blueprint missing field: {field}"


# ===========================================================================
# Test class: End-to-end reprompt pipeline
# ===========================================================================


class TestEndToEndReprompt:
    """Full pipeline: service call -> entity state -> handler -> response."""

    @pytest.mark.asyncio
    async def test_reprompt_flows_through_pipeline(self):
        """Reprompt survives the full service->entity->handler pipeline."""
        # Step 1: Service call writes entity state
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "reprompt": "Say yes or no",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data))

        # Step 2: Read the payload that was written to entity state
        payload_json = hass.states.async_set.call_args.args[1]

        # Step 3: Set up a NEW hass mock with this payload for skill_handler
        hass2 = MagicMock()
        mock_state = MagicMock()
        mock_state.state = payload_json
        hass2.states.get.return_value = mock_state

        # Step 4: Send a LaunchRequest through skill_handler
        launch_body = {
            "request": {"type": "LaunchRequest", "locale": "en-US"},
        }
        response = await sh.handle_alexa_request(hass2, launch_body)

        # Step 5: Verify reprompt appears in response
        assert response["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert response["response"]["reprompt"]["outputSpeech"]["text"] == "Say yes or no"
        assert response["response"]["shouldEndSession"] is False

    @pytest.mark.asyncio
    async def test_missing_reprompt_falls_back_to_text(self):
        """When no reprompt is set, handler falls back to the notification text."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data))

        payload_json = hass.states.async_set.call_args.args[1]

        hass2 = MagicMock()
        mock_state = MagicMock()
        mock_state.state = payload_json
        hass2.states.get.return_value = mock_state

        launch_body = {
            "request": {"type": "LaunchRequest", "locale": "en-US"},
        }
        response = await sh.handle_alexa_request(hass2, launch_body)

        # Reprompt should fall back to the notification text
        assert response["response"]["reprompt"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert response["response"]["shouldEndSession"] is False


# ===========================================================================
# Test class: Dialog contract
# ===========================================================================


class TestDialogContract:
    """Verify dialog definition flows through the service layer correctly."""

    def test_schema_accepts_dialog(self):
        """SERVICE_SEND_SCHEMA must accept a dialog dict."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        result = schema(
            {
                "text": "Setting a reminder",
                "suppress_confirmation": False,
                "dialog": {
                    "intent": "String",
                    "slots": [
                        {"name": "item", "type": "AMAZON.Person", "prompt": "What?"},
                    ],
                },
            }
        )
        assert result["dialog"]["intent"] == "String"
        assert len(result["dialog"]["slots"]) == 1

    @pytest.mark.asyncio
    async def test_payload_includes_dialog(self):
        """When dialog is provided, it must appear in the entity payload."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        dialog_def = {
            "intent": "String",
            "slots": [
                {"name": "name", "type": "AMAZON.Person", "prompt": "What is your name?"},
            ],
            "confirm": True,
            "confirm_prompt": "Your name is {name}. Correct?",
        }
        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Tell me your name",
                "suppress_confirmation": False,
                "dialog": dialog_def,
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        payload = json.loads(set_call.args[1])

        assert payload["dialog"] == dialog_def

    @pytest.mark.asyncio
    async def test_payload_omits_empty_dialog(self):
        """No dialog key when not provided — backward compatible."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Hello",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        payload = json.loads(set_call.args[1])

        assert "dialog" not in payload
