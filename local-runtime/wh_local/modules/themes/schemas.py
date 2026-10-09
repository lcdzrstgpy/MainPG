from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ThemeManifest(BaseModel):
    id: str
    label: str
    description: str
    swatch: str
    version: str
    files: list[str]


class ThemeListItem(BaseModel):
    id: str
    label: str
    description: str
    swatch: str
    version: str
    installed: bool = False


class ThemeListResponse(BaseModel):
    themes: list[ThemeListItem]


class ThemePackageResponse(BaseModel):
    manifest: ThemeManifest
    css: str
