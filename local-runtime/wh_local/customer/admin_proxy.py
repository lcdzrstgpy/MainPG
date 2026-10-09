from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request


def create_admin_proxy_router(remote_auth: Any, sessions: Any):
    """Create FastAPI passthrough routes for the platform admin API.

    The workbench serves the web frontend on the same origin, but the real
    admin endpoints (pricing rules, key grants, audit log) live on the remote
    platform auth service.  This router authorizes the local session, resolves
    the remote token, and forwards the request.  Only ``/api/admin/billing/*``
    paths are exposed.
    """

    router = APIRouter(prefix="/api/admin", tags=["admin-proxy"])

    def remote_token_from_local_session(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        session = sessions.store.get_session(token)
        if session is None:
            raise HTTPException(status_code=403, detail="not authorized")
        if not session.remote_token:
            raise HTTPException(status_code=503, detail="remote customer session is missing")
        return session.remote_token

    @router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy_admin(
        path: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not path.startswith("billing/"):
            raise HTTPException(status_code=404, detail="unknown admin endpoint")
        remote_token = remote_token_from_local_session(authorization)
        payload: dict[str, Any] | None = None
        if request.method in ("POST", "PUT", "PATCH"):
            raw = await request.body()
            payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        return remote_auth.admin_request(
            remote_token,
            request.method,
            f"/api/admin/{path}",
            payload,
        )

    return router
