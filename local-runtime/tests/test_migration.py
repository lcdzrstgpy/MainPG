# -*- coding: utf-8 -*-
"""整机数据迁移：导出 → 导入暂存 → 下次启动替换 的往返与安全护栏。

这是「换新电脑」功能的核心不变量：
  - 迁移包里**不含**任何本机绑定的凭证（DPAPI 换机解不开）；
  - 素材文件导入时就地还原，数据库等下次启动、开库之前才替换；
  - 替换前必须先备份，坏包/非法包一律拒绝。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
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


# --------------------------------------------------------------------------- #
# 2026-10-19 补的护栏：令牌泄漏 / 内嵌库 / 版本 / WAL 备份 / 并发
# --------------------------------------------------------------------------- #
def test_export_strips_remote_tokens_that_escape_the_scrub_list(tmp_path: Path) -> None:
    """擦除名单是人工维护的，必须有一道「残留 wh_auth_ 令牌一律清空」的兜底。

    实测踩过：``product_processing_diagnostic_outbox.remote_token`` 藏着 2 个
    有效服务端令牌，而该表不在名单里 —— 包一旦给出去等于交出账号。
    """
    root = tmp_path / "wh-local"
    db = root / "workbench.sqlite3"
    _make_db(db, settings="keep")
    conn = sqlite3.connect(str(db))
    try:
        with conn:
            # 名单内的表：整表清空
            conn.execute("CREATE TABLE product_processing_diagnostic_outbox (id INTEGER, remote_token TEXT)")
            conn.execute("INSERT INTO product_processing_diagnostic_outbox VALUES (1, 'wh_auth_leaked')")
            # 名单外的表：靠兜底把 token 类列上的令牌清空
            conn.execute("CREATE TABLE misc_cache (session_token TEXT, access_token TEXT)")
            conn.execute("INSERT INTO misc_cache VALUES ('wh_auth_xyz', '94955d2014e74771a288f176abc')")
    finally:
        conn.close()

    archive = tmp_path / "p.mainpgbak"
    migration.export_archive(root, db, archive)

    with zipfile.ZipFile(archive) as zf:
        extracted = tmp_path / "x.sqlite3"
        extracted.write_bytes(zf.read("db/workbench.sqlite3"))
    conn = sqlite3.connect(str(extracted))
    try:
        assert conn.execute("SELECT COUNT(1) FROM product_processing_diagnostic_outbox").fetchone()[0] == 0
        token, asset_token = conn.execute("SELECT session_token, access_token FROM misc_cache").fetchone()
        assert token == "", "名单外的 wh_auth_ 令牌必须被兜底清空"
        assert asset_token.startswith("94955d"), "本地资产令牌不该被误伤"
        assert conn.execute("SELECT value_json FROM workbench_settings WHERE key='theme'").fetchone()[0] == "keep"
    finally:
        conn.close()


def test_export_skips_sqlite_sidecars_and_snapshots_embedded_db(tmp_path: Path) -> None:
    """ClipForge 的 sqlite.db 不能裸拷：-wal/-shm 不进包，主库走 backup() 快照。"""
    root = tmp_path / "wh-local"
    db = root / "workbench.sqlite3"
    _make_db(db, settings="x")

    embedded = root / "clipforge" / "data" / "sqlite.db"
    embedded.parent.mkdir(parents=True)
    writer = sqlite3.connect(str(embedded))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE clips(name TEXT)")
    writer.execute("INSERT INTO clips VALUES ('a.mp4')")
    writer.commit()
    assert (embedded.parent / "sqlite.db-wal").exists()

    themes = root / "themes" / "violet"
    themes.mkdir(parents=True)
    (themes / "theme.css").write_text("body{}", encoding="utf-8")

    archive = tmp_path / "p.mainpgbak"
    migration.export_archive(root, db, archive)
    writer.close()

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert "assets/clipforge/data/sqlite.db" in names
        assert not any(n.endswith("-wal") or n.endswith("-shm") for n in names), names
        assert any("themes/violet/theme.css" in n for n in names)
        snapshot = tmp_path / "embedded.db"
        snapshot.write_bytes(zf.read("assets/clipforge/data/sqlite.db"))

    conn = sqlite3.connect(str(snapshot))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT name FROM clips").fetchone()[0] == "a.mp4"
    finally:
        conn.close()


def test_manifest_carries_app_version_and_import_notices_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "wh-local"
    db = root / "workbench.sqlite3"
    _make_db(db, settings="x")
    archive = tmp_path / "p.mainpgbak"
    migration.export_archive(root, db, archive)

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest.get("app_version"), "manifest 必须写 app_version，否则版本校验永远空转"

    # 伪造一个「来自别的版本」的包，导入时应给出提示而不是静默通过
    tampered = tmp_path / "other.mainpgbak"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "manifest.json":
                manifest["app_version"] = "0.0.1-old"
                data = json.dumps(manifest).encode("utf-8")
            dst.writestr(item, data)

    new_root = tmp_path / "new"
    _make_db(new_root / "workbench.sqlite3", settings="new")
    staged = migration.stage_import(tampered, new_root)
    assert "0.0.1-old" in staged["version_notice"]


def test_apply_pending_backup_is_wal_safe(tmp_path: Path) -> None:
    """回滚备份必须包含 WAL 里已提交但未 checkpoint 的数据。

    模拟「异常退出留下的 -wal」：在连接仍打开时把 db + -wal 拷到另一个目录
    （主库文件里还没有那段数据，数据只在 WAL 里）。裸 copy2 会丢掉它。
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    live = src_dir / "workbench.sqlite3"
    writer = sqlite3.connect(str(live))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE t(x)")
    writer.commit()
    writer.execute("INSERT INTO t VALUES('只在WAL里的行')")
    writer.commit()

    data_dir = tmp_path / "wh-local"
    data_dir.mkdir()
    db = data_dir / "workbench.sqlite3"
    shutil.copy2(live, db)
    shutil.copy2(src_dir / "workbench.sqlite3-wal", data_dir / "workbench.sqlite3-wal")
    writer.close()

    pending = data_dir / migration.PENDING_DIR_NAME
    pending.mkdir()
    staged = pending / "workbench.sqlite3"
    _make_db(staged, settings="导入的库")

    result = migration.apply_pending(data_dir, db)
    assert result["applied"] is True

    backup = sqlite3.connect(result["backup"])
    try:
        rows = [r[0] for r in backup.execute("SELECT x FROM t").fetchall()]
    finally:
        backup.close()
    assert "只在WAL里的行" in rows, "备份丢了 WAL 里的已提交数据"
    assert _theme(db) == "导入的库"


