from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

from .schemas import ThemeListResponse, ThemeManifest, ThemePackageResponse


def create_themes_router(themes_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/themes", tags=["themes"])

    def _list_theme_dirs() -> list[Path]:
        if not themes_dir.exists():
            return []
        return sorted(d for d in themes_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists())

    def _read_manifest(theme_dir: Path) -> dict[str, Any]:
        return json.loads((theme_dir / "manifest.json").read_text(encoding="utf-8"))

    @router.get("", response_model=ThemeListResponse)
    def list_themes() -> ThemeListResponse:
        """List all downloadable themes."""
        items = []
        for theme_dir in _list_theme_dirs():
            manifest = _read_manifest(theme_dir)
            items.append(
                {
                    "id": manifest["id"],
                    "label": manifest["label"],
                    "description": manifest["description"],
                    "swatch": manifest["swatch"],
                    "version": manifest["version"],
                    "installed": True,  # server-side packages are always available
                }
            )
        return ThemeListResponse(themes=items)

    @router.get("/{theme_id}/manifest", response_model=ThemeManifest)
    def get_manifest(theme_id: str) -> ThemeManifest:
        theme_dir = themes_dir / theme_id
        if not theme_dir.exists() or not (theme_dir / "manifest.json").exists():
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")
        return ThemeManifest(**_read_manifest(theme_dir))

    @router.get("/{theme_id}/package", response_model=ThemePackageResponse)
    def get_package(theme_id: str) -> ThemePackageResponse:
        """Download a theme package (manifest + bundled CSS)."""
        theme_dir = themes_dir / theme_id
        if not theme_dir.exists() or not (theme_dir / "manifest.json").exists():
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        manifest = _read_manifest(theme_dir)
        css_parts = []
        for filename in manifest.get("files", []):
            file_path = theme_dir / filename
            if not file_path.exists():
                raise HTTPException(status_code=500, detail=f"Theme file missing: {filename}")
            css_parts.append(file_path.read_text(encoding="utf-8"))

        return ThemePackageResponse(
            manifest=ThemeManifest(**manifest),
            css="\n\n".join(css_parts),
        )

    @router.get("/{theme_id}/theme.css")
    def get_theme_css(theme_id: str) -> FileResponse:
        """Raw CSS file for a theme (useful for direct <link> injection)."""
        theme_dir = themes_dir / theme_id
        css_path = theme_dir / "theme.css"
        if not css_path.exists():
            raise HTTPException(status_code=404, detail=f"Theme CSS for '{theme_id}' not found")
        return FileResponse(css_path, media_type="text/css")

    return router
