from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ..config import default_config
from ..data_collection import (
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from ..data_collection.provider import OneBound1688Provider
from ..db import init_db
from ..modules.basic_settings.router import create_router as create_basic_settings_router
from ..modules.basic_settings.service import SystemConfigService
from ..modules.product_processing import create_product_processing_router
from ..modules.profit_activity import create_profit_activity_router, create_profit_activity_service
from ..session import Actor, actor_from_authorization


def _resolve_daily_selection_actor(
    actor: Actor | None = None,
) -> dict[str, str]:
    """Bridge host Actor to the data-collection module's DailySelectionActor.

    In local dev mode the actor id doubles as workspace id.  When the full
    customer-auth framework lands this should map the platform workspace_id.
    """
    if actor is None:
        # Fallback when called outside a request context (e.g. health check).
        return {"actor_id": "local-demo", "workspace_id": "local-demo"}
    return {"actor_id": actor.id, "workspace_id": actor.id}


def _provider_config(actor: DailySelectionActor) -> Mapping[str, Any]:
    """Resolve OneBound 1688 credentials from environment variables.

    All secrets stay in the process environment; they are never written to
    the response or persisted outside the provider instance.
    """
    api_key = os.environ.get("DAILY_SELECTION_ONEBOUND_API_KEY", "")
    api_secret = os.environ.get("DAILY_SELECTION_ONEBOUND_API_SECRET", "")
    base_url = os.environ.get(
        "DAILY_SELECTION_ONEBOUND_BASE_URL",
        "https://api.onebound.cn/1688/api_call.php",
    )
    enabled = os.environ.get("DAILY_SELECTION_ONEBOUND_ENABLED", "").strip().lower() in (
        "1", "true", "yes",
    )
    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "base_url": base_url,
        "enabled": enabled,
    }


def _provider_factory(config: Mapping[str, Any]) -> OneBound1688Provider:
    """Create the OneBound provider from resolved configuration."""
    return OneBound1688Provider(config)


def create_app(database_path: Path | None = None) -> FastAPI:
    config = default_config()
    db_path = database_path or config.database_path
    init_db(db_path)

    app = FastAPI(title="H Smart Ecommerce Local Runtime", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database_path": str(db_path)}

    # 基础设置模块
    app.include_router(create_basic_settings_router(db_path))

    # 每日选品数据采集模块
    _register_data_collection(app, db_path)

    # 利润活动保留既有 /api/v1 前缀，避免影响已对接的页面接口。测试注入
    # database_path 时将模块库置于测试目录；常规运行仍使用模块默认库。
    profit_database_url = None
    if database_path is not None:
        profit_database_url = f"sqlite:///{(db_path.parent / 'profit_activity.sqlite3').as_posix()}"
    profit_activity_service = create_profit_activity_service(profit_database_url)
    # Preserve the versioned route and also serve the path used by the
    # original Profit Activity page.  The two routers share one service and
    # SQLite/WAL database, so this is an alias rather than a second module.
    app.include_router(create_profit_activity_router(profit_activity_service), prefix="/api/v1")
    app.include_router(create_profit_activity_router(profit_activity_service))

    # 产品处理使用自己的 SQLAlchemy 表和 WAL 数据库；它通过 handoff HTTP
    # 合同消费每日采集确认结果，不直接访问 daily_selection_* 表。
    product_database_url = f"sqlite:///{db_path.as_posix()}" if database_path is not None else None
    product_assets_root = db_path.parent / "product_processing_assets" if database_path is not None else None
    system_config_service = SystemConfigService(db_path)
    app.include_router(
        create_product_processing_router(
            database_url=product_database_url,
            assets_root=product_assets_root,
            media_config_provider=system_config_service.runtime_media_config,
        )
    )

    @app.on_event("shutdown")
    def close_profit_activity_database() -> None:
        profit_activity_service.close()

    return app


def _register_data_collection(app: FastAPI, db_path: Path) -> None:
    """Register daily-selection routes with the host-owned adapters."""
    dependencies = DailySelectionRouteDependencies(
        resolve_actor=actor_from_authorization,
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
        database_path=db_path,
    )
    router = app.router
    register_daily_selection_routes(router, dependencies)


app = create_app()
