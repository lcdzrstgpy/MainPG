"""Secure, local-only browser-plugin bridge for price verification."""

from .routes import PluginBridgeRouteDependencies, register_plugin_bridge_routes
from .service import (
    IssuedPairingCode,
    PluginAuthenticationError,
    PluginBridgeService,
    PluginLeaseError,
    PluginSession,
    PluginSessionSummary,
)

__all__ = [
    "IssuedPairingCode",
    "PluginAuthenticationError",
    "PluginBridgeRouteDependencies",
    "PluginBridgeService",
    "PluginLeaseError",
    "PluginSession",
    "PluginSessionSummary",
    "register_plugin_bridge_routes",
]
