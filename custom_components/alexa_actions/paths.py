"""Shared path utilities for locating Lambda source files."""
from __future__ import annotations

from pathlib import Path

_COMPONENT_DIR = Path(__file__).resolve().parent


def find_lambda_dir() -> Path:
    """Locate the ``lambda/`` source directory.

    Search order:
    1. ``custom_components/alexa_actions/lambda/`` (bundled via HACS).
    2. ``lambda/`` relative to the custom_components root (dev layout).

    Raises FileNotFoundError if neither location exists.
    """
    bundled = _COMPONENT_DIR / "lambda"
    if bundled.is_dir():
        return bundled

    dev_layout = _COMPONENT_DIR.parent.parent / "lambda"
    if dev_layout.is_dir():
        return dev_layout

    raise FileNotFoundError(
        f"Lambda source directory not found. Searched: {bundled}, {dev_layout}"
    )
