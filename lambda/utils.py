"""Logging utilities for the alexa-actions Lambda."""

import logging


def get_logger(debug: bool = False) -> logging.Logger:
    """Return a configured logger for the alexa-actions namespace.

    Args:
        debug: When True, set level to DEBUG; otherwise INFO.

    Returns:
        A ``logging.Logger`` with a single ``StreamHandler`` attached.
    """
    logger = logging.getLogger("alexa-actions")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)
    return logger


def _string_to_bool(value, default=False):
    """Convert a string/bool value to a boolean.

    Args:
        value: The value to convert.  Accepts ``bool`` directly, or the
            strings ``"true"`` / ``"false"`` (case-insensitive).
        default: Fallback when *value* cannot be interpreted.

    Returns:
        The converted boolean, or *default*.
    """
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return default
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return default
