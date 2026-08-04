from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ..config import default_config
from ..customer.db_store import SQLiteCustomerSessionStore
from ..customer.local_session import LocalSessionService
from ..customer.remote_client import CustomerAuthClient
from ..customer.routes import create_customer_router
from ..data_collection import (
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from ..data_collection.provider import OneBound1688Provider
from ..db import init_db
from ..modules.basic_settings.router import create_router as create_basic_settings_router
from ..session import daily_selection_actor_from_authorization


def _provider_config(actor: DailySelectionActor) -> Mapping[str, Any]:
    """Resolve OneBound 1688 credentials from environment variables."""
    api_key = os.environ.get("DAILY_SELECTION_ONEBOUND_API_KEY", "")
    api_secret = os.environ.get("DAILY_SELECTION_ONEBOUND_API_SECRET", "")
    base_url = os.environ.get(
        "DAILY_SELECTION_ONEBOUND_BASE_URL",
        "https://api.onebound.cn/1688/api_call.php",
    )
    enabled = os.environ.get("DAILY_SELECTION_ONEBOUND_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
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

    customer_auth = CustomerAuthClient(config.customer_auth_base_url)
    customer_sessions = LocalSessionService(SQLiteCustomerSessionStore(db_path))
    app.include_router(create_customer_router(customer_auth, customer_sessions))

    app.include_router(create_basic_settings_router(db_path))
    _register_data_collection(app, db_path)
    return app


def _register_data_collection(app: FastAPI, db_path: Path) -> None:
    """Register daily-selection routes with the host-owned adapters."""
    dependencies = DailySelectionRouteDependencies(
        resolve_actor=daily_selection_actor_from_authorization,
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
        database_path=db_path,
    )
    register_daily_selection_routes(app.router, dependencies)


app = create_app()
