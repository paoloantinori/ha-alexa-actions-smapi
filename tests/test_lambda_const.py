"""Tests for lambda/const.py — verify constant values are correct strings."""

import sys
import os

# Ensure the lambda/ directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from const import (
    INPUT_TEXT_ENTITY,
    LOCALIZATION_ATTR,
    RESPONSE_YES,
    RESPONSE_NO,
    RESPONSE_NONE,
    RESPONSE_SELECT,
    RESPONSE_NUMERIC,
    RESPONSE_DURATION,
    RESPONSE_STRING,
    RESPONSE_DATE_TIME,
)


class TestLambdaConstants:
    """Verify every constant in lambda/const.py has the expected value."""

    def test_input_text_entity(self):
        assert INPUT_TEXT_ENTITY == "input_text.alexa_actionable_notification"

    def test_localization_attr(self):
        assert LOCALIZATION_ATTR == "_"

    def test_response_yes(self):
        assert RESPONSE_YES == "ResponseYes"

    def test_response_no(self):
        assert RESPONSE_NO == "ResponseNo"

    def test_response_none(self):
        assert RESPONSE_NONE == "ResponseNone"

    def test_response_select(self):
        assert RESPONSE_SELECT == "ResponseSelect"

    def test_response_numeric(self):
        assert RESPONSE_NUMERIC == "ResponseNumeric"

    def test_response_duration(self):
        assert RESPONSE_DURATION == "ResponseDuration"

    def test_response_string(self):
        assert RESPONSE_STRING == "ResponseString"

    def test_response_date_time(self):
        assert RESPONSE_DATE_TIME == "ResponseDateTime"

    def test_all_constants_are_strings(self):
        """Every exported constant must be a str."""
        for name in [
            "INPUT_TEXT_ENTITY",
            "LOCALIZATION_ATTR",
            "RESPONSE_YES",
            "RESPONSE_NO",
            "RESPONSE_NONE",
            "RESPONSE_SELECT",
            "RESPONSE_NUMERIC",
            "RESPONSE_DURATION",
            "RESPONSE_STRING",
            "RESPONSE_DATE_TIME",
        ]:
            assert isinstance(
                __import__("const").__dict__[name], str
            ), f"{name} is not a str"
