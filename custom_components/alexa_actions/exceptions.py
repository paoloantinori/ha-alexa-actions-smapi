"""Custom exceptions for the Alexa Actions integration."""
from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError


class AWSDeploymentError(HomeAssistantError):
    """Raised when an AWS Lambda deployment operation fails."""


class SMAPIError(HomeAssistantError):
    """Raised when an Alexa SMAPI operation fails."""


class HostedSkillError(HomeAssistantError):
    """Raised when an Alexa-hosted skill operation fails."""
