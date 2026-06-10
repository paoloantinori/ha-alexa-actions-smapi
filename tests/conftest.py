"""Shared fixtures and module mocks for the test suite."""

import types
import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock third-party / external modules that are not installed in the test
# environment.  Using setdefault so that a real installation is not
# overwritten if it happens to be present.
# ---------------------------------------------------------------------------

# AbortFlow is an Exception subclass used by config_flow — must be a real
# class on a real module for ``from ... import AbortFlow`` to work.
class _MockAbortFlow(Exception):
    pass

_mock_data_flow = types.ModuleType("homeassistant.data_entry_flow")
_mock_data_flow.AbortFlow = _MockAbortFlow
_mock_data_flow.FlowResult = MagicMock

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
    "homeassistant.data_entry_flow": _mock_data_flow,
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
