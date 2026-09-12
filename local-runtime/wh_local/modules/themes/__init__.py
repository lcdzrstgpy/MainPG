"""Theme marketplace backend module.

Provides endpoints for listing downloadable UI themes and fetching their
CSS packages. Themes live in ``wh_local/data/themes/<id>/``.
"""

from .router import create_themes_router

__all__ = ["create_themes_router"]
