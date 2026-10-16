"""工作台整机数据迁移：换新电脑时把旧机的全部业务数据打包带走，在新机一键还原。

为什么是「整库快照替换」而不是「逐表合并」：
    业务表普遍带 ``workspace_id``，而新机首次登录会自己建一套 workspace / 用户 /
    RBAC 行。若把旧机的数据行逐表 INSERT 过去，主键与 workspace 归属仍指向旧机，
    结果是「库里明明有数据、界面上一条都看不见」。因此这里把归档库整体顶上，
    所有主键、外键、workspace 归属原样保留；目标机本来就是刚装好的空工作台，
    语义上也不存在需要合并的两份数据。

为什么导入要等下次启动才生效：
    后端进程持有 SQLite 连接（连接池 + 后台任务），运行期热替换数据库文件会
    直接损坏数据。所以导入只做两件事：① 素材文件不占锁，当场就地还原；
    ② 归档数据库落到 ``<data_dir>/.migration-pending/``，由
    :func:`apply_pending` 在下次启动、开库之前替换（替换前自动备份当前库）。

导出即剥离敏感数据：
    :data:`SCRUBBED_TABLES` 里的表在打包前会被清空并 ``VACUUM``（否则被删的行
    仍可能残留在空闲页里被翻出来）。这些数据要么被 Windows DPAPI 绑死在本机
    用户上（换机必然解不开），要么属于账号凭证/机器指纹，用户已明确选择「不随
    迁移包走」——新机重新登录、重新配置即可。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger("wh_local.migration")

FORMAT = "mainpg-migration"
SCHEMA_VERSION = 1
ARCHIVE_SUFFIX = ".mainpgbak"

#: 导出时清空（并在之后 VACUUM）的表。见模块 docstring。
SCRUBBED_TABLES: tuple[str, ...] = (
    "secret_values",              # API Key / COS Secret 密文
    "customer_sessions",          # 本地会话 + DPAPI 远端令牌
    "customer_users",             # 账号镜像，新机登录时重建
    "auth_accounts",
    "auth_password_credentials",
    "auth_login_logs",
    "auth_platform_sessions",
    "auth_password_reset_tokens",
    "auth_security_events",
    "auth_email_verifications",
    "account_invitations",
    "invitation_codes",
    "invitation_code_usages",
    "license_state",              # 与本机 machine_fingerprint 绑定
    "license_activation_logs",
    "launcher_log_uploads",       # 诊断日志上传记录
)

PENDING_DIR_NAME = ".migration-pending"
EXPORT_DIR_NAME = ".migration-export"
UPLOAD_DIR_NAME = ".migration-upload"
_BACKUP_MARKER = ".pre-import-"

# 素材目录里这些内容要么可重建、要么不该跟着走（DPAPI 文件换机解不开）。
_SKIP_DIR_NAMES = {
    "__pycache__",
    "image-feature-cache",
    "logs",
    PENDING_DIR_NAME,
    EXPORT_DIR_NAME,
    UPLOAD_DIR_NAME,
}
_SKIP_FILE_NAMES = {"login-credentials.bin", ".write-test", "clipforge.pid"}
_SKIP_SUFFIXES = (".log", ".pyc", ".tmp")
# 已经压过一遍的格式：再 DEFLATE 一遍纯属浪费 CPU 和时间。
_STORED_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic", ".tif", ".tiff",
    ".zip", ".gz", ".7z", ".rar", ".xz", ".mp4", ".mov", ".mkv", ".webm", ".mp3", ".m4a",
}

Progress = Callable[[str, int, int], None]


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@contextmanager
def _connect_ro(db_path: Path) -> Iterator[sqlite3.Connection]:
    """只读连接上下文。

    ⚠️ 必须自己 close：``with sqlite3.connect(...)`` 只提交/回滚事务、**不关连接**，
    句柄会一直留到 GC。Windows 上留一个句柄就足以让 ``os.replace`` 报
    WinError 32（文件被占用），导入替换时直接失败。
    uri 形式则避免路径里的特殊字符被解析成连接串参数。
    """
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _skip_file(name: str) -> bool:
    if name in _SKIP_FILE_NAMES:
        return True
    if _BACKUP_MARKER in name and name.endswith(".bak"):
        return True
    lower = name.lower()
    if lower.endswith(_SKIP_SUFFIXES):
        return True
    # 主库 / 附属库的 WAL 附属文件与就地备份
    if name.startswith("combo_kit.sqlite3"):
        return True
    return False


def _walk_files(root: Path) -> Iterator[tuple[str, Path, int]]:
    """产出 (相对 root 的 POSIX 路径, 绝对路径, 字节数)，已按跳过规则过滤。

    用 ``os.scandir`` 而不是 ``os.walk`` + ``stat()``：Windows 上 scandir 返回的
    ``DirEntry`` 自带目录枚举时拿到的属性，``entry.stat()`` 不再多打一次系统调用。
    素材目录动辄几万个文件，这一项差异就是十几秒和一两秒的区别。
    """
    if not root.is_dir():
        return
    stack: list[tuple[str, str]] = [(str(root), "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in _SKIP_DIR_NAMES:
                        stack.append((entry.path, f"{prefix}{entry.name}/"))
                    continue
                if not entry.is_file(follow_symlinks=False) or _skip_file(entry.name):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            yield f"{prefix}{entry.name}", Path(entry.path), size


def _dir_stats(root: Path) -> tuple[int, int]:
    files = total = 0
    for _rel, _path, size in _walk_files(root):
        files += 1
        total += size
    return files, total


# --------------------------------------------------------------------------- #
# 迁移范围
# --------------------------------------------------------------------------- #
def _profit_activity_roots(data_dir: Path, db_path: Path | None) -> list[Path]:
    """产品库/利润活动资产根：默认 data_dir 同级的 profit_activity，再追加用户自定义 save_root。

    自定义 save_root 是用户显式配的绝对路径，随数据库一起迁移：新机的
    ``profit_activity_settings.save_root`` 仍指向同一路径，因此这里把素材还原到
    原路径即可保持一致（路径建不出来就逐组报错，不静默丢数据）。
    """
    roots = [data_dir.parent / "profit_activity"]
    if db_path is not None and db_path.is_file():
        try:
            with _connect_ro(db_path) as conn:
                row = conn.execute(
                    "SELECT save_root FROM profit_activity_settings WHERE id=1"
                ).fetchone()
        except sqlite3.Error:
            row = None
        if row and row[0]:
            extra = Path(str(row[0]).strip()).expanduser()
            if str(extra) and extra not in roots:
                roots.append(extra)
    return roots


def asset_groups(data_dir: Path, db_path: Path | None) -> list[tuple[str, Path]]:
    """(分组 id, 目录) 列表。导出时是旧机路径，导入时按**当前机器**的数据根解析，
    因此同一份列表在两端各算一次就自动指向各机的正确位置。"""
    groups: list[tuple[str, Path]] = [
        ("product-processing-assets", data_dir / "product-processing-assets"),
        ("pod-customization-assets", data_dir / "pod-customization-assets"),
        ("ai-service", data_dir / "ai-service"),
        ("price-verification", data_dir / "price-verification"),
        ("combo-kit-assets", data_dir / "combo-kit" / "assets"),
        ("clipforge", data_dir / "clipforge"),
    ]
    profit_roots = _profit_activity_roots(data_dir, db_path)
    if profit_roots:
        groups.append(("profit-activity", profit_roots[0]))
    for index, extra in enumerate(profit_roots[1:], start=2):
        groups.append((f"profit-activity-custom{index}", extra))
    return groups


def _main_db_tables(db_path: Path) -> list[str]:
    with _connect_ro(db_path) as conn:
        return [t for t in _table_names(conn) if t not in SCRUBBED_TABLES]


def collect_plan(data_dir: Path, db_path: Path | None) -> dict[str, Any]:
    """迁移包内容清单：界面用它告诉用户「这一包会带走什么、多大」。"""
    included: list[str] = []
    scrubbed_present: list[str] = []
    table_count = row_count = 0
    db_bytes = 0
    combo_db = data_dir / "combo-kit" / "combo_kit.sqlite3"

    if db_path is not None and db_path.is_file():
        db_bytes += db_path.stat().st_size
        try:
            with _connect_ro(db_path) as conn:
                all_tables = _table_names(conn)
                included = [t for t in all_tables if t not in SCRUBBED_TABLES]
                scrubbed_present = [t for t in all_tables if t in SCRUBBED_TABLES]
                table_count = len(included)
                for table in included:
                    try:
                        row = conn.execute(f'SELECT COUNT(1) FROM "{table}"').fetchone()
                    except sqlite3.Error:
                        continue
                    row_count += int(row[0]) if row else 0
        except sqlite3.Error as exc:  # pragma: no cover - 库损坏属于另一类故障
            logger.warning("迁移清单读取数据库失败：%s", exc)
    if combo_db.is_file():
        db_bytes += combo_db.stat().st_size

    assets: list[dict[str, Any]] = []
    asset_files = asset_bytes = 0
    for group, root in asset_groups(data_dir, db_path):
        files, size = _dir_stats(root)
        if files == 0 and not root.is_dir():
            continue
        assets.append({"group": group, "path": str(root), "files": files, "bytes": size})
        asset_files += files
        asset_bytes += size

    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "data_dir": str(data_dir),
        "database": {
            "path": str(db_path) if db_path else "",
            "bytes": db_bytes,
            "tables": table_count,
            "rows": row_count,
            "includes": included,
            "scrubbed": scrubbed_present,
        },
        "assets": assets,
        "asset_files": asset_files,
        "asset_bytes": asset_bytes,
        "total_bytes": db_bytes + asset_bytes,
        "total_files": asset_files,
        "custom_profit_root": len(_profit_activity_roots(data_dir, db_path)) > 1,
    }


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
def _replace_with_retry(src: Path, dst: Path, attempts: int = 5) -> None:
    """替换文件，遇到 Windows 瞬时占用（杀软/索引器刚扫过）时退避重试。

    ``os.replace`` 在 Windows 上是原子的，但只要有别的进程恰好打开着目标/来源文件
    就会报 WinError 32。这种占用通常几十毫秒就释放，因此短重试比直接失败合理。
    """
    last: OSError | None = None
    for index in range(attempts):
        try:
            os.replace(str(src), str(dst))
            return
        except OSError as exc:
            last = exc
            time.sleep(0.2 * (index + 1))
    raise RuntimeError(f"无法替换数据文件（{dst}）：{last}") from last


def _snapshot_db(src: Path, dest: Path) -> None:
    """用 SQLite 在线备份 API 取一致性快照（WAL 下也安全，且不锁写）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    target = sqlite3.connect(str(dest))
    try:
        with _connect_ro(src) as source:
            source.backup(target)
    finally:
        target.close()


