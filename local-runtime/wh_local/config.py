from __future__ import annotations

import os
import json
import sys
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
    root = _runtime_root(workspace)
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


def _runtime_root(workspace: Path | None) -> Path:
    """数据根目录：显式指定 > 打包（PyInstaller）用户可写目录 > 当前工作目录。

    安装包场景下用户双击 exe，cwd 可能是安装目录或系统目录（可能不可写），
    此时把数据写入 %APPDATA%\\MainPG，保证草稿库/生成图/导出表能正常落盘。
    """
    if workspace is not None:
        return workspace
    if getattr(sys, "frozen", False):
        appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return appdata / "MainPG"
    return Path.cwd()


def _local_onebound_config() -> dict[str, str | bool]:
    """Read project-local credentials from the Git-ignored configuration file."""
    for path in _local_onebound_config_paths():
        if not path.is_file():
            continue
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
    return {}


def _local_onebound_config_paths() -> list[Path]:
    """onebound.local.json 候选位置：源码目录 + 打包资源目录（PyInstaller）。

    安装包构建时把 onebound.local.json 放进可执行文件同目录（onedir）或打包资源
    （onefile 的 _MEIPASS），用户安装后零配置即可使用每日选品采集 API（运营方写死凭据）。
    """
    candidates = [Path(__file__).with_name("onebound.local.json")]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "onebound.local.json")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "onebound.local.json")
    return candidates
