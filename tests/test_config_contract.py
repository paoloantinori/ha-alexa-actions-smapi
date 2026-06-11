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


def _read_payload_from_set_call(set_call) -> dict:
    """Read the active notification payload from a states.async_set call.

    The service now stores a JSON array (queue), so we unwrap and return
    the last appended item (the new notification).
    """
    stored = json.loads(set_call.args[1])
    assert isinstance(stored, list), f"Expected list, got {type(stored)}"
    return stored[-1]


def _make_mock_hass() -> MagicMock:
    """Build a mock HomeAssistant suitable for async_setup_entry."""
    hass = MagicMock()
    hass.data = {}
    hass.states.async_set = MagicMock()
    hass.states.get.return_value = None  # empty entity by default
    hass.bus.async_listen = MagicMock(return_value=MagicMock())
    hass.http = MagicMock()
    hass.http.register_view = MagicMock()
    hass.services.async_register = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.services.async_remove = MagicMock()
    return hass


async def _setup_entry_and_get_handler(hass: MagicMock):
    """Run async_setup_entry and return the ``send`` service handler.

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
    # Find the "send" handler (not "send_proactive") by inspecting
    # all async_register calls.  call_args only captures the last one.
    for call in hass.services.async_register.call_args_list:
        if call.args[1] == init_mod.SERVICE_SEND:
            return call.args[2]
    raise RuntimeError("alexa_actions.send service was not registered")


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
        payload = _read_payload_from_set_call(set_call)

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
        payload = _read_payload_from_set_call(set_call)

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
        payload = _read_payload_from_set_call(set_call)

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
        payload = _read_payload_from_set_call(set_call)

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
        payload = _read_payload_from_set_call(set_call)

        assert "dialog" not in payload


# ===========================================================================
# Test class: Queue storage contract
# ===========================================================================


class TestQueueStorageContract:
    """Verify service layer stores notifications as a JSON array queue."""

    @pytest.mark.asyncio
    async def test_send_stores_queue_array(self):
        """Service call should write a JSON array to the entity."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "First question?",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        stored = json.loads(set_call.args[1])
        assert isinstance(stored, list)
        assert len(stored) == 1
        assert stored[0]["text"] == "First question?"

    @pytest.mark.asyncio
    async def test_send_appends_to_existing_queue(self):
        """Second service call appends to the existing queue."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        # First notification — entity starts empty
        data1 = init_mod.SERVICE_SEND_SCHEMA(
            {"text": "First?", "suppress_confirmation": False}
        )
        await handler(_make_service_call(data1))

        first_stored = json.loads(hass.states.async_set.call_args.args[1])

        # Set up mock to return first queue for second call
        mock_state = MagicMock()
        mock_state.state = json.dumps(first_stored)
        hass.states.get.return_value = mock_state

        # Second notification — should append
        data2 = init_mod.SERVICE_SEND_SCHEMA(
            {"text": "Second?", "suppress_confirmation": False}
        )
        await handler(_make_service_call(data2))

        second_stored = json.loads(hass.states.async_set.call_args.args[1])
        assert isinstance(second_stored, list)
        assert len(second_stored) == 2
        assert second_stored[0]["text"] == "First?"
        assert second_stored[1]["text"] == "Second?"

    @pytest.mark.asyncio
    async def test_send_includes_alexa_device_in_payload(self):
        """Each queue item includes the target alexa_device."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {"text": "Test?", "suppress_confirmation": False}
        )
        await handler(_make_service_call(data, "media_player.kitchen"))

        stored = json.loads(hass.states.async_set.call_args.args[1])
        assert stored[0]["alexa_device"] == "media_player.kitchen"

    @pytest.mark.asyncio
    async def test_send_wraps_legacy_dict(self):
        """If entity has a legacy single-dict state, it gets wrapped."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        # Pre-set entity with a legacy single-dict state
        legacy_payload = json.dumps({
            "event": "old_evt",
            "text": "Old question?",
            "suppress_confirmation": "false",
        })
        mock_state = MagicMock()
        mock_state.state = legacy_payload
        hass.states.get.return_value = mock_state

        data = init_mod.SERVICE_SEND_SCHEMA(
            {"text": "New question?", "suppress_confirmation": False}
        )
        await handler(_make_service_call(data))

        stored = json.loads(hass.states.async_set.call_args.args[1])
        assert isinstance(stored, list)
        assert len(stored) == 2
        assert stored[0]["event"] == "old_evt"  # legacy item wrapped
        assert stored[1]["text"] == "New question?"  # new item appended

    @pytest.mark.asyncio
    async def test_queue_first_item_triggers_play_media(self):
        """When queue was empty, play_media should be called."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {"text": "First?", "suppress_confirmation": False}
        )
        await handler(_make_service_call(data))

        hass.services.async_call.assert_called_once()
        call_args = hass.services.async_call.call_args
        assert call_args.args[0] == "media_player"
        assert call_args.args[1] == "play_media"

    @pytest.mark.asyncio
    async def test_queue_second_item_does_not_trigger_play_media(self):
        """When queue already has items, play_media should NOT be called."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        # First notification
        data1 = init_mod.SERVICE_SEND_SCHEMA(
            {"text": "First?", "suppress_confirmation": False}
        )
        await handler(_make_service_call(data1))

        # Reset to track second call
        first_stored = json.loads(hass.states.async_set.call_args.args[1])
        mock_state = MagicMock()
        mock_state.state = json.dumps(first_stored)
        hass.states.get.return_value = mock_state
        hass.services.async_call.reset_mock()

        # Second notification — should NOT call play_media
        data2 = init_mod.SERVICE_SEND_SCHEMA(
            {"text": "Second?", "suppress_confirmation": False}
        )
        await handler(_make_service_call(data2))

        hass.services.async_call.assert_not_called()


class TestEndToEndQueuePipeline:
    """Full pipeline: service call queue -> entity state -> handler -> response -> advance."""

    @pytest.mark.asyncio
    async def test_queue_pipeline_full(self):
        """Two notifications queued, first answered, second becomes active."""
        # Step 1: First service call
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data1 = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "event_id": "pill_q",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data1))
        queue_json = hass.states.async_set.call_args.args[1]

        # Step 2: Second service call (append)
        mock_state = MagicMock()
        mock_state.state = queue_json
        hass.states.get.return_value = mock_state

        data2 = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you lock the door?",
                "event_id": "door_q",
                "suppress_confirmation": False,
            }
        )
        await handler(_make_service_call(data2))
        queue_json = hass.states.async_set.call_args.args[1]
        queue = json.loads(queue_json)
        assert len(queue) == 2

        # Step 3: Handler reads first item
        hass2 = MagicMock()
        mock_state2 = MagicMock()
        mock_state2.state = queue_json
        hass2.states.get.return_value = mock_state2

        response = await sh.handle_alexa_request(
            hass2,
            {"request": {"type": "LaunchRequest", "locale": "en-US"}},
        )
        assert response["response"]["outputSpeech"]["text"] == "Did you take the pill?"

        # Step 4: User says yes — handler fires event, advances queue
        response = await sh.handle_alexa_request(
            hass2,
            {"request": {"type": "IntentRequest", "intent": {"name": "AMAZON.YesIntent"}, "locale": "en-US"},
             "context": {"System": {}}},
        )
        assert response["response"]["shouldEndSession"] is True

        # Queue should have been advanced
        hass2.states.async_set.assert_called()
        remaining = json.loads(hass2.states.async_set.call_args.args[1])
        assert len(remaining) == 1
        assert remaining[0]["event"] == "door_q"

        # Step 5: Handler reads second item (now first)
        mock_state3 = MagicMock()
        mock_state3.state = json.dumps(remaining)
        hass2.states.get.return_value = mock_state3

        response = await sh.handle_alexa_request(
            hass2,
            {"request": {"type": "LaunchRequest", "locale": "en-US"}},
        )
        assert response["response"]["outputSpeech"]["text"] == "Did you lock the door?"


# ===========================================================================
# Test class: Display card contract
# ===========================================================================


class TestDisplayCardSchemaContract:
    """Verify SERVICE_SEND_SCHEMA accepts display_title and display_body."""

    def test_schema_accepts_display_title(self):
        """The schema must accept 'display_title'."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        result = schema(
            {
                "text": "Test question",
                "suppress_confirmation": False,
                "display_title": "Promemoria",
            }
        )
        assert result["display_title"] == "Promemoria"

    def test_schema_accepts_display_body(self):
        """The schema must accept 'display_body'."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        result = schema(
            {
                "text": "Test question",
                "suppress_confirmation": False,
                "display_body": "Hai preso la pastiglia oggi?",
            }
        )
        assert result["display_body"] == "Hai preso la pastiglia oggi?"

    def test_schema_accepts_both_display_fields(self):
        """The schema must accept both display fields together."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        result = schema(
            {
                "text": "Test question",
                "suppress_confirmation": False,
                "display_title": "Promemoria Pastiglia",
                "display_body": "Hai preso la pastiglia oggi?",
            }
        )
        assert result["display_title"] == "Promemoria Pastiglia"
        assert result["display_body"] == "Hai preso la pastiglia oggi?"

    def test_schema_omit_display_fields(self):
        """Backward compat: display fields are optional."""
        schema = init_mod.SERVICE_SEND_SCHEMA
        result = schema({"text": "Hello", "suppress_confirmation": True})
        assert "display_title" not in result
        assert "display_body" not in result


