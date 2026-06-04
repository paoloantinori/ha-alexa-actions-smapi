"""Dataclasses for Home Assistant state objects."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class HaState:
    event_id: Optional[str]
    suppress_confirmation: bool
    text: Optional[str]


@dataclass
class HaStateError:
    text: str
