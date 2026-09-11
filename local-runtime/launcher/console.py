"""启动器·资源控制台核心逻辑：纯 Python 实现，不依赖 PySide6，可用 CLI 单独运行测试。

职责：
  - 扫描各模块本地个人资产（产品处理草稿池/来源图库/任务输出、POD 资产、产品库、
    AI 素材会话、组合套装、价格核验输出、本地缓存）
  - 展示占用（文件数 / 字节数 / 数据库行数）
  - 清理（删除资产文件 + 关联数据库行，弹窗二次确认）
  - 导出 / 导入（把选中类别的资产文件 + 数据行打包为 zip，便于数据转移）

安全约定：
  - 清理仅操作「个人资产生成物」目录与对应数据行，绝不触碰 system_config / secret_values。
  - 清理前由 GUI 弹窗二次确认；本模块只接受 confirm 回调，未确认则不执行。
  - 网络/Redis 缓存不在本地落地，故不涉及；「清理缓存」仅处理磁盘上的 __pycache__ 与临时文件。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import core

SCHEMA_VERSION = 1

# 缓存清理时按这些规则识别"可安全删除"的临时产物
_CACHE_DIR_NAMES = {"__pycache__"}
_CACHE_FILE_PATTERNS = (re.compile(r"^~\$"), re.compile(r"\.tmp$", re.I),
                        re.compile(r"\.log\.\d+$", re.I), re.compile(r"\.pyc$", re.I),
                        re.compile(r"^Thumbs\.db$", re.I), re.compile(r"^\.DS_Store$"))


@dataclass
class Category:
    """一个可管理/可清理的资源类别。"""
    id: str
    name: str
    description: str
    dirs: tuple[Path, ...] = ()
    # 数据库相关：None db_path 表示使用主 workbench.sqlite3
    db_path: Path | None = None
    # 显式表名（优先）或前缀匹配（prefix 非空时按 sqlite_master LIKE 收集）
    tables: tuple[str, ...] = ()
    table_prefix: str = ""
    exportable: bool = True  # 缓存类不可导出

    def resolve_tables(self, db_path: Path | None = None) -> tuple[str, ...]:
        """解析该类别关联的数据表。

        - 显式 tables 优先；
        - 否则按 table_prefix 在 db_path（缺省用本类别 db_path）中 LIKE 匹配收集。
        """
        if self.tables:
            return self.tables
        db = db_path or self.db_path
        if self.table_prefix and db and db.is_file():
            try:
                with sqlite3.connect(str(db)) as conn:
                    rows = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?",
                        (f"{self.table_prefix}%",),
                    ).fetchall()
                return tuple(r[0] for r in rows)
            except sqlite3.Error:
                return ()
        return ()


@dataclass
class CategoryStat:
    id: str
    name: str
    description: str
    exists: bool = False
    dir_count: int = 0
    file_count: int = 0
    byte_size: int = 0
    table_count: int = 0
    rows: int = 0
    cache_cleanable: bool = False
    cache_items: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


def resolve_data_dir() -> Path | None:
    """数据目录（wh-local）。"""
    return core.data_dir_from_env()


def resolve_db() -> Path | None:
    """主 workbench.sqlite3。"""
    return core.resolve_db_path()


def _profit_activity_dirs(data_dir: Path, main_db: Path) -> tuple[Path, ...]:
    """利润活动/产品库资产根：默认 data.parent/profit_activity，如用户配置了 save_root 再追加。"""
    root = data_dir.parent / "profit_activity"
    roots = [root]
    if main_db.is_file():
        try:
            with sqlite3.connect(str(main_db)) as conn:
                row = conn.execute(
                    "SELECT save_root FROM profit_activity_settings WHERE id=1"
                ).fetchone()
            if row and row[0]:
                extra = Path(row[0]).expanduser()
                if extra not in roots:
                    roots.append(extra)
        except sqlite3.Error:
            pass
    return tuple(roots)


def _combo_db(data_dir: Path) -> Path:
    return data_dir / "combo-kit" / "combo_kit.sqlite3"


def _profit_db() -> Path | None:
    # 利润活动的表在主 workbench.sqlite3（app/main.py 传入共享 db_path）
    return resolve_db()


def build_categories(data_dir: Path | None = None, main_db: Path | None = None) -> list[Category]:
    """构建资源目录清单（目录路径均已解析为绝对路径）。"""
    data_dir = data_dir or resolve_data_dir()
    main_db = main_db or resolve_db()
    if data_dir is None:
        data_dir = Path.cwd() / "outputs" / "wh-local"

    pp_assets = data_dir / "product-processing-assets"
    cats = [
        Category(
            id="pp_drafts",
            name="产品处理·草稿池",
            description="上传的半成品草稿图片与草稿记录（只有写入，最易累积）",
            dirs=(pp_assets / "draft-images",),
            tables=("product_processing_drafts",),
        ),
        Category(
            id="pp_sources",
            name="产品处理·来源图库",
            description="选品/核价来源图片库与来源图记录",
            dirs=(pp_assets / "source-image-library",),
            tables=("product_processing_source_images",),
        ),
        Category(
            id="pp_outputs",
            name="产品处理·任务输出",
            description="任务生成的 Excel/CSV/清单、生成图、预览/媒体/画布/组合货源资产",
            dirs=(pp_assets / "outputs",),
            tables=(
                "product_processing_tasks",
                "product_processing_task_items",
                "product_processing_daily_selection_intakes",
                "product_processing_handoff_receipts",
                "product_processing_prompts",
            ),
        ),
        Category(
            id="pod_assets",
            name="POD 定制资产",
            description="POD 定制生成的图片、批次、导出记录等资产",
            dirs=(data_dir / "pod-customization-assets",),
            table_prefix="pod_customization_",
        ),
        Category(
            id="pa_library",
            name="利润活动·产品库",
            description="产品库图片、活动过滤输出（可申报/剔除），以及产品库/活动记录",
            dirs=_profit_activity_dirs(data_dir, main_db),
            table_prefix="profit_activity_",
        ),
        Category(
            id="ai_assets",
            name="AI 服务·素材与会话",
            description="AI 服务附件的本地素材、会话消息与生成结果",
            dirs=(data_dir / "ai-service",),
            table_prefix="ai_service_",
        ),
        Category(
            id="combo_assets",
            name="组合套装资产",
            description="组合套装的原始图与成品图（独立数据库存储）",
            dirs=(data_dir / "combo-kit",),
            db_path=_combo_db(data_dir),
            table_prefix="combo_kit_",
        ),
        Category(
            id="pv_outputs",
            name="价格核验输出",
            description="价格核验的询价记录、批量会话与导出产物",
            dirs=(data_dir / "price-verification",),
            table_prefix="price_verification_",
        ),
        Category(
            id="cache",
            name="本地缓存 / 临时文件",
            description="Python 缓存目录与临时产物（安全清理，不影响数据）",
            dirs=(data_dir, data_dir.parent / "profit_activity"),
            exportable=False,
        ),
    ]
    return cats


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def is_product_running(port: int = 8010, timeout: float = 1.0) -> bool:
    """探测主程序是否运行中（端口被占用通常即为本机实例）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _dir_stats(directory: Path) -> tuple[int, int]:
    """遍历目录，返回 (文件数, 字节数)。目录不存在时返回 (0,0)。"""
    files = bytes_written = 0
    if not directory or not directory.is_dir():
        return 0, 0
    for base, _dirnames, filenames in os.walk(directory):
        for name in filenames:
            try:
                size = (Path(base) / name).stat().st_size
            except OSError:
                size = 0
            files += 1
            bytes_written += size
    return files, bytes_written


