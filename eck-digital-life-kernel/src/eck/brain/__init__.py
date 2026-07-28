"""Replaceable brain providers."""

from eck.brain.base import BrainProvider
from eck.brain.mock import MockBrainProvider
from eck.brain.ollama import OllamaBrainProvider

__all__ = ["BrainProvider", "MockBrainProvider", "OllamaBrainProvider"]

