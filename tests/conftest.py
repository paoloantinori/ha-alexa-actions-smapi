"""Shared fixtures and module mocks for the test suite."""

import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock third-party / external modules that are not installed in the test
# environment.  Using setdefault so that a real installation is not
# overwritten if it happens to be present.
# ---------------------------------------------------------------------------

_mock_modules = {
    # ask-sdk (Lambda-side)
    "ask_sdk_core": MagicMock(),
    "ask_sdk_core.dispatch_components": MagicMock(),
    "ask_sdk_core.skill_builder": MagicMock(),
    "ask_sdk_core.utils": MagicMock(),
    "ask_sdk_model": MagicMock(),
    "ask_sdk_model.slu": MagicMock(),
    "ask_sdk_model.slu.entityresolution": MagicMock(),
    # isodate (Lambda-side)
    "isodate": MagicMock(),
    # Home Assistant core
    "homeassistant": MagicMock(),
    "homeassistant.core": MagicMock(),
    "homeassistant.exceptions": MagicMock(),
    "homeassistant.config_entries": MagicMock(),
    "homeassistant.const": MagicMock(),
    "homeassistant.data_entry_flow": MagicMock(),
    "homeassistant.helpers": MagicMock(),
    "homeassistant.helpers.selector": MagicMock(),
    "homeassistant.helpers.network": MagicMock(),
    "homeassistant.components": MagicMock(),
    "homeassistant.components.http": MagicMock(),
    # voluptuous (used by config_flow)
    "voluptuous": MagicMock(),
    # aiohttp (used by api.py)
    "aiohttp": MagicMock(),
}

for _name, _mock in _mock_modules.items():
    sys.modules.setdefault(_name, _mock)
