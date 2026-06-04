"""Tests for lambda/utils.py — get_logger and _string_to_bool."""

import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from utils import get_logger, _string_to_bool


class TestStringToBool:
    """Tests for the _string_to_bool helper."""

    # --- Truthy values ---

    def test_lowercase_true(self):
        assert _string_to_bool("true") is True

    def test_mixed_case_true(self):
        assert _string_to_bool("True") is True

    def test_uppercase_true(self):
        assert _string_to_bool("TRUE") is True

    def test_bool_true(self):
        assert _string_to_bool(True) is True

    # --- Falsy values ---

    def test_lowercase_false(self):
        assert _string_to_bool("false") is False

    def test_mixed_case_false(self):
        assert _string_to_bool("False") is False

    def test_uppercase_false(self):
        assert _string_to_bool("FALSE") is False

    def test_bool_false(self):
        assert _string_to_bool(False) is False

    # --- Default fallback ---

    def test_unrecognised_string_returns_default_false(self):
        assert _string_to_bool("maybe") is False

    def test_unrecognised_string_with_default_true(self):
        assert _string_to_bool("maybe", default=True) is True

    def test_non_string_non_bool_returns_default(self):
        assert _string_to_bool(42) is False

    def test_none_returns_default(self):
        assert _string_to_bool(None) is False

    def test_none_with_custom_default(self):
        assert _string_to_bool(None, default=True) is True

    def test_integer_with_custom_default(self):
        assert _string_to_bool(0, default=True) is True

    def test_empty_string_returns_default(self):
        assert _string_to_bool("") is False

    def test_empty_string_with_default_true(self):
        assert _string_to_bool("", default=True) is True

    # --- Edge cases ---

    def test_whitespace_true(self):
        """Whitespace-padded 'true' is not matched — returns default."""
        assert _string_to_bool(" true ") is False

    def test_yes_not_recognised(self):
        assert _string_to_bool("yes") is False

    def test_one_not_recognised(self):
        assert _string_to_bool("1") is False


class TestGetLogger:
    """Tests for the get_logger factory."""

    def test_logger_name(self):
        logger = get_logger(debug=True)
        assert logger.name == "alexa-actions"

    def test_debug_level(self):
        logger = get_logger(debug=True)
        assert logger.level == logging.DEBUG

    def test_info_level(self):
        logger = get_logger(debug=False)
        assert logger.level == logging.INFO

    def test_returns_logger_instance(self):
        logger = get_logger()
        assert isinstance(logger, logging.Logger)

    def test_handler_attached(self):
        """When first created, a StreamHandler should be attached."""
        logger = get_logger(debug=True)
        assert len(logger.handlers) >= 1
        assert any(
            isinstance(h, logging.StreamHandler) for h in logger.handlers
        )

    def test_handler_not_duplicated(self):
        """Calling get_logger multiple times should not add duplicate handlers."""
        logger = get_logger(debug=True)
        initial_count = len(logger.handlers)
        logger2 = get_logger(debug=False)
        assert len(logger2.handlers) == initial_count
