"""Public registration surface for the host-independent daily-selection module."""

from .routes import (
    CachedDailySelectionImage,
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from .service import DailySelectionService
from .plugin_queue import DataCollectionPluginQueue, PluginCommand, TEMU_LINK_CAPTURE


__all__ = [
    "CachedDailySelectionImage",
    "DailySelectionActor",
    "DailySelectionRouteDependencies",
    "DailySelectionService",
    "DataCollectionPluginQueue",
    "PluginCommand",
    "TEMU_LINK_CAPTURE",
    "register_daily_selection_routes",
]