def _table_rows(db_path: Path, table: str) -> int:
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _collect_cache(directory: Path) -> tuple[list[Path], list[Path]]:
    """在目录下收集缓存目录与临时文件，返回 (目录列表, 文件列表)。"""
    dirs: list[Path] = []
    files: list[Path] = []
    if not directory or not directory.is_dir():
        return dirs, files
    for base, dirnames, filenames in os.walk(directory):
        for d in dirnames:
            if d in _CACHE_DIR_NAMES:
                dirs.append(Path(base) / d)
        for name in filenames:
            if any(pat.search(name) for pat in _CACHE_FILE_PATTERNS):
                files.append(Path(base) / name)
    return dirs, files


def _confirm(confirm: Callable[[str], bool] | None, message: str) -> bool:
    """统一确认入口：CLI 传入 None 时默认放行（由调用方负责提示）。"""
    if confirm is None:
        return True
    return bool(confirm(message))


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def scan(category_ids: list[str] | None = None, main_db: Path | None = None) -> list[dict[str, Any]]:
    """扫描全部（或指定）类别，返回统计列表。"""
    main_db = main_db or resolve_db()
    cats = build_categories()
    if category_ids:
        cats = [c for c in cats if c.id in category_ids]

    results: list[dict[str, Any]] = []
    for cat in cats:
        stat = CategoryStat(id=cat.id, name=cat.name, description=cat.description)
        if cat.id == "cache":
            base_dirs = [d for d in cat.dirs if d.is_dir()]
            cdirs, cfiles = [], []
            for d in base_dirs:
                dd, ff = _collect_cache(d)
                cdirs.extend(dd)
                cfiles.extend(ff)
            stat.cache_cleanable = True
            stat.cache_items = len(cdirs) + len(cfiles)
            stat.dir_count = len(cdirs)
            stat.file_count = len(cfiles)
            stat.byte_size = _bytes_of_paths(cdirs + cfiles)
            # 数据库统计不适用
            stat.exists = bool(base_dirs)
            results.append(stat.as_dict())
            continue

        # 普通资产类别
        for d in cat.dirs:
            if d.is_dir():
                stat.exists = True
                stat.dir_count += 1
                files, size = _dir_stats(d)
                stat.file_count += files
                stat.byte_size += size

        if cat.db_path is not None:
            db = cat.db_path
        else:
            db = main_db
        if db is not None and db.is_file():
            tables = cat.resolve_tables(db)
            stat.table_count = len(tables)
            stat.rows = sum(_table_rows(db, t) for t in tables)
        results.append(stat.as_dict())
    return results


