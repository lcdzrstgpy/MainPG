"""产品处理本地缓存清扫（个人中心「存储空间」的产品处理分区）。

只回收「没有任何数据库记录引用」的本地文件（第 1、2 类）；第 3、4 类是
**默认不勾、显式选择才清理**的例外。分四类（与 POD 侧同一套产品语义）：

1. ``pp_asset_orphans``（媒体库无引用图片）：``product_processing_media_assets``
   里从任何地方都找不到引用的行（媒体绑定 / 预览资产 / 尺寸画布 / 任何 JSON 列里
   的 id 提及），且超窗 → 删行；文件确认「全库再无引用」后才删。
2. ``pp_disk_orphans``（无引用遗留文件）：磁盘 ↔ 数据库反向对账——遍历托管根
   目录，删除「归一化绝对路径不在全库引用集合里、且 mtime 超窗」的文件。
3. ``pp_review_images``（历史任务的复核原图）：**有意区别于前两类**——这些是
   任务结果 JSON（``result_json`` / ``output_json`` 的
   ``provider_original_image_paths``）留档的不合格原图，程序只写不读（界面不
   展示、没有读回接口），却是任务产出无限增长的大头。本分类**只删超 30 天的
   文件、不改 JSON 记录**（记录里的路径悬空无害），且只认「复核原图」命名、
   只认「纯 JSON 留档引用」的文件——凡被 ``*_path`` / ``*_file`` 列引用的一律
   不碰。单独勾选前两类永远删不到它们（JSON 引用算「有人要」），只有显式勾选
   本分类才会清理。
4. ``pp_task_exports``（历史任务的导出表格）：任务产出的店小秘/妙手导入表、
   失败报告与视频清单（含预检最终化按运行留档的表格）。这些文件**正是被**
   ``*_file`` / ``*_path`` **列登记在用**的正式产出——磁盘对账永远不碰它们；
   本分类只在用户显式勾选时，回收「命名匹配导出前缀 + 被列精确登记 + 超
   30 天」的文件。与复核原图一样**只删文件、不改任务记录**：删除后任务页
   「下载」会提示文件不存在，需要时重新导出即可。

引用集合按**命名约定**通用地收集（不写死表名，自动涵盖未来新增的表）：
- ``*_path`` / ``*_file`` 列：直接取值（PP 的路径列存的都是绝对路径）；
- ``*_json`` 列：递归提取其中的字符串，取绝对路径形态的值
  （例如任务结果 ``result_json`` / 阶段回执 ``output_json`` 里的
  ``provider_original_image_paths``——**被这些 JSON 引用的原图不算垃圾**）。
新增路径列请遵守上述命名；不守约定的列需要同步扩展 ``_collect_reference_data``。

安全取向与 POD 一致：**只少报、不虚报**——预览用「只回滚事务」跑与执行完全
相同的逻辑（``_run_in_rollback``），单分类执行量 == 该分类扫描量。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import bindparam, text

from .infrastructure.assets import ProductProcessingAssets
from .infrastructure.database import ProductProcessingDatabase
from .service import ProductProcessingValidationError

logger = logging.getLogger(__name__)

# 「存储空间」里产品处理侧的可勾选分类。
PP_CACHE_CATEGORY_ASSET_ORPHANS = "pp_asset_orphans"
PP_CACHE_CATEGORY_DISK_ORPHANS = "pp_disk_orphans"
PP_CACHE_CATEGORY_REVIEW_IMAGES = "pp_review_images"
PP_CACHE_CATEGORY_TASK_EXPORTS = "pp_task_exports"
PP_CACHE_ROW_CATEGORIES = (PP_CACHE_CATEGORY_ASSET_ORPHANS,)
PP_CACHE_CATEGORY_KEYS = PP_CACHE_ROW_CATEGORIES + (
    PP_CACHE_CATEGORY_DISK_ORPHANS,
    PP_CACHE_CATEGORY_REVIEW_IMAGES,
    PP_CACHE_CATEGORY_TASK_EXPORTS,
)

# 反向对账排除的辅助文件：处理期的源图清单（体积可忽略、可再生，保守保留）。
_DISK_SCAN_SKIP_SUFFIXES = ("_source_images.txt",)

# 复核原图的命名特征（grid_image_provider_original_N.png）；只有这个命名才进
# 「历史任务的复核原图」分类，避免误伤其它 JSON 留档引用的文件。
_REVIEW_IMAGE_NAME_MARKER = "provider_original"
# 复核原图保留期（天）：比通用 48h 窗口宽得多——留够人工复核的时间。
_REVIEW_IMAGE_RETENTION_DAYS = 30

# 任务导出文件的命名前缀（店小秘导入表 / 失败报告 / 视频清单 / 妙手两类模板）。
# 命名匹配只是第一道筛子：还要求被 ``*_path`` / ``*_file`` 列精确登记（见
# ``_task_export_paths_tx``），才认定为「任务的正式导出」。
_TASK_EXPORT_NAME_PREFIXES = (
    "dxm_import_task_",
    "error_report_task_",
    "product_video_manifest_task_",
    "miaoshou_apparel_task_",
    "miaoshou_general_task_",
)
_TASK_EXPORT_SUFFIXES = (".xlsx", ".csv")
# 导出表格保留期（天）：与复核原图同一档——30 天内的导出件保证可直接下载。
_TASK_EXPORT_RETENTION_DAYS = 30


def _is_task_export_name(name: str) -> bool:
    """任务导出文件命名：店小秘/妙手导入表、失败报告、视频清单（含 token 变体）。"""
    return name.endswith(_TASK_EXPORT_SUFFIXES) and name.startswith(_TASK_EXPORT_NAME_PREFIXES)


class ProductProcessingCacheSweep:
    """产品处理侧的选择性缓存清扫（供路由层调用，不直接持有 HTTP 语义）。"""

    def __init__(
        self,
        assets: ProductProcessingAssets,
        database: ProductProcessingDatabase,
    ) -> None:
        self.assets = assets
        self.database = database

    # ---- 对外：扫描 / 清扫 ----

    def preview_local_cache(self, *, older_than_hours: int = 48) -> dict[str, Any]:
        """扫描（只读）：按分类返回可释放的文件数与字节数，不删任何东西。"""

        categories: list[dict[str, Any]] = []
        total_files = 0
        total_bytes = 0
        for key in PP_CACHE_CATEGORY_KEYS:
            paths = self._preview_category(key, older_than_hours=older_than_hours)
            files, freed = self._measure(paths)
            categories.append({"key": key, "files": files, "bytes": freed})
            total_files += files
            total_bytes += freed
        return {
            "older_than_hours": int(older_than_hours),
            "categories": categories,
            "total_files": total_files,
            "total_bytes": total_bytes,
        }

    def sweep_local_cache(
        self,
        *,
        older_than_hours: int = 48,
        categories: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """按勾选分类清扫产品处理侧缓存。

        ``categories=None`` 表示全选；显式空列表表示什么都不清。第 1、2 类只
        作用于「没有任何记录引用」的对象；第 3、4 类（复核原图 / 导出表格）
        只清「显式勾选 + 超期」的对应文件。未勾选分类一步都不执行。
        """

        selected: tuple[str, ...] = (
            PP_CACHE_CATEGORY_KEYS if categories is None else tuple(dict.fromkeys(categories))
        )
        unknown = [key for key in selected if key not in PP_CACHE_CATEGORY_KEYS]
        if unknown:
            raise ProductProcessingValidationError(f"未知的缓存清扫分类: {', '.join(unknown)}")

        per_category: dict[str, dict[str, int]] = {
            key: {"files": 0, "bytes": 0} for key in PP_CACHE_CATEGORY_KEYS
        }

        if PP_CACHE_CATEGORY_ASSET_ORPHANS in set(selected):
            freed = self._commit(
                lambda connection: self._sweep_asset_orphans_tx(
                    connection, older_than_hours=older_than_hours
                )
            )
            files, freed_bytes = self._remove_files(freed)
            per_category[PP_CACHE_CATEGORY_ASSET_ORPHANS] = {"files": files, "bytes": freed_bytes}

        if PP_CACHE_CATEGORY_DISK_ORPHANS in set(selected):
            # 行级删除已提交，这里拿到的引用集合是最新的（不会漏删、也不会误删）。
            orphan_paths = self._commit(
                lambda connection: self._disk_orphan_paths_tx(
                    connection, older_than_hours=older_than_hours
                )
            )
            files, freed_bytes = self._remove_files(orphan_paths)
            per_category[PP_CACHE_CATEGORY_DISK_ORPHANS] = {"files": files, "bytes": freed_bytes}

        if PP_CACHE_CATEGORY_REVIEW_IMAGES in set(selected):
            # 有意放在 disk_orphans 之后并独立执行：复核原图是「被 JSON 引用」的，
            # 磁盘对账永远不会碰它们，两分类天然不重叠、不会重复计数。
            review_paths = self._commit(
                lambda connection: self._review_image_paths_tx(connection)
            )
            files, freed_bytes = self._remove_files(review_paths)
            per_category[PP_CACHE_CATEGORY_REVIEW_IMAGES] = {"files": files, "bytes": freed_bytes}

        if PP_CACHE_CATEGORY_TASK_EXPORTS in set(selected):
            # 独立执行：导出表格被列登记（磁盘对账永不碰），只有显式勾选才回收。
            # 与复核原图互斥——后者要求「纯 JSON 留档引用」，本类要求「被列登记」，
            # 同一文件不会同时进两个分类。
            export_paths = self._commit(
                lambda connection: self._task_export_paths_tx(connection)
            )
            files, freed_bytes = self._remove_files(export_paths)
            per_category[PP_CACHE_CATEGORY_TASK_EXPORTS] = {"files": files, "bytes": freed_bytes}

        total_files = sum(item["files"] for item in per_category.values())
        total_bytes = sum(item["bytes"] for item in per_category.values())
        orphan_files = per_category[PP_CACHE_CATEGORY_DISK_ORPHANS]["files"]
        review_files = per_category[PP_CACHE_CATEGORY_REVIEW_IMAGES]["files"]
        export_files = per_category[PP_CACHE_CATEGORY_TASK_EXPORTS]["files"]
        row_files = per_category[PP_CACHE_CATEGORY_ASSET_ORPHANS]["files"]

        logger.info(
            "PP manual cache sweep: files=%d bytes=%d reaped_assets=%d orphans=%d review_images=%d task_exports=%d window_hours=%d categories=%s",
            total_files,
            total_bytes,
            row_files,
            orphan_files,
            review_files,
            export_files,
            int(older_than_hours),
            ",".join(selected),
        )
        return {
            "older_than_hours": int(older_than_hours),
            "categories": per_category,
            "files_removed": total_files,
            "bytes_freed": total_bytes,
            "reaped_assets": row_files,
            "orphan_files_removed": orphan_files,
            "task_exports_removed": export_files,
        }

    # ---- 分类实现 ----

    def _preview_category(self, key: str, *, older_than_hours: int) -> list[str]:
        if key == PP_CACHE_CATEGORY_ASSET_ORPHANS:
            return self._run_in_rollback(
                lambda connection: self._sweep_asset_orphans_tx(
                    connection, older_than_hours=older_than_hours
                )
            )
        if key == PP_CACHE_CATEGORY_DISK_ORPHANS:
            return self._run_in_rollback(
                lambda connection: self._disk_orphan_paths_tx(
                    connection, older_than_hours=older_than_hours
                )
            )
        if key == PP_CACHE_CATEGORY_REVIEW_IMAGES:
            return self._run_in_rollback(
                lambda connection: self._review_image_paths_tx(connection)
            )
        if key == PP_CACHE_CATEGORY_TASK_EXPORTS:
            return self._run_in_rollback(
                lambda connection: self._task_export_paths_tx(connection)
            )
        raise ProductProcessingValidationError(f"未知的缓存清扫分类: {key}")

    def _sweep_asset_orphans_tx(self, connection, *, older_than_hours: int) -> list[str]:
        """媒体库孤儿行：删行；返回「删完全库已无引用、可删文件」的绝对路径。"""

        cutoff = self._cutoff(older_than_hours)
        orphan_rows = connection.execute(
            text(
                """SELECT a.id AS id, a.managed_path AS managed_path
                   FROM product_processing_media_assets AS a
                   WHERE a.managed_path <> ''
                     AND a.created_at < :cutoff
                     AND NOT EXISTS (
                         SELECT 1 FROM product_processing_media_bindings AS b
                         WHERE b.asset_id = a.id
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM product_processing_preview_image_assets AS p
                         WHERE p.media_asset_id = a.id OR p.source_asset_id = a.id
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM product_processing_dimension_assets AS d
                         WHERE d.source_media_asset_id = a.id
                     )"""
            ),
            {"cutoff": cutoff},
        ).fetchall()
        if not orphan_rows:
            return []

        # JSON 列里的提及同样算引用（防御性：id 或路径字符串出现在任何 JSON 里都不碰）。
        _references, _column_references, json_strings = self._collect_reference_data(connection)
        target_rows = [
            row
            for row in orphan_rows
            if str(row[0]) not in json_strings and str(row[1]) not in json_strings
        ]
        if not target_rows:
            return []
        target_ids = [str(row[0]) for row in target_rows]
        candidate_paths = sorted({str(row[1]) for row in target_rows})

        connection.execute(
            text(
                "DELETE FROM product_processing_media_assets WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": target_ids},
        )

        # 删行后重算全库引用：只有确认没有任何记录（含 JSON）再引用的路径才可删文件。
        references_after, _columns_after, _strings = self._collect_reference_data(connection)
        return [path for path in candidate_paths if self._normalize(path) not in references_after]

    def _disk_orphan_paths_tx(self, connection, *, older_than_hours: int) -> list[str]:
        """磁盘 ↔ 数据库反向对账：返回「无任何引用且超窗」的绝对路径。"""

        references, _column_references, _strings = self._collect_reference_data(connection)
        threshold = (
            datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        ).timestamp()
        orphans: list[str] = []
        for path, mtime in self.assets.iter_stored_files():
            if mtime >= threshold:
                continue
            if path.name.endswith(_DISK_SCAN_SKIP_SUFFIXES):
                continue
            if self._normalize(path) in references:
                continue
            orphans.append(str(path))
        return orphans

    def _review_image_paths_tx(self, connection) -> list[str]:
        """历史任务的复核原图：返回「纯 JSON 留档引用 + 命名匹配 + 超 30 天」的路径。

        三个条件全部满足才收（缺一不删）：
        1. 命名含 ``provider_original``（复核原图的既定命名，防误伤其它 JSON 引用）；
        2. **只**被 JSON 提及——凡被 ``*_path`` / ``*_file`` 列引用的说明还有业务
           字段指着它，不碰；
        3. mtime 超过 30 天保留期（比通用 48h 窗口宽，留够人工复核时间）。

        只删文件、不改 JSON 记录（记录里的路径悬空无害——程序没有任何读回
        接口；不动业务数据也符合说明书 §8.5）。
        """

        references, column_references, _strings = self._collect_reference_data(connection)
        threshold = (
            datetime.now(timezone.utc) - timedelta(days=_REVIEW_IMAGE_RETENTION_DAYS)
        ).timestamp()
        review_paths: list[str] = []
        for path, mtime in self.assets.iter_stored_files():
            if mtime >= threshold:
                continue
            if _REVIEW_IMAGE_NAME_MARKER not in path.name:
                continue
            normalized = self._normalize(path)
            if normalized not in references:
                continue  # 无引用的归 disk_orphans，不归本分类（避免重复计数）
            if normalized in column_references:
                continue  # 被业务字段指着 = 在用，绝不删
            review_paths.append(str(path))
        return review_paths

    def _task_export_paths_tx(self, connection) -> list[str]:
        """历史任务的导出表格：返回「命名匹配 + 被列登记 + 超 30 天」的路径。

        三个条件全部满足才收（缺一不删）：
        1. 命名匹配导出文件前缀（店小秘 / 妙手 / 失败报告 / 视频清单，含 token 变体）；
        2. **被** ``*_path`` / ``*_file`` **列精确登记**——只有任务行的
           ``output_file`` 等字段或预检运行行的 ``workbook_path`` 指着的那一份，
           才算「任务的正式导出」；名称相像但没登记的文件不归本分类（无引用的
           杂散文件由 ``pp_disk_orphans`` 常规回收）；
        3. mtime 超过 30 天保留期。

        只删文件、不改记录：删除后任务页/预检页的「下载」会提示文件不存在，
        用户可在任务页重新导出（业务数据全部保留、导出记录不受影响）。
        """

        _references, column_references, _strings = self._collect_reference_data(connection)
        threshold = (
            datetime.now(timezone.utc) - timedelta(days=_TASK_EXPORT_RETENTION_DAYS)
        ).timestamp()
        export_paths: list[str] = []
        for path, mtime in self.assets.iter_stored_files():
            if mtime >= threshold:
                continue
            if not _is_task_export_name(path.name):
                continue
            if self._normalize(path) not in column_references:
                continue
            export_paths.append(str(path))
        return export_paths

    # ---- 引用集合（命名约定，通用收集） ----

    def _iter_reference_columns(self, connection) -> Iterator[tuple[str, str, str]]:
        tables = [
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE 'product_processing%' ORDER BY name"
                )
            )
        ]
        for table in tables:
            for row in connection.execute(text(f"PRAGMA table_info([{table}])")):
                column = str(row[1])
                if column.endswith(("_path", "_file")):
                    yield table, column, "path"
                elif column.endswith("_json"):
                    yield table, column, "json"

    def _collect_reference_data(
        self, connection
    ) -> tuple[set[str], set[str], set[str]]:
        """返回 (全部归一化路径引用, 仅列引用路径, JSON 中的全部字符串集合)。

        「仅列引用」= ``*_path`` / ``*_file`` 列给出的路径（真被业务字段指着、
        属于在用数据）；JSON 提及单独算「留档引用」——只挡 ``pp_disk_orphans``,
        但允许 ``pp_review_images`` 在超期后清理。
        """

        references: set[str] = set()
        column_references: set[str] = set()
        json_strings: set[str] = set()
        for table, column, kind in self._iter_reference_columns(connection):
            rows = connection.execute(
                text(
                    f"SELECT [{column}] FROM [{table}] "
                    f"WHERE [{column}] IS NOT NULL AND [{column}] <> ''"
                )
            )
            for (value,) in rows:
                if kind == "path":
                    raw = str(value)
                    normalized = self._normalize(raw)
                    if normalized:
                        references.add(normalized)
                        column_references.add(normalized)
                        if not os.path.isabs(raw):
                            # 保险绳：万一将来某个列存了相对路径，也按「相对托管根」保护。
                            fallback = self._normalize(self.assets.root / raw)
                            references.add(fallback)
                            column_references.add(fallback)
                else:
                    try:
                        payload = json.loads(value)
                    except (TypeError, ValueError):
                        continue
                    for item in self._json_strings(payload):
                        json_strings.add(item)
                        if os.path.isabs(item):
                            normalized = self._normalize(item)
                            if normalized:
                                references.add(normalized)
        return references, column_references, json_strings

    @classmethod
    def _json_strings(cls, value: Any) -> Iterator[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from cls._json_strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._json_strings(item)

    # ---- 事务 / 文件 / 度量 ----

    def _commit(self, handler: Callable[[Any], list[str]]) -> list[str]:
        with self.database.engine.begin() as connection:
            return handler(connection)

    def _run_in_rollback(self, handler: Callable[[Any], list[str]]) -> list[str]:
        with self.database.engine.connect() as connection:
            transaction = connection.begin()
            try:
                return handler(connection)
            finally:
                transaction.rollback()

    def _remove_files(self, absolute_paths: list[str]) -> tuple[int, int]:
        """逐文件尽力删除（走 assets 的越界保护）；返回 (删除数, 释放字节数)。"""

        files = 0
        total = 0
        for raw in absolute_paths:
            path = Path(raw)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            try:
                self.assets.remove_stored_file(path)
            except Exception:  # noqa: BLE001 —— 单个失败不打断整批
                logger.warning("PP cache sweeper failed to remove %s", raw)
                continue
            files += 1
            total += size
        return files, total

    @staticmethod
    def _measure(absolute_paths: list[str]) -> tuple[int, int]:
        """按删除口径测量（文件缺失计 0，与 ``_remove_files`` 记账一致）。"""

        files = len(absolute_paths)
        total = 0
        for raw in absolute_paths:
            try:
                total += Path(raw).stat().st_size
            except OSError:
                pass
        return files, total

    @staticmethod
    def _cutoff(older_than_hours: int) -> str:
        return (
            datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        ).isoformat(timespec="microseconds")

    @staticmethod
    def _normalize(path: Path | str) -> str:
        try:
            return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))
        except (TypeError, ValueError, OSError):
            return ""
