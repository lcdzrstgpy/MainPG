"""HTTP surface for the loopback-only ClipForge sidecar."""

from __future__ import annotations

from fastapi import APIRouter

from .service import ClipForgeService, ClipForgeStatus


def create_router(service: ClipForgeService) -> APIRouter:
    router = APIRouter(prefix="/api/clipforge", tags=["clipforge"])

    @router.get("/status")
    def status() -> dict[str, object]:
        return _payload(service.status())

    @router.post("/start")
    def start() -> dict[str, object]:
        return _payload(service.start())

    return router


def _payload(status: ClipForgeStatus) -> dict[str, object]:
    return {
        "available": status.available,
        "state": status.state,
        "url": status.url,
        "message": status.message,
    }