def _scrub_db(db_path: Path) -> list[str]:
    """清空敏感表并 VACUUM：DELETE 只是把行移入空闲页，VACUUM 才真正抹掉残留。"""
    scrubbed: list[str] = []
    conn = sqlite3.connect(str(db_path))
    try:
        with conn:
            for table in SCRUBBED_TABLES:
                if table not in _table_names(conn):
                    continue
                conn.execute(f'DELETE FROM "{table}"')
                scrubbed.append(table)
        conn.execute("VACUUM")
    finally:
        conn.close()
    return scrubbed


def export_archive(
    data_dir: Path,
    db_path: Path | None,
    out_path: Path,
    *,
    on_progress: Progress | None = None,
) -> dict[str, Any]:
    """把整机业务数据打包成迁移包（zip）。

    结构：``manifest.json`` + ``db/workbench.sqlite3`` + ``db/combo_kit.sqlite3``
    + ``assets/<分组>/…``。返回结果里带 manifest，异常向上抛由调用方记录。
    """

    def report(phase: str, done: int, total: int) -> None:
        if on_progress is not None:
            on_progress(phase, done, total)

    if db_path is None or not db_path.is_file():
        raise RuntimeError("找不到工作台数据库，无法导出")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    combo_src = data_dir / "combo-kit" / "combo_kit.sqlite3"

    staged: list[tuple[str, Path]] = []  # (归档内路径, 临时/源文件)
    temp_dir = Path(tempfile.mkdtemp(prefix="mainpg-mig-"))
    try:
        report("snapshot", 0, 1)
        main_copy = temp_dir / "workbench.sqlite3"
        _snapshot_db(db_path, main_copy)
        scrubbed = _scrub_db(main_copy)
        staged.append(("db/workbench.sqlite3", main_copy))

        if combo_src.is_file():
            combo_copy = temp_dir / "combo_kit.sqlite3"
            _snapshot_db(combo_src, combo_copy)
            staged.append(("db/combo_kit.sqlite3", combo_copy))
        report("snapshot", 1, 1)

        asset_entries: list[tuple[str, Path]] = []
        for group, root in asset_groups(data_dir, db_path):
            for rel, path, _size in _walk_files(root):
                asset_entries.append((f"assets/{group}/{rel}", path))

        db_tables = len(_main_db_tables(db_path))

        total_assets = len(asset_entries)
        manifest: dict[str, Any] = {
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            "created_at": _now(),
            "source_data_dir": str(data_dir),
            "include_tables": db_tables,
            "asset_files": total_assets,
            "scrubbed_tables": scrubbed,
        }

        report("pack", 0, total_assets + 1)
        written = 0
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, path in staged:
                zf.write(path, arcname)
            for arcname, path in asset_entries:
                compress = (
                    zipfile.ZIP_STORED
                    if path.suffix.lower() in _STORED_SUFFIXES
                    else zipfile.ZIP_DEFLATED
                )
                try:
                    zf.write(path, arcname, compress_type=compress)
                except OSError as exc:
                    logger.warning("迁移包跳过无法读取的文件 %s：%s", path, exc)
                    continue
                written += 1
                if written % 50 == 0:
                    report("pack", written, total_assets + 1)
            manifest["packed_asset_files"] = written
            zf.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
        report("pack", total_assets + 1, total_assets + 1)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    size = out_path.stat().st_size if out_path.is_file() else 0
    return {
        "status": "ok",
        "path": str(out_path),
        "bytes": size,
        "asset_files": written,
        "manifest": manifest,
    }