class TestDisplayCardPayloadContract:
    """Verify the payload built by async_send_notification includes display fields."""

    @pytest.mark.asyncio
    async def test_payload_includes_display_title(self):
        """When display_title is provided, it must appear in the entity payload."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "suppress_confirmation": False,
                "display_title": "Promemoria",
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        payload = _read_payload_from_set_call(set_call)

        assert payload["display_title"] == "Promemoria"

    @pytest.mark.asyncio
    async def test_payload_includes_display_body(self):
        """When display_body is provided, it must appear in the entity payload."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "suppress_confirmation": False,
                "display_body": "Hai preso la pastiglia oggi?",
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        payload = _read_payload_from_set_call(set_call)

        assert payload["display_body"] == "Hai preso la pastiglia oggi?"

    @pytest.mark.asyncio
    async def test_payload_includes_both_display_fields(self):
        """When both display fields are provided, both must appear in the payload."""
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "suppress_confirmation": False,
                "display_title": "Promemoria Pastiglia",
                "display_body": "Hai preso la pastiglia oggi?",
            }
        )
        await handler(_make_service_call(data))

        set_call = hass.states.async_set.call_args
        payload = _read_payload_from_set_call(set_call)

        assert payload["display_title"] == "Promemoria Pastiglia"
        assert payload["display_body"] == "Hai preso la pastiglia oggi?"

    @pytest.mark.asyncio
    async def test_payload_omits_display_fields_when_absent(self):
        """No display fields in payload when not provided — backward compatible."""
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
        payload = _read_payload_from_set_call(set_call)

        assert "display_title" not in payload
        assert "display_body" not in payload


