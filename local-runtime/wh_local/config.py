from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


# Release automation updates this single value when producing a desktop build.
APP_VERSION = "1.2.10"
# Replace this host only when the official MainPG release origin moves. Keep the
# manifest and installer allowlist bound to the same release-owned host.
UPDATE_RELEASE_HOST = "workbench.haocoming.top"
UPDATE_MANIFEST_URL = f"https://{UPDATE_RELEASE_HOST}/mainpg/windows/manifest.json"
# Incremental patch manifest: describes from_version -> to_version file diff.
# Signed with the same Ed25519 key; verified by PatchManager before download.
UPDATE_PATCH_MANIFEST_URL = f"https://{UPDATE_RELEASE_HOST}/mainpg/windows/patch-manifest.json"
UPDATE_MANIFEST_ALLOWED_HOSTS = frozenset({UPDATE_RELEASE_HOST})
# Public verification key only. The matching private key belongs in the release
# signing system and must never be distributed with the application.
# 2026-08-23 regenerated keypair; private key: C:\secure\mainpg-release-ed25519.pem
UPDATE_ED25519_PUBLIC_KEY_B64 = "qxQ5zE+euDRvWKgT+VcWeCoKcNrOxv6skEBVoCE1MIc="


@dataclass(frozen=True)
class LocalRuntimeConfig:
    app_version: str
    runtime_root: Path
    install_root: Path
    data_dir: Path
    database_path: Path
    dev_admin_token: str
    customer_auth_base_url: str
    onebound_1688_api_key: str
    onebound_1688_api_secret: str
    onebound_1688_base_url: str
    onebound_1688_enabled: bool


def default_config(workspace: Path | None = None) -> LocalRuntimeConfig:
    root = runtime_root(workspace)
    install_dir = install_root()
    local_secrets = _local_onebound_config()
    # Allow a dedicated data directory for development, tests, and packaged builds.
    data_dir = Path(
        os.environ.get("WH_LOCAL_DATA_DIR", "")
        or root / "outputs" / "wh-local"
    )
    database_path = Path(os.environ.get("WH_LOCAL_DATABASE_PATH", "") or data_dir / "workbench.sqlite3")
    return LocalRuntimeConfig(
        app_version=_resolved_app_version(install_dir),
        runtime_root=root,
        install_root=install_dir,
        data_dir=data_dir,
        database_path=database_path,
        dev_admin_token=os.environ.get("WH_LOCAL_DEV_ADMIN_TOKEN", "dev-admin-token"),
        customer_auth_base_url=os.environ.get(
            "WH_LOCAL_CUSTOMER_AUTH_BASE_URL",
            "https://workbench.haocoming.top/auth-api",
        ),
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


def runtime_root(workspace: Path | None = None) -> Path:
    """Data root: explicit workspace > packaged (%APPDATA%\MainPG) > current directory.

    For packaged builds the user double-clicks the exe, so cwd may be the install
    directory or a system directory (possibly read-only). Data goes to
    %APPDATA%\MainPG so drafts/generated images/exports always land on disk."""
    if workspace is not None:
        return workspace
    if getattr(sys, "frozen", False):
        appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return appdata / "MainPG"
    return Path.cwd()


def install_root() -> Path:
    """Installation directory that holds the executable (patch targets live here).

    Packaged builds put MainPG.exe / MainPG-Updater.exe / version.json next to
    the executable. Source runs use the current directory so updates can be
    exercised in development without a real install."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _resolved_app_version(install_dir: Path) -> str:
    """Read the on-disk version.json (written by MainPG-Updater after a patch).

    Falls back to the compiled-in APP_VERSION when the file is absent (fresh
    checkout / source run / not yet patched)."""
    try:
        data = json.loads((install_dir / "version.json").read_text(encoding="utf-8"))
        value = str(data.get("version") or "").strip()
        if value:
            return value
    except (OSError, ValueError):
        pass
    return APP_VERSION


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
    """onebound.local.json candidates: source dir + packaged resource dir (PyInstaller).

    The installer build places onebound.local.json next to the executable (onedir)
    or into the bundle resources (onefile _MEIPASS), so installed users can use
    the daily-selection collection API with zero configuration."""
    candidates = [Path(__file__).with_name("onebound.local.json")]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "onebound.local.json")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "onebound.local.json")
    return candidates

