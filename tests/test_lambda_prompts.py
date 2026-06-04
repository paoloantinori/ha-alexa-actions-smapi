"""Tests for lambda/prompts.py — verify all prompt keys exist and are strings."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

import prompts


class TestPrompts:
    """Verify prompt constant values."""

    EXPECTED_KEYS = [
        ("ERROR_401", "ERROR_401"),
        ("ERROR_404", "ERROR_404"),
        ("ERROR_400", "ERROR_400"),
        ("ERROR_ACOUSTIC", "ERROR_ACOUSTIC"),
        ("ERROR_CONFIG", "ERROR_CONFIG"),
        ("HELP_MESSAGE", "HELP_MESSAGE"),
        ("OKAY", "OKAY"),
        ("STRING", "STRING"),
        ("SELECTED", "SELECTED"),
        ("SKILL_NAME", "SKILL_NAME"),
        ("STOP_MESSAGE", "STOP_MESSAGE"),
        ("WELCOME_MESSAGE", "WELCOME_MESSAGE"),
    ]

    def test_all_expected_keys_exist(self):
        for attr, value in self.EXPECTED_KEYS:
            assert hasattr(prompts, attr), f"Missing prompt key: {attr}"
            assert getattr(prompts, attr) == value

    def test_all_values_are_strings(self):
        for attr, _ in self.EXPECTED_KEYS:
            val = getattr(prompts, attr)
            assert isinstance(val, str), f"{attr} is not a str: {type(val)}"

    def test_no_extra_keys(self):
        """Module should only expose the known prompt constants."""
        public_attrs = [
            name
            for name in dir(prompts)
            if not name.startswith("_")
        ]
        expected_names = {attr for attr, _ in self.EXPECTED_KEYS}
        actual_names = set(public_attrs)
        assert actual_names == expected_names, (
            f"Unexpected prompts: {actual_names - expected_names}"
        )

    def test_error_prompts_have_error_prefix(self):
        """All error-related prompts should start with ERROR_."""
        for attr, _ in self.EXPECTED_KEYS:
            if "ERROR" in attr:
                assert attr.startswith("ERROR_"), f"{attr} doesn't follow ERROR_ prefix"
                assert getattr(prompts, attr).startswith("ERROR_"), (
                    f"Value of {attr} doesn't start with ERROR_"
                )