# --------------------------------------------------------------------------- #
# 导入（暂存 + 下次启动应用）
# --------------------------------------------------------------------------- #
def _safe_arcname(name: str) -> bool:
    """归档条目必须落在白名单前缀内、且不含 .. —— 防 zip-slip 逃逸。"""
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    if ".." in Path(name.replace("\\", "/")).parts:
        return False
    return name == "manifest.json" or name.startswith(("db/", "assets/"))


def _verify_sqlite(path: Path) -> None:
    """确认落下来的确实是可读的 SQLite 库，避免把半个文件顶到线上。"""
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise RuntimeError("迁移包里的数据库校验未通过（integrity_check）")
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"迁移包里的数据库无法读取：{exc}") from exc
    finally:
        conn.close()


def stage_import(
    archive_path: Path,
    data_dir: Path,
    *,
    on_progress: Progress | None = None,
) -> dict[str, Any]:
    """校验迁移包：素材就地还原，数据库落到 .migration-pending 等下次启动替换。"""

    def report(phase: str, done: int, total: int) -> None:
        if on_progress is not None:
            on_progress(phase, done, total)

    if not archive_path.is_file():
        raise RuntimeError("迁移包不存在")

    pending_dir = data_dir / PENDING_DIR_NAME
    if pending_dir.exists():
        # 上一次导入还没重启生效，不能叠第二份（否则不知道最终该用哪份）。
        raise RuntimeError("上一次导入还没重启生效，请先重启工作台再导入新的迁移包")

    with zipfile.ZipFile(archive_path, "r") as zf:
        if "manifest.json" not in zf.namelist():
            raise RuntimeError("这不像是工作台迁移包（缺少 manifest.json）")
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("迁移包的 manifest.json 无法解析") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise RuntimeError("迁移包格式不正确")
        if int(manifest.get("schema_version") or 0) != SCHEMA_VERSION:
            raise RuntimeError("迁移包版本与本机程序不一致，请把两台机器的程序更新到同一版本后再试")

        names = [n for n in zf.namelist() if not n.endswith("/")]
        for name in names:
            if not _safe_arcname(name):
                raise RuntimeError(f"迁移包内含非法路径条目，已中止导入：{name}")

        asset_entries = [n for n in names if n.startswith("assets/")]
        total = len(asset_entries) + 2

        # 1) 数据库先落到暂存区（下次启动、开库之前替换）
        pending_dir.mkdir(parents=True, exist_ok=True)
        report("extract", 0, total)
        staged_db = False
        for arcname, target_name in (
            ("db/workbench.sqlite3", "workbench.sqlite3"),
            ("db/combo_kit.sqlite3", "combo_kit.sqlite3"),
        ):
            if arcname not in names:
                continue
            target = pending_dir / target_name
            with zf.open(arcname) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            _verify_sqlite(target)
            if target_name == "workbench.sqlite3":
                staged_db = True
        if not staged_db:
            shutil.rmtree(pending_dir, ignore_errors=True)
            raise RuntimeError("迁移包里没有工作台数据库，无法恢复")
        report("extract", 1, total)

        # 2) 素材直接就地还原（纯文件覆盖，不涉及数据库锁）。
        #    目录清单要用**刚暂存下来的旧机数据库**解析：产品库的自定义 save_root
        #    存在那一行设置里，而当前库（新机空库）里还没有这条记录。
        groups = dict(asset_groups(data_dir, pending_dir / "workbench.sqlite3"))
        restored = 0
        errors: dict[str, str] = {}
        for index, name in enumerate(asset_entries, start=1):
            parts = name.split("/")
            if len(parts) < 3:
                errors[name] = "条目结构不对"
                continue
            group, relative = parts[1], "/".join(parts[2:])
            root = groups.get(group)
            if root is None:
                errors[name] = "本机没有对应的素材目录，已跳过"
                continue
            target = (root / relative).resolve()
            base = root.resolve()
            if target != base and base not in target.parents:
                errors[name] = "目标路径越界，已跳过"
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                restored += 1
            except OSError as exc:
                errors[name] = str(exc)
            if index % 50 == 0:
                report("extract", index + 1, total)
        report("extract", total, total)

    (pending_dir / "manifest.json").write_text(
        json.dumps(
            {
                "staged_at": _now(),
                "source_app_version": str(manifest.get("app_version") or ""),
                "source_created_at": str(manifest.get("created_at") or ""),
                "asset_files": restored,
                "asset_errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "asset_files": restored,
        "asset_errors": len(errors),
        "restart_required": True,
    }


def pending_info(data_dir: Path) -> dict[str, Any] | None:
    """读取「已导入、等下次启动生效」的暂存信息；没有则返回 None。"""
    pending_dir = data_dir / PENDING_DIR_NAME
    if not (pending_dir / "workbench.sqlite3").is_file():
        return None
    info: dict[str, Any] = {"pending": True}
    try:
        info.update(json.loads((pending_dir / "manifest.json").read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return info


def apply_pending(data_dir: Path, db_path: Path) -> dict[str, Any]:
    """把暂存的迁移包数据库顶上（导入后的首次启动调用）。

    必须在任何连接打开数据库**之前**执行。替换前把当前库备份为
    ``<库名>.pre-import-<时间戳>.bak``，误操作可人工回滚。
    """
    pending_dir = data_dir / PENDING_DIR_NAME
    staged_main = pending_dir / "workbench.sqlite3"
    if not staged_main.is_file():
        return {"applied": False}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = ""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.is_file():
        backup_path = db_path.with_name(f"{db_path.name}{_BACKUP_MARKER}{stamp}.bak")
        shutil.copy2(db_path, backup_path)
        backup = str(backup_path)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    _replace_with_retry(staged_main, db_path)

    combo_target = data_dir / "combo-kit" / "combo_kit.sqlite3"
    staged_combo = pending_dir / "combo_kit.sqlite3"
    if staged_combo.is_file():
        combo_target.parent.mkdir(parents=True, exist_ok=True)
        if combo_target.is_file():
            shutil.copy2(
                combo_target,
                combo_target.with_name(f"{combo_target.name}{_BACKUP_MARKER}{stamp}.bak"),
            )
        for suffix in ("-wal", "-shm"):
            Path(str(combo_target) + suffix).unlink(missing_ok=True)
        _replace_with_retry(staged_combo, combo_target)

    shutil.rmtree(pending_dir, ignore_errors=True)
    logger.warning("已应用导入的数据迁移包：database=%s backup=%s", db_path, backup or "-")
    return {"applied": True, "backup": backup}


# --------------------------------------------------------------------------- #
# 后台任务（导出体积可达 GB 级，不能用同步请求扛）
# --------------------------------------------------------------------------- #
class MigrationJobs:
    """极简后台任务表：一个线程跑一件迁移任务，前端轮询进度。"""

    def __init__(self, data_dir: Path, db_path: Path | None):
        self.data_dir = data_dir
        self.db_path = db_path
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _new(self, kind: str) -> dict[str, Any]:
        job = {
            "job_id": uuid.uuid4().hex,
            "kind": kind,
            "state": "running",
            "phase": "准备中",
            "done": 0,
            "total": 0,
            "message": "",
            "result": None,
            "created_at": _now(),
        }
        with self._lock:
            self._jobs[job["job_id"]] = job
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _progress(self, job: dict[str, Any]) -> Progress:
        def report(phase: str, done: int, total: int) -> None:
            with self._lock:
                job["phase"] = phase
                job["done"] = done
                job["total"] = total

        return report

    def start_export(self) -> dict[str, Any]:
        job = self._new("export")
        export_dir = self.data_dir / EXPORT_DIR_NAME
        out_path = export_dir / f"MainPG-migration-{time.strftime('%Y%m%d-%H%M%S')}{ARCHIVE_SUFFIX}"

        def run() -> None:
            try:
                # 上一轮的产物先清掉，避免导出目录无限膨胀。
                shutil.rmtree(export_dir, ignore_errors=True)
                result = export_archive(
                    self.data_dir, self.db_path, out_path, on_progress=self._progress(job)
                )
                self._finish(job, "done", "", result)
            except Exception as exc:  # noqa: BLE001 - 后台任务，失败要落回 job 让前端能看到
                logger.exception("导出迁移包失败")
                self._finish(job, "failed", str(exc), None)

        threading.Thread(target=run, name="migration-export", daemon=True).start()
        return dict(job)

    def start_import(self, archive_path: Path) -> dict[str, Any]:
        job = self._new("import")

        def run() -> None:
            try:
                result = stage_import(
                    archive_path, self.data_dir, on_progress=self._progress(job)
                )
                self._finish(job, "done", "", result)
            except Exception as exc:  # noqa: BLE001
                logger.exception("导入迁移包失败")
                self._finish(job, "failed", str(exc), None)
            finally:
                try:
                    archive_path.unlink(missing_ok=True)
                except OSError:
                    pass

        threading.Thread(target=run, name="migration-import", daemon=True).start()
        return dict(job)

    def _finish(
        self, job: dict[str, Any], state: str, message: str, result: dict[str, Any] | None
    ) -> None:
        with self._lock:
            job["state"] = state
            job["message"] = message
            job["result"] = result
            if state == "done":
                job["phase"] = "已完成"
                job["done"] = job["total"]
