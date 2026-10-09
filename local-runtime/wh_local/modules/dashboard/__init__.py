"""Workspace dashboard aggregation backend module."""

from .router import create_router
from .service import DashboardService

__all__ = ["DashboardService", "create_router"]