def _bytes_of_paths(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            for base, _dn, fns in os.walk(p):
                for name in fns:
                    total += (Path(base) / name).stat().st_size
        except OSError:
            continue
    return total


# --------------------------------------------------------------------------- #
# 清理
# --------------------------------------------------------------------------- #
def clean(
    category_ids: list[str],
    main_db: Path | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """清理指定类别：删除资产文件 + 清空关联数据行。

    - 仅当主程序未运行时才清空数据库行（避免锁/损坏）；文件删除无此限制。
    - confirm 回调用于二次确认；未被确认的类别跳过。
    """
    main_db = main_db or resolve_db()
    cats = {c.id: c for c in build_categories()}
    results: dict[str, Any] = {"status": "ok", "cleaned": [], "skipped": [], "errors": {}}

    running = is_product_running()
    for cat_id in category_ids:
        cat = cats.get(cat_id)
        if cat is None:
            results["errors"][cat_id] = "unknown category"
            continue

        detail = _describe_clean(cat, main_db)
        if _confirm(confirm, f"确定清理【{cat.name}】？\n\n{detail}\n\n此操作不可撤销（如有需要请先导出备份）。"):
            try:
                _do_clean(cat, main_db, allow_db_rows=not running)
                results["cleaned"].append(cat_id)
            except Exception as exc:  # noqa: BLE001 - 逐项记录，避免一个失败中断全部
                results["errors"][cat_id] = str(exc)
        else:
            results["skipped"].append(cat_id)

    if results["errors"]:
        results["status"] = "partial"
    return results


def _describe_clean(cat: Category, main_db: Path | None) -> str:
    lines: list[str] = []
    if cat.id == "cache":
        base_dirs = [d for d in cat.dirs if d.is_dir()]
        cdirs, cfiles = [], []
        for d in base_dirs:
            dd, ff = _collect_cache(d)
            cdirs.extend(dd)
            cfiles.extend(ff)
        lines.append(f"将删除缓存目录 {len(cdirs)} 个、临时文件 {len(cfiles)} 个。")
        return "\n".join(lines)
    for d in cat.dirs:
        if d.is_dir():
            files, size = _dir_stats(d)
            lines.append(f"{d}\n  文件 {files} 个，占用 {_human(size)}")
        else:
            lines.append(f"{d}\n  （目录不存在，跳过文件部分）")
    if not running_check(cat):
        db = cat.db_path or main_db
        if db is not None and db.is_file():
            tables = cat.resolve_tables(db)
            if tables:
                lines.append(f"数据库表 {len(tables)} 张共 {sum(_table_rows(db, t) for t in tables)} 行将清空。")
    return "\n".join(lines) if lines else "（无可清理内容）"


def _clean_running(cat: Category, main_db: Path | None) -> bool:
    return is_product_running()


def running_check(cat: Category) -> bool:
    """占位：是否因主程序运行而跳过数据库清理。"""
    return is_product_running()


def _do_clean(cat: Category, main_db: Path | None, allow_db_rows: bool) -> None:
    if cat.id == "cache":
        for d in cat.dirs:
            if not d.is_dir():
                continue
            cdirs, cfiles = _collect_cache(d)
            for p in cdirs:
                shutil.rmtree(p, ignore_errors=True)
            for p in cfiles:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        return

    # 1) 删除资产目录内容
    for d in cat.dirs:
        if d.is_dir():
            for child in list(d.iterdir()):
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except OSError:
                    pass

    # 2) 清空关联数据行（主程序运行中跳过，避免破坏正在使用的数据库）
    if allow_db_rows:
        db = cat.db_path or main_db
        if db is not None and db.is_file():
            conn = sqlite3.connect(str(db))
            try:
                with conn:
                    for table in cat.resolve_tables():
                        conn.execute(f'DELETE FROM "{table}"')
            finally:
                conn.close()


def _human(size: int) -> str:
    step = 1024.0
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= step
    return f"{value:.1f} GB"


# --------------------------------------------------------------------------- #
# 导出 / 导入（zip）
# --------------------------------------------------------------------------- #
def export_archive(category_ids: list[str], out_path: Path, main_db: Path | None = None) -> dict[str, Any]:
    """把选中类别的「资产文件 + 数据行」打包为 zip，形成可迁移备份。

    结构：manifest.json + resources/{cat_id}/<目录> + db/{cat_id}/{table}.json
    不含 system_config / secret_values，不覆盖目标机密钥。
    """
    main_db = main_db or resolve_db()
    cats = {c.id: c for c in build_categories() if c.exportable}
    out_path = Path(out_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "fail", "message": f"无法创建导出目录：{exc}"}

    manifest: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version": getattr(core, "_APP_VERSION", "") or "",
        "categories": {},
    }
    written_files = 0
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for cat_id in category_ids:
                cat = cats.get(cat_id)
                if cat is None:
                    continue
                info: dict[str, Any] = {"name": cat.name, "files": [], "tables": []}
                # 资产目录
                for d in cat.dirs:
                    if not d.is_dir():
                        continue
                    anchor = d.name
                    for file in sorted(d.rglob("*")):
                        if file.is_file():
                            arcname = f"resources/{cat_id}/{anchor}/{file.relative_to(d).as_posix()}"
                            zf.write(file, arcname)
                            info["files"].append(str(file.relative_to(d)))
                            written_files += 1
                # 数据行（不以主 DB 的密钥/配置表为对象；只导出该类别表）
                db = cat.db_path or main_db
                if db is not None and db.is_file():
                    for table in cat.resolve_tables(db):
                        rows = _export_table_rows(db, table)
                        if not rows:
                            continue
                        zf.writestr(f"db/{cat_id}/{table}.json",
                                    json.dumps(rows, ensure_ascii=False, default=str))
                        info["tables"].append({"table": table, "rows": len(rows)})
                manifest["categories"][cat_id] = info
            manifest["total_files"] = written_files
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    except OSError as exc:
        return {"status": "fail", "message": f"导出失败：{exc}"}

    total = sum(c["files"] and 1 or 0 for c in manifest["categories"].values())
    return {
        "status": "ok",
        "message": f"已导出 {len(manifest['categories'])} 个类别到 {out_path}",
        "path": str(out_path),
        "categories": manifest["categories"],
    }


def _export_table_rows(db_path: Path, table: str) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
            if not cols:
                return []
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def import_archive(archive_path: Path, main_db: Path | None = None, confirm: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """从导出 zip 恢复选中类别的资产与数据行。

    资产文件解回各自类别的目标目录；数据行按表 UPSERT（仅插入目标库已存在的列）。
    """
    main_db = main_db or resolve_db()
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        return {"status": "fail", "message": f"备份文件不存在：{archive_path}"}

    cats = {c.id: c for c in build_categories()}
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            manifest = _read_manifest(zf, archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        return {"status": "fail", "message": f"无法读取备份：{exc}"}

    if manifest is None or not isinstance(manifest, dict):
        return {"status": "fail", "message": "备份缺少有效 manifest.json"}

    # 仅导入备份里出现、且本机认识的类别
    cat_ids = [cid for cid in manifest.get("categories", {}) if cid in cats]
    if not cat_ids:
        return {"status": "fail", "message": "备份中没有可导入的资源类别"}

    if _confirm(confirm, f"导入将把备份内容恢复到本地对应目录/数据库。\n\n"
                          f"共 {len(cat_ids)} 个类别（{', '.join(cat_ids)}）。\n"
                          f"已有同名资源会被覆盖，数据库行按唯一键冲突时替换。\n\n是否继续？"):
        restored, errors = _do_import(manifest, cats, archive_path, main_db)
        return {"status": "ok" if not errors else "partial",
                "message": f"导入完成：目录/文件 {restored} 项。",
                "restored": restored, "errors": errors}
    return {"status": "aborted", "message": "已取消导入"}


def _read_manifest(zf: zipfile.ZipFile, archive_path: Path) -> dict[str, Any] | None:
    if "manifest.json" not in zf.namelist():
        return None
    try:
        return json.loads(zf.read("manifest.json").decode("utf-8"))
    except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
        return None


def _do_import(manifest: dict[str, Any], cats: dict[str, Category], archive_path: Path,
               main_db: Path | None) -> tuple[int, dict[str, str]]:
    """从备份 zip 恢复资产文件与数据行。

    - 资产文件：解回匹配的 Category.dirs（按目录名 anchor 定位）。
    - 数据行：按表 INSERT OR REPLACE，仅写入目标库已存在的列。
    返回 (恢复项数, 错误字典)。
    """
    restored = 0
    errors: dict[str, str] = {}
    try:
        zf = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        return 0, {"_archive": str(exc)}

    with zf:
        names = {n for n in zf.namelist() if not n.endswith("/")}
        cat_info = manifest.get("categories") or {}
        for cat_id, info in cat_info.items():
            cat = cats.get(cat_id)
            if cat is None:
                errors[cat_id] = "本机无此类别，跳过"
                continue

            # 1) 恢复资产文件
            file_prefix = f"resources/{cat_id}/"
            for name in sorted(n for n in names if n.startswith(file_prefix)):
                try:
                    if _restore_one_file(zf, name, cat):
                        restored += 1
                    else:
                        errors[f"{cat_id}:{name.split('/', 3)[-1]}"] = "无法定位目标目录"
                except Exception as exc:  # noqa: BLE001 - 单文件失败不中断
                    errors[f"{cat_id}:{name.split('/', 3)[-1]}"] = str(exc)

            # 2) 恢复数据行
            db = cat.db_path or main_db
            if db is None or not db.is_file():
                continue
            db_prefix = f"db/{cat_id}/"
            for name in sorted(n for n in names if n.startswith(db_prefix) and n.endswith(".json")):
                table = os.path.basename(name)[:-5]
                try:
                    payload = json.loads(zf.read(name).decode("utf-8"))
                    if not isinstance(payload, list):
                        errors[f"{cat_id}:{table}"] = "数据行格式无效，跳过"
                        continue
                    n = _restore_table_rows(db, table, payload)
                    restored += n
                except Exception as exc:  # noqa: BLE001
                    errors[f"{cat_id}:{table}"] = str(exc)
    return restored, errors


def _restore_one_file(zf: zipfile.ZipFile, name: str, cat: Category) -> bool:
    """把备份中单个资产文件解回对应目标目录。name 形如 resources/{cat}/{anchor}/{relative...}。"""
    parts = name.split("/")
    if len(parts) < 4:  # resources / cat_id / anchor / <file>
        return False
    anchor = parts[2]
    relative = "/".join(parts[3:])
    base = next((d for d in cat.dirs if d.name == anchor), None)
    if base is None:
        return False
    target = (base / relative).resolve()
    base_resolved = base.resolve()
    # 防 zip-slip：目标必须落在基准目录内
    if target != base_resolved and base_resolved not in target.parents:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return True
    except OSError:
        return False


def _restore_table_rows(db_path: Path, table: str, rows: list[Any]) -> int:
    """把导出的数据行写回目标表（仅写入存在的列，按唯一键冲突替换）。"""
    if not db_path.is_file():
        return 0
    with sqlite3.connect(str(db_path)) as conn:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        if not cols:
            return 0
        col_sql = ",".join(f'"{c}"' for c in cols)
        ph = ",".join("?" * len(cols))
        insert_sql = f'INSERT OR REPLACE INTO "{table}" ({col_sql}) VALUES ({ph})'
        count = 0
        with conn:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    conn.execute(insert_sql, [row.get(c) for c in cols])
                    count += 1
                except sqlite3.Error:
                    continue  # 单行失败跳过，不中断整体导入
    return count


# --------------------------------------------------------------------------- #
# CLI（供 core 调用，也可单独运行）
# --------------------------------------------------------------------------- #
def run_cli(argv: list[str]) -> int:
    """资源控制台 CLI：--console-stats / --console-clean / --console-export / --console-import。"""
    if "--console-stats" in argv:
        ids = _arg_list(argv, "--cats")
        stats = scan(ids or None)
        print(json.dumps({"status": "ok", "resources": stats,
                          "running": is_product_running()}, ensure_ascii=False, indent=2))
        return 0

    if "--console-clean" in argv:
        ids = _arg_list(argv, "--cats")
        if not ids:
            print(json.dumps({"status": "fail", "message": "需要 --cats 指定类别"}, ensure_ascii=False))
            return 1
        result = clean(ids, confirm=lambda msg: _cli_confirm(msg))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1

    if "--console-export" in argv:
        ids = _arg_list(argv, "--cats")
        out = Path(_arg_of(argv, "--out") or "mainpg-resources.zip")
        result = export_archive(ids, out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1

    if "--console-import" in argv:
        arch = Path(_arg_of(argv, "--archive") or "")
        if not arch:
            print(json.dumps({"status": "fail", "message": "需要 --archive 指定备份文件"}, ensure_ascii=False))
            return 1
        result = import_archive(arch, confirm=lambda msg: _cli_confirm(msg))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"ok", "partial"} else 1

    return 2


def _cli_confirm(_msg: str) -> bool:
    return True  # CLI 默认确认（调用方已按需提示）


def _arg_of(argv: list[str], flag: str) -> str:
    if flag not in argv:
        return ""
    idx = argv.index(flag)
    return argv[idx + 1] if idx + 1 < len(argv) else ""


def _arg_list(argv: list[str], flag: str) -> list[str]:
    val = _arg_of(argv, flag)
    if not val:
        return []
    sep = "," if "," in val else None
    return [x.strip() for x in (val.split(sep) if sep else val.split()) if x.strip()]


if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))
