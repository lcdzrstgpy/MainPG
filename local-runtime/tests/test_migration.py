# -*- coding: utf-8 -*-
"""整机数据迁移：导出 → 导入暂存 → 下次启动替换 的往返与安全护栏。

这是「换新电脑」功能的核心不变量：
  - 迁移包里**不含**任何本机绑定的凭证（DPAPI 换机解不开）；
  - 素材文件导入时就地还原，数据库等下次启动、开库之前才替换；
  - 替换前必须先备份，坏包/非法包一律拒绝。
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from wh_local import migration


def _make_db(path: Path, *, settings: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        with conn:
            conn.execute(
                "CREATE TABLE workbench_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE customer_sessions (token_hash TEXT PRIMARY KEY, remote_token TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "CREATE TABLE secret_values (scope TEXT NOT NULL, name TEXT NOT NULL, "
                "ciphertext TEXT NOT NULL, PRIMARY KEY (scope, name))"
            )
            conn.execute("INSERT INTO workbench_settings VALUES ('theme', ?)", (settings,))
            conn.execute("INSERT INTO customer_sessions VALUES ('hash-1', 'dpapi:v1:xxx')")
            conn.execute("INSERT INTO secret_values VALUES ('cos', 'secret', 'cipher')")
    finally:
        conn.close()


def _theme(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        return str(
            conn.execute("SELECT value_json FROM workbench_settings WHERE key='theme'").fetchone()[0]
        )
    finally:
        conn.close()


def test_export_scrubs_credentials_and_import_round_trips(tmp_path: Path) -> None:
    old_root = tmp_path / "old" / "outputs" / "wh-local"
    old_db = old_root / "workbench.sqlite3"
    _make_db(old_db, settings="from-old-machine")
    drafts = old_root / "product-processing-assets" / "draft-images"
    drafts.mkdir(parents=True)
    (drafts / "a.png").write_bytes(b"PNG")
    outputs = old_root / "product-processing-assets" / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "task.xlsx").write_bytes(b"XLSX")
    # 以下三项都不该进迁移包：运行日志、可重建缓存、DPAPI 凭证文件。
    (outputs / "debug.log").write_text("noise", encoding="utf-8")
    (old_root / "price-verification" / "image-feature-cache").mkdir(parents=True)
    (old_root / "price-verification" / "image-feature-cache" / "c.bin").write_bytes(b"cache")
    (old_root / "login-credentials.bin").write_bytes(b"dpapi")

    archive = tmp_path / "pack.mainpgbak"
    result = migration.export_archive(old_root, old_db, archive)
    assert result["status"] == "ok"
    assert archive.is_file()

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "db/workbench.sqlite3" in names
        assert any(name.endswith("draft-images/a.png") for name in names)
        assert any(name.endswith("outputs/task.xlsx") for name in names)
        assert not any(name.endswith("debug.log") for name in names)
        assert not any("image-feature-cache" in name for name in names)
        assert not any("login-credentials.bin" in name for name in names)
        extracted = tmp_path / "inspect.sqlite3"
        extracted.write_bytes(zf.read("db/workbench.sqlite3"))

    # 敏感表在包里必须是空的（并被 VACUUM 真正抹掉），业务设置照常保留。
    conn = sqlite3.connect(str(extracted))
    try:
        assert conn.execute("SELECT COUNT(1) FROM customer_sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(1) FROM secret_values").fetchone()[0] == 0
        assert (
            conn.execute("SELECT value_json FROM workbench_settings WHERE key='theme'").fetchone()[0]
            == "from-old-machine"
        )
    finally:
        conn.close()

    # 新机：自带一套（空）数据的全新工作台。
    new_root = tmp_path / "new" / "outputs" / "wh-local"
    new_db = new_root / "workbench.sqlite3"
    _make_db(new_db, settings="from-new-machine")

    staged = migration.stage_import(archive, new_root)
    assert staged["restart_required"] is True
    assert staged["asset_files"] == 2
    assert (new_root / "product-processing-assets" / "draft-images" / "a.png").read_bytes() == b"PNG"
    assert migration.pending_info(new_root) is not None
    # 重启之前，新机自己的数据库一点都不该动。
    assert _theme(new_db) == "from-new-machine"

    applied = migration.apply_pending(new_root, new_db)
    assert applied["applied"] is True
    assert Path(applied["backup"]).is_file()
    assert migration.pending_info(new_root) is None
    assert _theme(new_db) == "from-old-machine"


def test_plan_reports_tables_and_asset_bytes(tmp_path: Path) -> None:
    root = tmp_path / "outputs" / "wh-local"
    db_path = root / "workbench.sqlite3"
    _make_db(db_path, settings="x")
    assets = root / "pod-customization-assets"
    assets.mkdir(parents=True)
    (assets / "shot.png").write_bytes(b"12345")

    plan = migration.collect_plan(root, db_path)
    assert plan["database"]["tables"] == 1  # 只有 workbench_settings 参与迁移
    assert plan["database"]["rows"] == 1
    assert "customer_sessions" in plan["database"]["scrubbed"]
    assert plan["asset_files"] == 1
    assert plan["total_bytes"] >= 5


def test_import_rejects_path_traversal_and_wrong_format(tmp_path: Path) -> None:
    root = tmp_path / "data"

    evil = tmp_path / "evil.mainpgbak"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"format": migration.FORMAT, "schema_version": migration.SCHEMA_VERSION}),
        )
        zf.writestr("db/workbench.sqlite3", b"not-a-db")
        zf.writestr("assets/../../evil.png", b"x")
    with pytest.raises(RuntimeError, match="非法路径"):
        migration.stage_import(evil, root)
    assert migration.pending_info(root) is None

    foreign = tmp_path / "foreign.mainpgbak"
    with zipfile.ZipFile(foreign, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "something-else", "schema_version": 1}))
    with pytest.raises(RuntimeError, match="格式不正确"):
        migration.stage_import(foreign, root)


def test_import_refuses_to_stack_on_a_pending_package(tmp_path: Path) -> None:
    old_root = tmp_path / "old" / "outputs" / "wh-local"
    old_db = old_root / "workbench.sqlite3"
    _make_db(old_db, settings="a")
    archive = tmp_path / "pack.mainpgbak"
    migration.export_archive(old_root, old_db, archive)

    new_root = tmp_path / "new" / "outputs" / "wh-local"
    _make_db(new_root / "workbench.sqlite3", settings="b")
    migration.stage_import(archive, new_root)

    with pytest.raises(RuntimeError, match="还没重启生效"):
        migration.stage_import(archive, new_root)


def test_apply_pending_is_a_noop_without_a_package(tmp_path: Path) -> None:
    root = tmp_path / "outputs" / "wh-local"
    db_path = root / "workbench.sqlite3"
    _make_db(db_path, settings="untouched")

    assert migration.apply_pending(root, db_path) == {"applied": False}
    assert _theme(db_path) == "untouched"


def test_router_needs_a_session_but_download_uses_the_job_id(tmp_path: Path) -> None:
    """接口护栏：状态/导出要登录会话；下载要能被浏览器原生拉取（带不了 Authorization）。

    大文件必须走磁盘流，前端 fetch+blob 会把几 GB 读进内存，所以下载端点以
    不可猜的 job_id 作为能力凭据 —— 这条断言就是把这个约定钉死。
    """
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local.migration_routes import create_migration_router

    root = tmp_path / "outputs" / "wh-local"
    db_path = root / "workbench.sqlite3"
    _make_db(db_path, settings="x")

    class _Store:
        def get_session(self, token: str) -> object | None:
            return SimpleNamespace(token=token) if token == "good" else None

    app = FastAPI()
    app.include_router(
        create_migration_router(root, db_path, SimpleNamespace(store=_Store()))
    )
    client = TestClient(app)

    assert client.get("/api/migration/plan").status_code == 401

    headers = {"Authorization": "Bearer good"}
    plan = client.get("/api/migration/plan", headers=headers)
    assert plan.status_code == 200
    assert plan.json()["database"]["tables"] == 1

    started = client.post("/api/migration/export", headers=headers)
    assert started.status_code == 200
    job_id = started.json()["job_id"]

    status: dict = {}
    for _ in range(200):
        status = client.get(f"/api/migration/export/{job_id}", headers=headers).json()
        if status["state"] != "running":
            break
    assert status.get("state") == "done", status

    download = client.get(f"/api/migration/export/{job_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert len(download.content) > 0
