from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from .service import DashboardService


def create_router(database_path: Path) -> APIRouter:
    router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
    service = DashboardService(database_path)

    @router.get("/overview")
    def get_overview(
        workspace_id: str = Header(default="default", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        # 工作台指标按工作区隔离，口径与产品处理页一致（X-Workspace-ID）。
        return service.overview(_workspace(workspace_id))

    return router


def _workspace(value: str | None) -> str:
    normalized = str(value or "default").strip()
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "workspace id must not be empty")
    return normalized