def test_pending_info_surfaces_apply_error(tmp_path: Path) -> None:
    root = tmp_path / "wh-local"
    _make_db(root / "workbench.sqlite3", settings="x")
    pending = root / migration.PENDING_DIR_NAME
    pending.mkdir()
    (pending / "workbench.sqlite3").write_bytes(b"staged")
    migration.record_apply_error(root, RuntimeError("workbench.sqlite3-wal 被占用"))

    info = migration.pending_info(root)
    assert info is not None
    assert "被占用" in info["apply_error"]


def test_jobs_run_one_at_a_time(tmp_path: Path) -> None:
    jobs = migration.MigrationJobs(tmp_path, None)
    jobs._new("export")
    with pytest.raises(RuntimeError, match="已有迁移任务"):
        jobs._new("import")


def test_upload_rejects_oversized_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wh_local import migration_routes

    root = tmp_path / "wh-local"
    _make_db(root / "workbench.sqlite3", settings="x")
    monkeypatch.setattr(migration_routes, "MAX_UPLOAD_BYTES", 1024)

    class _Store:
        def get_session(self, token: str) -> object | None:
            return SimpleNamespace(token=token)

    app = FastAPI()
    app.include_router(
        migration_routes.create_migration_router(
            root, root / "workbench.sqlite3", SimpleNamespace(store=_Store())
        )
    )
    client = TestClient(app)
    response = client.post(
        "/api/migration/import",
        headers={"Authorization": "Bearer good"},
        files={"archive": ("big.mainpgbak", b"x" * 4096, "application/octet-stream")},
    )
    assert response.status_code == 413
    assert not list((root / migration.UPLOAD_DIR_NAME).glob("*.mainpgbak")), "超限上传必须清掉残留"


def test_import_rejects_a_valid_sqlite_that_is_not_the_workbench_db(tmp_path: Path) -> None:
    """integrity_check 只能证明「这是个好 SQLite」，不能证明「这是工作台的库」。

    外来库（哪怕来自别的软件）一旦被顶上，工作台会在错误 schema 上启动。
    """
    foreign_db = tmp_path / "foreign.sqlite3"
    conn = sqlite3.connect(str(foreign_db))
    conn.execute("CREATE TABLE something_else(id INTEGER)")
    conn.commit()
    conn.close()

    archive = tmp_path / "foreign.mainpgbak"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"format": migration.FORMAT, "schema_version": migration.SCHEMA_VERSION}),
        )
        zf.write(foreign_db, "db/workbench.sqlite3")

    root = tmp_path / "data"
    with pytest.raises(RuntimeError, match="不是工作台数据库"):
        migration.stage_import(archive, root)
    assert migration.pending_info(root) is None


