from __future__ import annotations

from pathlib import Path

from run_workbench import _workbench_url


def test_workbench_url_is_versioned_and_encodes_version() -> None:
    url = _workbench_url("127.0.0.1", 8010, "1.3.5-beta 1")
    # 版本号必须 URL 编码；后面再跟一次性 nonce —— 否则浏览器遇到同 URL 的旧标签
    # 只切前台不重载，「叉掉浏览器再点启动」会看到冻结的旧页面（2026-10-19）。
    assert url.startswith("http://127.0.0.1:8010/?app_version=1.3.5-beta%201&t=")


def test_frontend_bootstrap_recovers_one_time_from_missing_hashed_assets() -> None:
    index = Path(__file__).resolve().parents[2] / "web-frontend" / "index.html"
    source = index.read_text(encoding="utf-8")

    assert 'const recoveryKey = "mainpg_asset_recovery_v1"' in source
    assert 'assetPathPrefix = "/assets/"' in source
    assert 'window.addEventListener("error"' in source
    assert 'window.location.replace(recoveryUrl.toString())' in source
    assert 'window.sessionStorage.getItem(recoveryKey) === "retrying"' in source
    assert 'window.sessionStorage.removeItem(recoveryKey)' in source
