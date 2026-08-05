from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI

from ..config import default_config
from ..customer.auth_service import SQLiteCustomerAuthService
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
from ..data_collection.plugin_queue import DataCollectionPluginQueue
from ..db import init_db
from ..modules.basic_settings.router import create_router as create_basic_settings_router
from ..price_verification import (
    PriceVerificationRouteDependencies,
    register_price_verification_routes,
)
from ..price_verification.contracts import PriceVerificationActor
from ..session import Actor, actor_from_authorization, daily_selection_actor_from_authorization

def _price_verification_actor(
    actor: Actor = Depends(actor_from_authorization),
) -> PriceVerificationActor:
    """Bridge the local host actor to the price-verification workspace."""
    return PriceVerificationActor(actor_id=actor.id, workspace_id=actor.workspace_id)



def _provider_config(actor: DailySelectionActor) -> Mapping[str, Any]:
    """Resolve OneBound credentials from local global configuration."""
    config = default_config()
    return {
        "api_key": config.onebound_1688_api_key,
        "api_secret": config.onebound_1688_api_secret,
        "base_url": config.onebound_1688_base_url,
        "enabled": config.onebound_1688_enabled,
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

    remote_customer_auth = CustomerAuthClient(config.customer_auth_base_url)
    customer_auth = remote_customer_auth if remote_customer_auth.configured() else SQLiteCustomerAuthService(db_path)
    customer_sessions = LocalSessionService(SQLiteCustomerSessionStore(db_path))
    app.include_router(create_customer_router(customer_auth, customer_sessions))

    app.include_router(create_basic_settings_router(db_path))
    plugin_queue = DataCollectionPluginQueue(db_path)
    _register_data_collection(app, db_path, plugin_queue)

    # 核价及货源模块
    _register_price_verification(app, db_path, config.data_dir, plugin_queue)

    return app


def _register_data_collection(
    app: FastAPI, db_path: Path, plugin_queue: DataCollectionPluginQueue
) -> None:
    """Register daily-selection routes with the host-owned adapters."""
    dependencies = DailySelectionRouteDependencies(
        resolve_actor=daily_selection_actor_from_authorization,
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
        database_path=db_path,
        plugin_queue=plugin_queue,
    )
    register_daily_selection_routes(app.router, dependencies)

def _register_price_verification(
    app: FastAPI,
    db_path: Path,
    data_dir: Path,
    plugin_queue: DataCollectionPluginQueue,
) -> None:
    """Register read-only price-verification routes with host-owned adapters."""
    dependencies = PriceVerificationRouteDependencies(
        resolve_actor=_price_verification_actor,
        database_path=db_path,
        output_root=data_dir / "price-verification",
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
        plugin_queue=plugin_queue,
    )
    register_price_verification_routes(app.router, dependencies)



app = create_app()
