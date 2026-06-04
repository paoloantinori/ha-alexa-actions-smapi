"""Tests for lambda/schemas.py — HaState and HaStateError dataclasses."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from schemas import HaState, HaStateError


class TestHaState:
    """Tests for the HaState dataclass."""

    def test_creation_with_all_fields(self):
        state = HaState(event_id="test-123", suppress_confirmation=False, text="Hello")
        assert state.event_id == "test-123"
        assert state.suppress_confirmation is False
        assert state.text == "Hello"

    def test_optional_fields_none(self):
        state = HaState(event_id=None, suppress_confirmation=True, text=None)
        assert state.event_id is None
        assert state.suppress_confirmation is True
        assert state.text is None

    def test_event_id_can_be_uuid(self):
        state = HaState(
            event_id="550e8400-e29b-41d4-a716-446655440000",
            suppress_confirmation=False,
            text="Turn on the lights",
        )
        assert len(state.event_id) == 36
        assert "-" in state.event_id

    def test_suppress_confirmation_truthy_values(self):
        state = HaState(event_id="x", suppress_confirmation=True, text="y")
        assert state.suppress_confirmation is True

    def test_text_with_special_characters(self):
        text = "It's 50% off — deal!"
        state = HaState(event_id=None, suppress_confirmation=False, text=text)
        assert state.text == text

    def test_immutable_via_dataclass_equality(self):
        """Two HaState objects with same values should be equal."""
        s1 = HaState(event_id="abc", suppress_confirmation=False, text="hi")
        s2 = HaState(event_id="abc", suppress_confirmation=False, text="hi")
        assert s1 == s2

    def test_different_values_not_equal(self):
        s1 = HaState(event_id="abc", suppress_confirmation=False, text="hi")
        s2 = HaState(event_id="abc", suppress_confirmation=True, text="hi")
        assert s1 != s2


class TestHaStateError:
    """Tests for the HaStateError dataclass."""

    def test_creation(self):
        err = HaStateError(text="Something went wrong")
        assert err.text == "Something went wrong"

    def test_text_is_required(self):
        """HaStateError.text has no default, so omitting it raises TypeError."""
        import pytest

        with pytest.raises(TypeError):
            HaStateError()

    def test_error_with_empty_string(self):
        err = HaStateError(text="")
        assert err.text == ""

    def test_error_equality(self):
        e1 = HaStateError(text="fail")
        e2 = HaStateError(text="fail")
        assert e1 == e2
