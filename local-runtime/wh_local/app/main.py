from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ..config import default_config
from ..db import init_db
from ..modules.basic_settings.router import create_router as create_basic_settings_router


def create_app(database_path: Path | None = None) -> FastAPI:
    config = default_config()
    db_path = database_path or config.database_path
    init_db(db_path)

    # 本地运行时统一入口：以后其它桌面模块也从这里挂载自己的 router。
    app = FastAPI(title="H Smart Ecommerce Local Runtime", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database_path": str(db_path)}

    # 基础设置模块：当前先接入“系统配置”后端接口。
    app.include_router(create_basic_settings_router(db_path))
    return app


app = create_app()
