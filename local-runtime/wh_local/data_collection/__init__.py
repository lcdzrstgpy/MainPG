"""Public registration surface for the host-independent daily-selection module."""

from .routes import (
    CachedDailySelectionImage,
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from .service import DailySelectionService


__all__ = [
    "CachedDailySelectionImage",
    "DailySelectionActor",
    "DailySelectionRouteDependencies",
    "DailySelectionService",
    "register_daily_selection_routes",
]
