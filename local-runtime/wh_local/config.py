from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalRuntimeConfig:
    data_dir: Path
    database_path: Path
    dev_admin_token: str
    customer_auth_base_url: str
    onebound_1688_api_key: str
    onebound_1688_api_secret: str
    onebound_1688_base_url: str
    onebound_1688_enabled: bool


def default_config(workspace: Path | None = None) -> LocalRuntimeConfig:
    root = workspace or Path.cwd()
    local_secrets = _local_onebound_config()
    # 允许通过环境变量切换数据目录，方便开发、测试和打包后的桌面端各自使用不同库。
    data_dir = Path(os.environ.get("WH_LOCAL_DATA_DIR", "") or root / "outputs" / "wh-local")
    database_path = Path(os.environ.get("WH_LOCAL_DATABASE_PATH", "") or data_dir / "workbench.sqlite3")
    return LocalRuntimeConfig(
        data_dir=data_dir,
        database_path=database_path,
        dev_admin_token=os.environ.get("WH_LOCAL_DEV_ADMIN_TOKEN", "dev-admin-token"),
        customer_auth_base_url=os.environ.get("WH_LOCAL_CUSTOMER_AUTH_BASE_URL", ""),
        onebound_1688_api_key=os.environ.get(
            "DAILY_SELECTION_ONEBOUND_API_KEY", local_secrets.get("api_key", "")
        ),
        onebound_1688_api_secret=os.environ.get(
            "DAILY_SELECTION_ONEBOUND_API_SECRET", local_secrets.get("api_secret", "")
        ),
        onebound_1688_base_url=os.environ.get(
            "DAILY_SELECTION_ONEBOUND_BASE_URL",
            local_secrets.get("base_url", "https://api-gw.onebound.cn/1688"),
        ),
        onebound_1688_enabled=os.environ.get(
            "DAILY_SELECTION_ONEBOUND_ENABLED", str(local_secrets.get("enabled", True))
        ).strip().lower() in {"1", "true", "yes"},
    )


def _local_onebound_config() -> dict[str, str | bool]:
    """Read project-local credentials from the Git-ignored configuration file."""
    path = Path(__file__).with_name("onebound.local.json")
    if not path.exists():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid local OneBound configuration") from error
    if not isinstance(values, dict):
        raise RuntimeError("local OneBound configuration must be an object")
    return {
        key: value
        for key, value in values.items()
        if key in {"api_key", "api_secret", "base_url", "enabled"}
        and isinstance(value, (str, bool))
    }