class TestDisplayCardBlueprintContract:
    """Verify blueprint references display card fields."""

    def test_blueprint_has_display_title_input(self):
        """Blueprint must define a display_title input."""
        text = _read_blueprint_text()
        assert re.search(r"^\s+display_title:", text, re.MULTILINE), (
            "Blueprint does not define 'display_title' input"
        )

    def test_blueprint_has_display_body_input(self):
        """Blueprint must define a display_body input."""
        text = _read_blueprint_text()
        assert re.search(r"^\s+display_body:", text, re.MULTILINE), (
            "Blueprint does not define 'display_body' input"
        )

    def test_blueprint_includes_display_title_in_service_data(self):
        """Blueprint must pass display_title in the service call data."""
        text = _read_blueprint_text()
        assert re.search(r"^\s+display_title\s*:", text, re.MULTILINE), (
            "Blueprint service call data does not include 'display_title' key"
        )

    def test_blueprint_includes_display_body_in_service_data(self):
        """Blueprint must pass display_body in the service call data."""
        text = _read_blueprint_text()
        assert re.search(r"^\s+display_body\s*:", text, re.MULTILINE), (
            "Blueprint service call data does not include 'display_body' key"
        )

    def test_blueprint_display_title_input_has_default(self):
        """display_title input should default to empty string."""
        text = _read_blueprint_text()
        match = re.search(
            r"display_title:.*?default:\s*[\"']{2}", text, re.DOTALL
        )
        assert match, "display_title input should have default: ''"

    def test_blueprint_display_body_input_has_default(self):
        """display_body input should default to empty string."""
        text = _read_blueprint_text()
        match = re.search(
            r"display_body:.*?default:\s*[\"']{2}", text, re.DOTALL
        )
        assert match, "display_body input should have default: ''"


class TestEndToEndDisplayCard:
    """Full pipeline: service call -> entity state -> handler -> card in response."""

    @pytest.mark.asyncio
    async def test_display_card_flows_through_pipeline(self):
        """Display card survives the full service->entity->handler pipeline."""
        # Step 1: Service call writes entity state
        hass = _make_mock_hass()
        handler = await _setup_entry_and_get_handler(hass)

        data = init_mod.SERVICE_SEND_SCHEMA(
            {
                "text": "Did you take the pill?",
                "suppress_confirmation": False,
                "display_title": "Promemoria Pastiglia",
                "display_body": "Hai preso la pastiglia oggi?",
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

        # Step 5: Verify card appears in response
        assert response["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert response["response"]["shouldEndSession"] is False
        card = response["response"]["card"]
        assert card is not None
        assert card["type"] == "Simple"
        assert card["title"] == "Promemoria Pastiglia"
        assert card["content"] == "Hai preso la pastiglia oggi?"

    @pytest.mark.asyncio
    async def test_no_card_without_display_fields(self):
        """No card in response when display fields are absent — backward compatible."""
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

        assert response["response"]["outputSpeech"]["text"] == "Did you take the pill?"
        assert "card" not in response["response"]
