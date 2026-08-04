from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalRuntimeConfig:
    data_dir: Path
    database_path: Path
    dev_admin_token: str
    customer_auth_base_url: str


def default_config(workspace: Path | None = None) -> LocalRuntimeConfig:
    root = workspace or Path.cwd()
    # 允许通过环境变量切换数据目录，方便开发、测试和打包后的桌面端各自使用不同库。
    data_dir = Path(os.environ.get("WH_LOCAL_DATA_DIR", "") or root / "outputs" / "wh-local")
    database_path = Path(os.environ.get("WH_LOCAL_DATABASE_PATH", "") or data_dir / "workbench.sqlite3")
    return LocalRuntimeConfig(
        data_dir=data_dir,
        database_path=database_path,
        dev_admin_token=os.environ.get("WH_LOCAL_DEV_ADMIN_TOKEN", "dev-admin-token"),
        customer_auth_base_url=os.environ.get("WH_LOCAL_CUSTOMER_AUTH_BASE_URL", ""),
    )
