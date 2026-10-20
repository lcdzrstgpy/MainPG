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

    擦表之外还有一道兜底 :func:`_strip_remote_tokens`：任何**未擦除**的表里，
    只要 token 类列上残留 ``wh_auth_`` 服务端会话令牌，一律清空。实测
    ``product_processing_diagnostic_outbox.remote_token`` 就藏过 2 个有效令牌
    —— 名单是人工维护的，迟早漏；令牌是能力凭据，漏一次等于把账号交出去。

内嵌数据库（ClipForge 的 ``clipforge/data/sqlite.db``）走和主库一样的
``backup()`` 快照，且**不打包 ``-wal`` / ``-shm`` / ``-journal``**：它们是
某一瞬间的附属文件，和主库文件不是同一刻的快照，还原后 SQLite 会回放错乱。
"""
from __future__ import annotations

import errno
import json
import logging
import os
import re
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
    # 下面几张是「明文存着服务端/插件令牌」的表，换机后既无用又危险：
    # diagnostic_outbox.remote_token 实测存的是有效 wh_auth_ 会话令牌（7 天），
    # 插件会话令牌同样是本机配对产物，新机必须重新配对。
    "product_processing_diagnostic_outbox",
    "data_collection_plugin_sessions",
    "price_verification_plugin_sessions",
)

#: 令牌列的兜底扫描：这些列名里的 ``wh_auth_`` 值在包里一律清空。
_TOKEN_COLUMN_HINT = "token"
_REMOTE_TOKEN_PREFIX = "wh_auth_"

PENDING_DIR_NAME = ".migration-pending"
EXPORT_DIR_NAME = ".migration-export"
UPLOAD_DIR_NAME = ".migration-upload"
#: 上次启动替换失败的原因落盘位置（给界面看，别只写日志）。
APPLY_ERROR_NAME = "apply-error.json"
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
#: SQLite 附属文件：绝不单独打包（见 _skip_file）。
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
#: 内嵌数据库的候选扩展名。先按扩展名过滤再读文件头，避免为几万个 jpg 各开一次句柄。
_SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db3")
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


def _app_version() -> str:
    """当前程序版本。延迟 import：config.py 会在函数内反向 import 本模块。"""
    try:
        from .config import APP_VERSION

        return str(APP_VERSION)
    except Exception:  # noqa: BLE001 - 版本只用于提示，取不到不该阻断导出
        return ""


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
    # SQLite 的附属文件一律不打包：``-wal`` / ``-shm`` / ``-journal`` 是某一瞬间
    # 的状态，和主库文件不是同一刻的快照；``-shm`` 更是进程间共享内存，拷过去
    # 只会让目标机的 SQLite 回放错乱。内嵌库（如 ClipForge 的 sqlite.db）在
    # export_archive 里走 backup() 另行快照，不靠文件拷贝。
    if lower.endswith(_SQLITE_SIDECAR_SUFFIXES):
        return True
    return False


def _is_sqlite_file(path: Path) -> bool:
    """按文件头判断是不是 SQLite 库（不信扩展名：内嵌库可能叫 .db / .sqlite）。"""
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
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
        except OSError as exc:
            # 静默跳过 = 用户以为「全带走了」其实少了整棵目录。至少要留痕。
            logger.warning("迁移导出跳过无法读取的目录 %s：%s", directory, exc)
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


def _version_key(version: str) -> tuple[int, ...]:
    """把 "1.5.6" / "1.5.6-beta2" 变成可比较的元组；解析不出来返回空元组。"""
    parts = re.findall(r"\d+", str(version or ""))[:3]
    try:
        return tuple(int(part) for part in parts)
    except ValueError:  # pragma: no cover - findall 只给数字
        return ()


def _normalize_profit_save_root(db_path: Path, data_dir: Path) -> str:
    """旧机「产品库自定义保存目录」在新机不存在时，改指到本机默认目录。

    整库替换会把这行设置原样带过来。如果新机没有那个盘符（``D:\\`` 之类的
    移动硬盘/另一台机器布局不同），产品库打开就是空的，用户还得自己去设置里
    翻出来改 —— 而这恰好是「换机后数据看不见」最容易被误判成丢数据的一种。

    只在**根节点都不存在**时才改：目录存在但文件夹还没建的情况保持原样，
    还原时会自动 mkdir（用户可能就是想在原结构下继续用）。

    返回改动说明（没改返回空串）。
    """
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return ""
    try:
        row = conn.execute(
            "SELECT save_root FROM profit_activity_settings WHERE id=1"
        ).fetchone()
        if not row or not str(row[0] or "").strip():
            return ""
        current = str(row[0]).strip()
        path = Path(current).expanduser()
        anchor = path.anchor
        if not anchor:
            return ""
        try:
            anchor_exists = Path(anchor).exists()
        except OSError:
            anchor_exists = False
        if anchor_exists:
            return ""
        fallback = data_dir.parent / "profit_activity"
        with conn:
            conn.execute(
                "UPDATE profit_activity_settings SET save_root=? WHERE id=1",
                (str(fallback),),
            )
        logger.warning("产品库保存目录在本机不可用，已改指默认目录：%s → %s", current, fallback)
        return f"{current} → {fallback}"
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


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
        # 已下载的主题包：体积小（百 KB 级），但对用户是「我买/挑好的皮肤」，
        # 换机后重下要重新登录主题商店，不如一起带走。
        ("themes", data_dir / "themes"),
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
            # 跨卷（数据库被 WH_LOCAL_DATABASE_PATH 指到另一个盘时）os.replace 必然
            # 失败：Windows 报 WinError 17 / errno EXDEV。这不是「占用」，重试也没用，
            # 直接退化成复制 + 删除。
            if getattr(exc, "winerror", None) == 17 or exc.errno == errno.EXDEV:
                shutil.move(str(src), str(dst))
                return
            last = exc
            time.sleep(0.2 * (index + 1))
    raise RuntimeError(f"无法替换数据文件（{dst}）：{last}") from last


def _unlink_with_retry(path: Path, attempts: int = 6) -> None:
    """删文件，遇到 Windows 瞬时占用时退避重试（与 _replace_with_retry 同理）。

    ``apply_pending`` 删 ``-wal`` / ``-shm`` 时如果目标被另一个实例/杀软占着，
    裸 ``unlink`` 会抛 WinError 32，而这一步失败会让整次替换静默失败 ——
    用户界面永远停在「请重启生效」，重启多少次都不生效（2026-10-19 实测复现）。
    """
    last: OSError | None = None
    for index in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last = exc
            time.sleep(0.2 * (index + 1))
    raise RuntimeError(f"无法删除数据文件附属文件（{path}）：{last}") from last


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


def _strip_remote_tokens(conn: sqlite3.Connection) -> tuple[int, int]:
    """兜底：清空所有**未擦除**表里 token 类列上的 ``wh_auth_`` 服务端令牌。

    擦除名单是人工维护的，漏一张表就等于把 7 天有效的会话令牌打进包里。
    这里按「列名含 token + 值以 wh_auth_ 开头」双重条件命中，误伤面极小：
    资产访问令牌（32 位随机串）、本地 claim 令牌都不会被碰。

    返回 (命中的表数, 清空的行数)。
    """
    tables = 0
    rows = 0
    for table in _table_names(conn):
        if table in SCRUBBED_TABLES:
            continue
        columns = [
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if _TOKEN_COLUMN_HINT in str(row[1]).lower()
        ]
        touched = False
        for column in columns:
            try:
                # 用 instr 而不是 LIKE：LIKE 里的 ``_`` 是通配符，'wh_auth_' 会
                # 顺带匹配 'whXauthY' 这类无关值。instr 是字面匹配。
                cursor = conn.execute(
                    f'UPDATE "{table}" SET "{column}" = \'\' '
                    f'WHERE instr(CAST("{column}" AS TEXT), ?) > 0',
                    (_REMOTE_TOKEN_PREFIX,),
                )
            except sqlite3.Error:
                continue
            if cursor.rowcount and cursor.rowcount > 0:
                rows += cursor.rowcount
                touched = True
        if touched:
            tables += 1
    if rows:
        logger.warning("迁移包已清空 %d 行残留的服务端会话令牌（%d 张表）", rows, tables)
    return tables, rows


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
            # 名单之外的兜底：任何残留的 wh_auth_ 令牌一并清掉（写在这个事务里，
            # 随后 VACUUM 连同旧页一起抹除）。
            _strip_remote_tokens(conn)
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
    # 临时目录必须和产物同盘：默认的 %TEMP% 通常在 C:，而数据目录常在 D:，
    # 几 GB 的快照会先把 C: 写爆、而下面的空间预检却算的是 D: 的剩余量。
    temp_dir = Path(tempfile.mkdtemp(prefix=".mig-tmp-", dir=str(out_path.parent)))
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

        asset_entries: list[tuple[str, Path, int]] = []
        asset_bytes = 0
        for group, root in asset_groups(data_dir, db_path):
            for rel, path, size in _walk_files(root):
                asset_entries.append((f"assets/{group}/{rel}", path, size))
                asset_bytes += size

        # 空间预检：临时快照 + zip 产物会同时存在，磁盘不够就直接给出人话错误，
        # 而不是跑到一半抛 ENOSPC 留下半个包。
        db_bytes = sum(path.stat().st_size for _arc, path in staged if path.is_file())
        free_bytes = shutil.disk_usage(out_path.parent).free
        needed = asset_bytes + db_bytes * 2
        if free_bytes < needed:
            raise RuntimeError(
                f"磁盘空间不足：迁移包约需 {needed // (1024 * 1024)} MB，"
                f"当前磁盘只剩 {free_bytes // (1024 * 1024)} MB。"
                "请清理磁盘或把数据目录换到空间更大的盘后再试。"
            )

        db_tables = len(_main_db_tables(db_path))

        total_assets = len(asset_entries)
        manifest: dict[str, Any] = {
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            # 认版本用：manifest 里没有它，import 端的「两台机器版本不一致」永远
            # 判不出来（此前 export 不写、import 却去读，恒为空串）。
            "app_version": _app_version(),
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
            for index, (arcname, path, _size) in enumerate(asset_entries):
                # 内嵌 SQLite（ClipForge 的 clipforge/data/sqlite.db）不能裸拷文件：
                # 运行中的库可能正写到一半。和主库一样走 backup() 取一致性快照；
                # 万一快照失败，退回原文件（至少不让整次导出崩掉）。
                source = path
                snapshot: Path | None = None
                if path.suffix.lower() in _SQLITE_SUFFIXES and _is_sqlite_file(path):
                    snapshot = temp_dir / f"asset-sqlite-{index}.db"
                    # 内嵌库可能正被 ClipForge 子服务写，backup() 撞上写锁会失败一次；
                    # 给它两轮机会再退回原文件。
                    for attempt in range(2):
                        try:
                            _snapshot_db(path, snapshot)
                            source = snapshot
                            break
                        except (sqlite3.Error, OSError) as exc:
                            snapshot.unlink(missing_ok=True)
                            if attempt == 1:
                                logger.warning(
                                    "内嵌数据库快照失败，改为原样打包 %s：%s", path, exc
                                )
                                snapshot = None
                                source = path
                            else:
                                time.sleep(0.3)
                compress = (
                    zipfile.ZIP_STORED
                    if path.suffix.lower() in _STORED_SUFFIXES
                    else zipfile.ZIP_DEFLATED
                )
                try:
                    zf.write(source, arcname, compress_type=compress)
                except OSError as exc:
                    logger.warning("迁移包跳过无法读取的文件 %s：%s", path, exc)
                    if snapshot is not None:
                        snapshot.unlink(missing_ok=True)
                    continue
                if snapshot is not None:
                    snapshot.unlink(missing_ok=True)
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
        # 打包时读不了的文件（被杀软锁住、权限不足）会被跳过；这里如实上报，
        # 否则用户以为「全带走了」，到新机才发现少图。
        "skipped_assets": max(0, manifest.get("asset_files", 0) - written),
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


#: 判定「这是本程序的库」的最小表集合。外来 SQLite 恰好也能过 integrity_check，
#: 一旦被顶上去，工作台会在错误的 schema 上启动 —— 必须先认门牌。
_REQUIRED_TABLES: tuple[str, ...] = ("workbench_settings",)


def _check_required_tables(path: Path) -> None:
    """只认门牌（表是否存在），不做 integrity_check —— 启动路径上要快。"""
    conn = sqlite3.connect(str(path))
    try:
        names = set(_table_names(conn))
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"迁移包里的数据库无法读取：{exc}") from exc
    finally:
        conn.close()
    missing = [table for table in _REQUIRED_TABLES if table not in names]
    if missing:
        raise RuntimeError(
            "迁移包里的数据库不是工作台数据库（缺少 "
            + "、".join(missing)
            + "），已中止导入"
        )


def _verify_workbench_db(path: Path) -> None:
    """integrity_check + 认门牌：排除「格式合法但不是工作台库」的外来文件。"""
    _verify_sqlite(path)
    _check_required_tables(path)


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

        # 版本：新包 → 旧程序必须挡住。旧程序只补列、不降级，把新 schema 的库
        # 顶上去可能让工作台在它不认识的语义上运行。反过来（旧包 → 新程序）
        # 是安全的：启动时 _migrate_core_schema 会补列，只需提示。
        source_version = str(manifest.get("app_version") or "").strip()
        version_notice = ""
        current_version = _app_version()
        source_key = _version_key(source_version)
        current_key = _version_key(current_version)
        if source_key and current_key and source_key > current_key:
            raise RuntimeError(
                f"迁移包来自更高版本（{source_version}），本机是 {current_version}。"
                "请先把本机程序升级到同版本或更高版本，再导入这个包。"
            )
        if source_version and current_version and source_version != current_version:
            version_notice = (
                f"迁移包来自 {source_version}，本机是 {current_version}，"
                "已按本机版本自动补齐数据库结构。"
            )
            logger.warning("迁移包版本不一致：包 %s / 本机 %s", source_version, current_version)

        names = [n for n in zf.namelist() if not n.endswith("/")]
        for name in names:
            if not _safe_arcname(name):
                raise RuntimeError(f"迁移包内含非法路径条目，已中止导入：{name}")

        asset_entries = [n for n in names if n.startswith("assets/")]
        total = len(asset_entries) + 2

        # 2) 解包前的空间预检：zip 头里记着每个条目的原始大小，先加起来对一下
        #    剩余空间。否则跑到 90% 才 ENOSPC，留下半套覆盖过的素材。
        try:
            needed = sum(zf.getinfo(name).file_size for name in asset_entries)
            needed += sum(
                zf.getinfo(arc).file_size
                for arc in ("db/workbench.sqlite3", "db/combo_kit.sqlite3")
                if arc in names
            )
        except KeyError:  # pragma: no cover - namelist 已经取过，理论不会缺
            needed = 0
        if needed:
            # 数据目录可能还不存在（全新机器首次导入），往上找第一个存在的祖先：
            # shutil.disk_usage 在 Windows 上对不存在的路径会直接抛 WinError 3。
            probe = data_dir
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            free = shutil.disk_usage(probe).free
            if free < needed + 256 * 1024 * 1024:
                raise RuntimeError(
                    f"磁盘空间不足：解包需要 {needed // (1024 * 1024)} MB，"
                    f"当前只剩 {free // (1024 * 1024)} MB。请清理磁盘后再导入。"
                )

        # 1) 数据库先落到暂存区（下次启动、开库之前替换）
        pending_dir.mkdir(parents=True, exist_ok=True)
        report("extract", 0, total)
        staged_db = False
        try:
            for arcname, target_name in (
                ("db/workbench.sqlite3", "workbench.sqlite3"),
                ("db/combo_kit.sqlite3", "combo_kit.sqlite3"),
            ):
                if arcname not in names:
                    continue
                target = pending_dir / target_name
                with zf.open(arcname) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                if target_name == "workbench.sqlite3":
                    _verify_workbench_db(target)
                    staged_db = True
                else:
                    _verify_sqlite(target)
            if not staged_db:
                raise RuntimeError("迁移包里没有工作台数据库，无法恢复")
        except BaseException:
            # 校验没过就必须把暂存区清干净：否则 .migration-pending 里留着一个
            # 被拒的库，下次启动 apply_pending 会把它顶上去（实测过）。
            shutil.rmtree(pending_dir, ignore_errors=True)
            raise
        report("extract", 1, total)

        # 2) 产品库自定义 save_root 若在本机不可用，先改指默认目录 —— 必须在下面
        #    解析素材分组**之前**改，否则素材会被还原到旧机路径（甚至还原到不存在的盘）。
        save_root_notice = _normalize_profit_save_root(
            pending_dir / "workbench.sqlite3", data_dir
        )

        # 3) 素材直接就地还原（纯文件覆盖，不涉及数据库锁）。
        #    目录清单要用**刚暂存下来的旧机数据库**解析：产品库的自定义 save_root
        #    存在那一行设置里，而当前库（新机空库）里还没有这条记录。
        groups = dict(asset_groups(data_dir, pending_dir / "workbench.sqlite3"))
        restored = 0
        overwritten = 0
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
            # 先写同目录临时文件再原子替换：中途失败不会留下半截图片/视频，
            # 覆盖已有文件也是「要么旧的、要么新的」，不会有中间态。
            staging = target.with_name(target.name + ".importing")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                existed = target.is_file()
                info = zf.getinfo(name)
                with zf.open(name) as src, open(staging, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                _replace_with_retry(staging, target)
                # 还原原始修改时间：素材按日期排序/筛选的场景很常见，
                # 全部变成「导入那一刻」会打乱用户的时间线。
                try:
                    stamp = time.mktime(tuple(info.date_time) + (0, 0, -1))
                    os.utime(target, (stamp, stamp))
                except (OSError, OverflowError, ValueError):
                    pass
                restored += 1
                if existed:
                    overwritten += 1
            except (OSError, RuntimeError) as exc:
                errors[name] = str(exc)
                staging.unlink(missing_ok=True)
            if index % 50 == 0:
                report("extract", index + 1, total)
        report("extract", total, total)

    (pending_dir / "manifest.json").write_text(
        json.dumps(
            {
                "staged_at": _now(),
                "source_app_version": source_version,
                "source_created_at": str(manifest.get("created_at") or ""),
                "asset_files": restored,
                "asset_overwritten": overwritten,
                "asset_errors": errors,
                "version_notice": version_notice,
                "save_root_notice": save_root_notice,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "asset_files": restored,
        "asset_overwritten": overwritten,
        "asset_errors": len(errors),
        "version_notice": version_notice,
        "save_root_notice": save_root_notice,
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
    # 上一次启动尝试替换但失败了：必须让界面看得见，否则用户会一直对着
    # 「请重启生效」重启到天亮（此前只写日志，界面永远不解释）。
    try:
        info["apply_error"] = json.loads(
            (pending_dir / APPLY_ERROR_NAME).read_text(encoding="utf-8")
        ).get("message", "")
    except (OSError, json.JSONDecodeError):
        info["apply_error"] = ""
    return info


def record_apply_error(data_dir: Path, error: BaseException) -> None:
    """把「替换失败」落盘给界面看。

    启动期的迁移失败只写日志等于没写：用户看不到日志，只会对着「请重启生效」
    一遍遍重启。这里写进 ``.migration-pending/apply-error.json``，
    :func:`pending_info` 会把它带给前端。
    """
    pending_dir = data_dir / PENDING_DIR_NAME
    try:
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / APPLY_ERROR_NAME).write_text(
            json.dumps(
                {"at": _now(), "message": str(error) or type(error).__name__},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("记录迁移替换失败原因时出错")


def _prune_backups(db_path: Path, keep: int = 3) -> None:
    """只留最近几份 ``.pre-import-*.bak``：每次导入都留一份 50MB 级的库，
    反复试几次就能把数据盘塞满。文件名带时间戳，字典序即时间序。"""
    try:
        backups = sorted(db_path.parent.glob(f"{db_path.name}{_BACKUP_MARKER}*.bak"))
    except OSError:
        return
    for stale in backups[:-keep] if len(backups) > keep else []:
        try:
            stale.unlink()
            logger.info("清理过期的迁移回滚备份：%s", stale)
        except OSError:
            continue


def apply_pending(data_dir: Path, db_path: Path) -> dict[str, Any]:
    """把暂存的迁移包数据库顶上（导入后的首次启动调用）。

    必须在任何连接打开数据库**之前**执行。替换前把当前库备份为
    ``<库名>.pre-import-<时间戳>.bak``，误操作可人工回滚。
    """
    pending_dir = data_dir / PENDING_DIR_NAME
    staged_main = pending_dir / "workbench.sqlite3"
    if not staged_main.is_file():
        return {"applied": False}
    # 防线二：导入时校验过，但暂存区可能在两次启动之间被塞进别的东西
    # （手工拷贝、磁盘异常、旧的半截文件）。这里不做 integrity_check（启动要快），
    # 只确认门牌 —— 认不出就不替换，原库原样保留。
    _check_required_tables(staged_main)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = ""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.is_file():
        backup_path = db_path.with_name(f"{db_path.name}{_BACKUP_MARKER}{stamp}.bak")
        # 用 backup() 而不是 copy2：库是 WAL 模式，异常退出（崩溃/强杀）留下的
        # ``-wal`` 里可能有已提交但未 checkpoint 的数据，裸拷主库文件会丢掉最后
        # 一段 —— 而这正是用户想回滚时的「最新数据」。
        try:
            _snapshot_db(db_path, backup_path)
        except (sqlite3.Error, OSError) as exc:
            # 当前库已损坏/被锁时不能让备份挡住导入：退回裸拷，至少留一份原文件。
            logger.warning("原库快照失败，改为直接复制备份：%s", exc)
            shutil.copy2(db_path, backup_path)
        backup = str(backup_path)
    for suffix in ("-wal", "-shm"):
        # 必须重试：另一个实例/杀软占着就可能 WinError 32，而这一步失败会让整次
        # 替换静默失败（2026-10-19 实测复现过）。
        _unlink_with_retry(Path(str(db_path) + suffix))
    _replace_with_retry(staged_main, db_path)

    combo_target = data_dir / "combo-kit" / "combo_kit.sqlite3"
    staged_combo = pending_dir / "combo_kit.sqlite3"
    if staged_combo.is_file():
        combo_target.parent.mkdir(parents=True, exist_ok=True)
        if combo_target.is_file():
            _snapshot_db(
                combo_target,
                combo_target.with_name(f"{combo_target.name}{_BACKUP_MARKER}{stamp}.bak"),
            )
        for suffix in ("-wal", "-shm"):
            _unlink_with_retry(Path(str(combo_target) + suffix))
        _replace_with_retry(staged_combo, combo_target)

    shutil.rmtree(pending_dir, ignore_errors=True)
    _prune_backups(db_path)
    if staged_combo.is_file() or combo_target.is_file():
        _prune_backups(combo_target)
    logger.warning("已应用导入的数据迁移包：database=%s backup=%s", db_path, backup or "-")
    return {"applied": True, "backup": backup}


# --------------------------------------------------------------------------- #
# 后台任务（导出体积可达 GB 级，不能用同步请求扛）
# --------------------------------------------------------------------------- #
class MigrationJobs:
    """极简后台任务表：一个线程跑一件迁移任务，前端轮询进度。"""

    def __init__(self, data_dir: Path, db_path: Path | None, service: Any = None):
        self.data_dir = data_dir
        self.db_path = db_path
        #: 内嵌子服务（ClipForge）。导入会覆盖它正在用的 sqlite.db 与素材目录，
        #: 必须先停、后启；传 None 表示本环境没有这个服务。
        self._service = service
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
            # 同一时刻只允许一个迁移任务：两个导出会互相 rmtree 掉对方正在写的包，
            # 导出与导入并行则会在读素材的同时改素材。
            running = [j for j in self._jobs.values() if j["state"] == "running"]
            if running:
                raise RuntimeError("已有迁移任务正在进行，请等它结束再开始新的任务")
            self._jobs[job["job_id"]] = job
            self._prune_locked()
        return job

    def _prune_locked(self) -> None:
        """只留最近 20 条已结束的任务，避免 job 表随使用无限增长。"""
        finished = [j for j in self._jobs.values() if j["state"] != "running"]
        if len(finished) <= 20:
            return
        finished.sort(key=lambda j: str(j.get("created_at") or ""))
        for stale in finished[: len(finished) - 20]:
            self._jobs.pop(stale["job_id"], None)

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
            resume = self._pause_embedded_service()
            try:
                result = stage_import(
                    archive_path, self.data_dir, on_progress=self._progress(job)
                )
                self._finish(job, "done", "", result)
            except Exception as exc:  # noqa: BLE001
                logger.exception("导入迁移包失败")
                self._finish(job, "failed", str(exc), None)
            finally:
                resume()
                try:
                    archive_path.unlink(missing_ok=True)
                except OSError:
                    pass

        threading.Thread(target=run, name="migration-import", daemon=True).start()
        return dict(job)

    def _pause_embedded_service(self) -> Callable[[], None]:
        """导入前停掉内嵌子服务，返回「恢复现场」回调（原本没跑就不动它）。

        ClipForge 的 ``data/sqlite.db`` 和素材目录正是这次导入要覆盖的对象。
        不停就覆盖 = 往一个正在写的库里灌数据。
        """
        service = self._service
        if service is None:
            return lambda: None
        was_running = False
        try:
            state = str(getattr(service.status(), "state", "") or "")
            was_running = state in {"ready", "starting"}
        except Exception:  # noqa: BLE001 - 状态读不到就按「没在跑」处理
            logger.exception("读取内嵌服务状态失败，按未运行处理")
        try:
            service.stop()
        except Exception:  # noqa: BLE001 - 停不下来也要继续导入，最坏是覆盖正在写的库
            logger.exception("导入前停止内嵌服务失败")

        def resume() -> None:
            if not was_running:
                return
            try:
                service.start()
            except Exception:  # noqa: BLE001
                logger.exception("导入后重启内嵌服务失败")

        return resume

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