def test_import_refuses_when_disk_is_too_small(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old"
    old_db = old_root / "workbench.sqlite3"
    _make_db(old_db, settings="x")
    assets = old_root / "product-processing-assets"
    assets.mkdir(parents=True)
    (assets / "big.png").write_bytes(b"x" * 5000)
    archive = tmp_path / "p.mainpgbak"
    migration.export_archive(old_root, old_db, archive)

    monkeypatch.setattr(
        migration.shutil,
        "disk_usage",
        lambda _p: type("U", (), {"total": 1, "used": 1, "free": 1024})(),
    )
    with pytest.raises(RuntimeError, match="磁盘空间不足"):
        migration.stage_import(archive, tmp_path / "new")


def test_apply_pending_keeps_only_the_latest_backups(tmp_path: Path) -> None:
    root = tmp_path / "wh-local"
    root.mkdir()
    db = root / "workbench.sqlite3"
    _make_db(db, settings="x")
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000",
                  "20260105-000000"):
        (root / f"workbench.sqlite3.pre-import-{stamp}.bak").write_bytes(b"old")

    pending = root / migration.PENDING_DIR_NAME
    pending.mkdir()
    staged = pending / "workbench.sqlite3"
    _make_db(staged, settings="imported")

    migration.apply_pending(root, db)
    remaining = sorted(p.name for p in root.glob("workbench.sqlite3.pre-import-*.bak"))
    # 5 份历史 + 本次 1 份 → 只留最近 3 份（20260104 / 20260105 / 本次）
    assert len(remaining) == 3, remaining
    assert remaining[0].endswith("20260104-000000.bak"), remaining


def test_import_blocks_a_package_from_a_newer_app(tmp_path: Path) -> None:
    """新包 → 旧程序必须挡住：旧程序只补列不降级。"""
    root = tmp_path / "wh-local"
    db = root / "workbench.sqlite3"
    _make_db(db, settings="x")
    archive = tmp_path / "p.mainpgbak"
    migration.export_archive(root, db, archive)

    newer = tmp_path / "newer.mainpgbak"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(newer, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["app_version"] = "99.0.0"
                data = json.dumps(manifest).encode("utf-8")
            dst.writestr(item, data)

    target = tmp_path / "new"
    with pytest.raises(RuntimeError, match="更高版本"):
        migration.stage_import(newer, target)
    assert migration.pending_info(target) is None


def test_import_rewrites_a_profit_save_root_that_does_not_exist_here(tmp_path: Path) -> None:
    """旧机自定义保存目录在新机盘符都不存在时，改指本机默认目录。

    否则整库替换会把这行设置原样带过来，产品库打开是空的 —— 用户会以为丢数据。
    """
    root = tmp_path / "old"
    db = root / "workbench.sqlite3"
    _make_db(db, settings="x")
    conn = sqlite3.connect(str(db))
    try:
        with conn:
            conn.execute(
                "CREATE TABLE profit_activity_settings (id INTEGER PRIMARY KEY, save_root TEXT)"
            )
            conn.execute(
                "INSERT INTO profit_activity_settings VALUES (1, ?)",
                ("Z:\\definitely-missing-drive\\profit_activity",),
            )
    finally:
        conn.close()

    archive = tmp_path / "p.mainpgbak"
    migration.export_archive(root, db, archive)

    new_root = tmp_path / "new"
    _make_db(new_root / "workbench.sqlite3", settings="new")
    staged = migration.stage_import(archive, new_root)
    assert staged["save_root_notice"], "必须告诉用户目录被改过"

    staged_db = sqlite3.connect(str(new_root / migration.PENDING_DIR_NAME / "workbench.sqlite3"))
    try:
        value = staged_db.execute(
            "SELECT save_root FROM profit_activity_settings WHERE id=1"
        ).fetchone()[0]
    finally:
        staged_db.close()
    assert value == str(new_root.parent / "profit_activity")
    assert "Z:" not in value


def test_replace_falls_back_to_move_across_devices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库被指到另一个盘时 os.replace 必失败（WinError 17），要退化成 move。"""
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")

    real_replace = migration.os.replace

    def _boom(_a: str, _b: str) -> None:
        error = OSError(0, "cross-device link")
        error.winerror = 17  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(migration.os, "replace", _boom)
    try:
        migration._replace_with_retry(src, dst)
    finally:
        monkeypatch.setattr(migration.os, "replace", real_replace)
    assert dst.read_bytes() == b"new"
    assert not src.exists()


def test_import_pauses_and_resumes_the_embedded_service(tmp_path: Path) -> None:
    """导入会覆盖 ClipForge 正在用的库，必须先停、导入后恢复。"""
    from types import SimpleNamespace

    root = tmp_path / "wh-local"
    db = root / "workbench.sqlite3"
    _make_db(db, settings="x")
    archive = tmp_path / "p.mainpgbak"
    migration.export_archive(root, db, archive)

    calls: list[str] = []

    class _Service:
        def status(self) -> object:
            return SimpleNamespace(state="ready")

        def stop(self) -> None:
            calls.append("stop")

        def start(self) -> None:
            calls.append("start")

    jobs = migration.MigrationJobs(root, db, _Service())
    job = jobs.start_import(archive)
    for _ in range(200):
        state = jobs.get(job["job_id"]) or {}
        if state.get("state") != "running":
            break
        time.sleep(0.05)
    assert calls == ["stop", "start"], calls
    assert (jobs.get(job["job_id"]) or {}).get("state") == "done"
