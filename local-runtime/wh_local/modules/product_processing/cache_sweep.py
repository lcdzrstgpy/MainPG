"""产品处理本地缓存清扫（个人中心「存储空间」的产品处理分区）。

只回收「没有任何数据库记录引用」的本地文件，分两层（与 POD 侧同一套产品语义）：

1. ``pp_asset_orphans``（媒体库无引用图片）：``product_processing_media_assets``
   里从任何地方都找不到引用的行（媒体绑定 / 预览资产 / 尺寸画布 / 任何 JSON 列里
   的 id 提及），且超窗 → 删行；文件确认「全库再无引用」后才删。
2. ``pp_disk_orphans``（无引用遗留文件）：磁盘 ↔ 数据库反向对账——遍历托管根
   目录，删除「归一化绝对路径不在全库引用集合里、且 mtime 超窗」的文件。

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
PP_CACHE_ROW_CATEGORIES = (PP_CACHE_CATEGORY_ASSET_ORPHANS,)
PP_CACHE_CATEGORY_KEYS = PP_CACHE_ROW_CATEGORIES + (PP_CACHE_CATEGORY_DISK_ORPHANS,)

# 反向对账排除的辅助文件：处理期的源图清单（体积可忽略、可再生，保守保留）。
_DISK_SCAN_SKIP_SUFFIXES = ("_source_images.txt",)


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

        ``categories=None`` 表示全选；显式空列表表示什么都不清。删行/删文件
        只作用于「没有任何记录引用」的对象，未勾选分类一步都不执行。
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

        total_files = sum(item["files"] for item in per_category.values())
        total_bytes = sum(item["bytes"] for item in per_category.values())
        orphan_files = per_category[PP_CACHE_CATEGORY_DISK_ORPHANS]["files"]

        logger.info(
            "PP manual cache sweep: files=%d bytes=%d reaped_assets=%d orphans=%d window_hours=%d categories=%s",
            total_files,
            total_bytes,
            total_files - orphan_files,
            orphan_files,
            int(older_than_hours),
            ",".join(selected),
        )
        return {
            "older_than_hours": int(older_than_hours),
            "categories": per_category,
            "files_removed": total_files,
            "bytes_freed": total_bytes,
            "reaped_assets": total_files - orphan_files,
            "orphan_files_removed": orphan_files,
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
        _references, json_strings = self._collect_reference_data(connection)
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
        references_after, _strings = self._collect_reference_data(connection)
        return [path for path in candidate_paths if self._normalize(path) not in references_after]

    def _disk_orphan_paths_tx(self, connection, *, older_than_hours: int) -> list[str]:
        """磁盘 ↔ 数据库反向对账：返回「无任何引用且超窗」的绝对路径。"""

        references, _strings = self._collect_reference_data(connection)
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

    def _collect_reference_data(self, connection) -> tuple[set[str], set[str]]:
        """返回 (归一化绝对路径集合, JSON 中的全部字符串集合)。"""

        references: set[str] = set()
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
                        if not os.path.isabs(raw):
                            # 保险绳：万一将来某个列存了相对路径，也按「相对托管根」保护。
                            references.add(self._normalize(self.assets.root / raw))
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
        return references, json_strings

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
