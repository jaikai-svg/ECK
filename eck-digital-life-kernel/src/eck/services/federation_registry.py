"""Backward-compatible import facade for the experimental P7 registry."""

from eck.experimental.p7.federation_registry import CapabilityRegistryService, CosignBlobService

__all__ = ["CapabilityRegistryService", "CosignBlobService"]

