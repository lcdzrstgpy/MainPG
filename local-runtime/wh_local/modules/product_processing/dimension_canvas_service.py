from __future__ import annotations

import hashlib
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import ValidationError

from .domain.policy import is_safe_external_url
from .domain.physical_dimensions import PhysicalDimensions
from .infrastructure.assets import ProductProcessingAssets
from .infrastructure.dimension_canvas_repository import (
    CanvasStateConflict,
    DimensionCanvasRepository,
    StaleCanvasRevision,
)
from .infrastructure.dimension_renderer import DimensionAnnotation, DimensionRenderRequest, DimensionRenderer
from .infrastructure.repository import ProductProcessingRepository


class DimensionCanvasNotFound(LookupError):
    pass


class DimensionCanvasConflict(RuntimeError):
    pass


class DimensionCanvasService:
    """Local editing/rendering plus explicit public handoff for dimension images."""

    def __init__(
        self,
        canvas_repository: DimensionCanvasRepository,
        product_repository: ProductProcessingRepository,
        assets: ProductProcessingAssets,
        renderer: DimensionRenderer,
        source_loader: Callable[[dict[str, Any]], bytes] | None = None,
        publisher: Callable[[bytes, int, int, int, str, str], dict[str, Any]] | None = None,
        *,
        max_workers: int = 3,
    ):
        if max_workers < 1 or max_workers > 3:
            raise ValueError("dimension render max_workers must be between 1 and 3")
        self.canvas_repository = canvas_repository
        self.product_repository = product_repository
        self.assets = assets
        self.renderer = renderer
        self.source_loader = source_loader or self._load_local_source
        # Kept as a compatibility-only constructor argument for older composition
        # roots. Dimension images stay local through review/acceptance; COS is used
        # only by the final precheck finalizer.
        _ = publisher
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="dimension-canvas",
        )
        self._futures: dict[str, Future[None]] = {}
        self._futures_lock = threading.Lock()
        self.canvas_repository.recover_rendering_items()

    def import_preview_item(
        self,
        task_id: int,
        task_item_id: int,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        try:
            batch = self.canvas_repository.import_task_items(task_id, [task_item_id], workspace_id)
        except LookupError as exc:
            raise DimensionCanvasNotFound(str(exc)) from exc
        return self._hydrate_item(batch["items"][0], workspace_id)

    def import_task(
        self,
        task_id: int,
        task_item_ids: list[int],
        *,
        existing_dimension_actions: dict[str, Literal["keep", "remake", "skip"]] | None = None,
        workspace_id: str,
    ) -> dict[str, Any]:
        actions = existing_dimension_actions or {}
        task_item_ids = [
            item_id
            for item_id in task_item_ids
            if actions.get(str(item_id), "remake") == "remake"
        ]
        if not task_item_ids:
            raise ValueError("no products selected for dimension canvas import")
        try:
            batch = self.canvas_repository.import_task_items(task_id, task_item_ids, workspace_id)
        except LookupError as exc:
            raise DimensionCanvasNotFound(str(exc)) from exc
        batch["items"] = [self._hydrate_item(item, workspace_id) for item in batch["items"]]
        return batch

    def importable_tasks(self, *, workspace_id: str) -> list[dict[str, Any]]:
        allowed = {"completed", "failed", "partial_failure", "completed_with_attention"}
        tasks, _total = self.product_repository.list_tasks(200, workspace_id)
        return [
            {
                "id": task["id"],
                "title": task.get("title", ""),
                "status": task.get("status", ""),
                "total_count": task.get("total_count", len(task.get("items") or [])),
                "created_at": task.get("created_at", ""),
            }
            for task in tasks
            if task.get("status") in allowed
        ]

    def task_eligibility(self, task_id: int, *, workspace_id: str) -> dict[str, list[dict[str, Any]]]:
        task = self.product_repository.get_task(task_id, workspace_id)
        if task is None:
            raise DimensionCanvasNotFound("product processing task not found")
        groups: dict[str, list[dict[str, Any]]] = {
            "ready": [],
            "needs_dimensions": [],
            "existing_dimension": [],
            "asset_failed": [],
        }
        for item in task.get("items") or []:
            if item.get("status") != "completed" or not item.get("product_draft_id"):
                continue
            item_id = int(item["id"])
            eligibility_item = {
                "task_item_id": item_id,
                "skc": str(item.get("skc") or ""),
                "label": str(item.get("title") or item.get("skc") or f"商品 #{item_id}"),
            }
            result = item.get("result") or {}
            dimensions = result.get("physical_dimensions") or {}
            manifest = result.get("image_manifest") or []
            values = [entry.get("value") for entry in manifest if isinstance(entry, dict) and entry.get("value")]
            if not values:
                values = [value for value in result.get("carousel_image_paths") or [] if value]
            if not values and not item.get("image_url"):
                groups["asset_failed"].append(eligibility_item)
                continue
            if any(
                isinstance(entry, dict)
                and entry.get("slot_id") == "carousel.dimension_background"
                and entry.get("role") in {"dimension_slot", "rendered_dimension"}
                for entry in manifest
            ):
                groups["existing_dimension"].append(eligibility_item)
                continue
            try:
                parsed = PhysicalDimensions.model_validate(dimensions)
            except ValidationError:
                parsed = PhysicalDimensions()
            groups["ready" if parsed.drawable else "needs_dimensions"].append(eligibility_item)
        return groups

    def list_batches(self, *, workspace_id: str) -> list[dict[str, Any]]:
        return self.canvas_repository.list_batches(workspace_id)

    def get_batch(self, batch_id: str, *, workspace_id: str) -> dict[str, Any]:
        batch = self.canvas_repository.get_batch(batch_id, workspace_id)
        if batch is None:
            raise DimensionCanvasNotFound("dimension canvas batch not found")
        batch["items"] = [self._hydrate_item(item, workspace_id) for item in batch["items"]]
        return batch

    def get_item(self, item_id: str, *, workspace_id: str) -> dict[str, Any]:
        item = self.canvas_repository.get_item(item_id, workspace_id)
        if item is None:
            raise DimensionCanvasNotFound("dimension canvas item not found")
        return self._hydrate_item(item, workspace_id)

    def save_item(
        self,
        item_id: str,
        expected_revision: int,
        patch: dict[str, Any],
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        cleaned = self._validate_save_patch(patch)
        try:
            saved = self.canvas_repository.save_item(
                item_id,
                expected_revision,
                cleaned,
                workspace_id,
            )
        except (StaleCanvasRevision, CanvasStateConflict) as exc:
            raise DimensionCanvasConflict(str(exc)) from exc
        except LookupError as exc:
            raise DimensionCanvasNotFound(str(exc)) from exc
        return self._hydrate_item(saved, workspace_id)

    def upload_asset(
        self,
        item_id: str,
        content: bytes,
        filename: str,
        content_type: str,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        """Validate and register an uploaded image without touching editor revision."""

        if self.canvas_repository.get_item(item_id, workspace_id) is None:
            raise DimensionCanvasNotFound("dimension canvas item not found")
        info = self.renderer.inspect_source(bytes(content))
        digest = hashlib.sha256(content).hexdigest()
        path = self.assets.save_dimension_asset(
            content,
            kind="source",
            suffix=info.suffix,
            workspace_id=workspace_id,
        )
        asset = self.canvas_repository.register_uploaded_asset(
            item_id,
            workspace_id,
            managed_path=str(path),
            content_hash=digest,
            width=info.width,
            height=info.height,
            content_type=info.content_type,
        )
        item = self.canvas_repository.get_item(item_id, workspace_id)
        assert item is not None
        return {
            "item": self._hydrate_item(item, workspace_id),
            "asset_id": asset["id"],
            "filename": Path(filename or "uploaded-image").name,
        }

    def complete_item(
        self,
        item_id: str,
        expected_revision: int,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        raw_item = self.canvas_repository.get_item(item_id, workspace_id)
        if raw_item is None:
            raise DimensionCanvasNotFound("dimension canvas item not found")
        self._materialize_selected_asset(raw_item, workspace_id)
        item = self.get_item(item_id, workspace_id=workspace_id)
        self._validate_complete(item)
        try:
            rendering = self.canvas_repository.mark_rendering(
                item_id,
                expected_revision,
                workspace_id,
            )
        except StaleCanvasRevision as exc:
            raise DimensionCanvasConflict(str(exc)) from exc
        except (LookupError, CanvasStateConflict) as exc:
            if isinstance(exc, LookupError):
                raise DimensionCanvasNotFound(str(exc)) from exc
            raise DimensionCanvasConflict(str(exc)) from exc
        future = self.executor.submit(
            self._render_item,
            item_id,
            int(rendering["render_revision"]),
            workspace_id,
        )
        with self._futures_lock:
            self._futures[item_id] = future
        return self._hydrate_item(rendering, workspace_id)

    def retry_render(
        self,
        item_id: str,
        expected_revision: int | None,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        item = self.get_item(item_id, workspace_id=workspace_id)
        if item["state"] not in {"render_retryable", "editing", "completed"}:
            raise DimensionCanvasConflict("dimension item is not retryable")
        revision = int(item["item_revision"] if expected_revision is None else expected_revision)
        return self.complete_item(item_id, revision, workspace_id=workspace_id)

    def submit_review(self, batch_id: str, *, workspace_id: str) -> dict[str, Any]:
        batch = self.canvas_repository.get_batch(batch_id, workspace_id)
        if batch is None:
            raise DimensionCanvasNotFound("dimension canvas batch not found")
        completed = [item for item in batch["items"] if item["state"] == "completed"]
        if not completed:
            raise ValueError("batch has no completed dimension items")
        identities: list[str] = []
        for item in sorted(completed, key=lambda value: value["id"]):
            draft = self.product_repository.get_draft(
                int(item["product_draft_id"]),
                workspace_id=workspace_id,
            )
            if draft is None:
                raise DimensionCanvasNotFound("product draft not found")
            if int(draft.get("preview_revision") or 0) != int(item.get("source_preview_revision") or 0):
                raise DimensionCanvasConflict("商品预检图在导入尺寸画布后已更新，请重新导入后再交回")
            asset = self.canvas_repository.get_asset(
                item["render_asset_id"],
                item["id"],
                workspace_id,
            )
            if asset is None or asset.get("availability") not in {"local", "published"}:
                raise DimensionCanvasConflict("rendered asset not found")
            identities.append(
                f"{item['id']}:{item['render_revision']}:{asset.get('content_hash') or asset['id']}"
            )
        idempotency_key = hashlib.sha256("|".join(identities).encode("utf-8")).hexdigest()
        try:
            result = self.canvas_repository.create_change_set(
                batch_id,
                [item["id"] for item in completed],
                idempotency_key,
                workspace_id,
            )
            return self._sanitize_change_set(result)
        except LookupError as exc:
            raise DimensionCanvasNotFound(str(exc)) from exc
        except CanvasStateConflict as exc:
            raise DimensionCanvasConflict(str(exc)) from exc

    def get_change_set(self, change_set_id: str, *, workspace_id: str) -> dict[str, Any]:
        result = self.canvas_repository.get_change_set(change_set_id, workspace_id)
        if result is None:
            raise DimensionCanvasNotFound("dimension change set not found")
        return self._sanitize_change_set(result)

    def accept_change_set(self, change_set_id: str, *, workspace_id: str) -> dict[str, Any]:
        change_set = self.get_change_set(change_set_id, workspace_id=workspace_id)
        for item in change_set.get("items") or []:
            if item["status"] != "pending":
                continue
            try:
                self._accept_local_change(item["id"], workspace_id)
            except CanvasStateConflict:
                # A concurrent revision is a visible conflict, never an overwrite.
                continue
        return self.get_change_set(change_set_id, workspace_id=workspace_id)

    def accept_change_item(
        self,
        change_set_id: str,
        change_item_id: str,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        change_set = self.get_change_set(change_set_id, workspace_id=workspace_id)
        if change_item_id not in {item["id"] for item in change_set.get("items") or []}:
            raise DimensionCanvasNotFound("dimension change item not found")
        try:
            return self._sanitize_change_item(
                self._accept_local_change(change_item_id, workspace_id)
            )
        except LookupError as exc:
            raise DimensionCanvasNotFound(str(exc)) from exc
        except CanvasStateConflict as exc:
            raise DimensionCanvasConflict(str(exc)) from exc

    def _accept_local_change(self, change_item_id: str, workspace_id: str) -> dict[str, Any]:
        raw = self.canvas_repository.get_change_item(change_item_id, workspace_id)
        if raw is None:
            raise LookupError("dimension change item not found")
        replacement = dict(raw.get("replacement_asset") or {})
        managed_path = str(replacement.get("managed_path") or "")
        content_hash = str(replacement.get("content_hash") or "")
        content_type = str(replacement.get("content_type") or "image/jpeg")
        preview_path = self.assets.import_dimension_as_preview_asset(
            managed_path,
            workspace_id=workspace_id,
            content_hash=content_hash,
            content_type=content_type,
        )
        return self.canvas_repository.accept_change_item(
            change_item_id,
            workspace_id,
            preview_managed_path=str(preview_path),
        )

    def reject_change_item(
        self,
        change_set_id: str,
        change_item_id: str,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        change_set = self.get_change_set(change_set_id, workspace_id=workspace_id)
        if change_item_id not in {item["id"] for item in change_set.get("items") or []}:
            raise DimensionCanvasNotFound("dimension change item not found")
        try:
            return self._sanitize_change_item(
                self.canvas_repository.reject_change_item(change_item_id, workspace_id)
            )
        except LookupError as exc:
            raise DimensionCanvasNotFound(str(exc)) from exc

    def list_notifications(self, *, workspace_id: str, after: str = "") -> list[dict[str, Any]]:
        return self.canvas_repository.list_notifications(workspace_id, after)

    def mark_notification_read(self, notification_id: str, *, workspace_id: str) -> dict[str, Any]:
        try:
            return self.canvas_repository.mark_notification_read(notification_id, workspace_id)
        except LookupError as exc:
            raise DimensionCanvasNotFound(str(exc)) from exc

    def wait_for_test_render(
        self,
        item_id: str,
        *,
        workspace_id: str,
        timeout: float = 10,
    ) -> dict[str, Any]:
        with self._futures_lock:
            future = self._futures.get(item_id)
        if future is not None:
            future.result(timeout=timeout)
        item = self.canvas_repository.get_item(item_id, workspace_id)
        if item is None:
            raise DimensionCanvasNotFound("dimension canvas item not found")
        return item

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    def _render_item(self, item_id: str, render_revision: int, workspace_id: str) -> None:
        try:
            item = self.canvas_repository.get_item(item_id, workspace_id)
            if item is None or item["state"] != "rendering" or item["render_revision"] != render_revision:
                return
            asset = self.canvas_repository.get_asset(
                item["selected_source_asset_id"],
                item_id,
                workspace_id,
            )
            if asset is None:
                raise ValueError("selected source asset not found")
            source_bytes = self.assets.require_workspace_dimension_asset(
                str(asset.get("managed_path") or ""),
                workspace_id=workspace_id,
            ).read_bytes()
            request = DimensionRenderRequest(
                source_bytes=source_bytes,
                annotations=[self._renderer_annotation(value) for value in item["annotations"]],
                fit=str(item.get("canvas_settings", {}).get("fit") or "contain"),
            )
            output = self.renderer.render(request)
            master_path = self.assets.save_dimension_asset(
                output.master_png_bytes,
                kind="master",
                suffix=".png",
                workspace_id=workspace_id,
            )
            output_path = self.assets.save_dimension_asset(
                output.jpeg_bytes,
                kind="published",
                suffix=".jpg",
                workspace_id=workspace_id,
            )
            rendered_asset = {
                "source_url": "",
                "managed_path": str(output_path),
                "content_hash": str(output.content_hash),
                "width": int(output.width),
                "height": int(output.height),
                "content_type": "image/jpeg",
                "availability": "local",
                "master_path": str(master_path),
            }
            self.canvas_repository.finish_render(
                item_id,
                render_revision,
                rendered_asset,
                workspace_id,
            )
        except Exception as exc:  # worker boundary: persist a bounded, retryable error
            self.canvas_repository.fail_render(
                item_id,
                render_revision,
                "dimension_render_failed",
                str(exc) or type(exc).__name__,
                workspace_id,
            )

    def _hydrate_item(self, item: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        result = {key: value for key, value in item.items() if key != "workspace_id"}
        result["assets"] = [
            {
                key: value
                for key, value in {
                    **asset,
                    "preview_url": self._asset_preview_url(
                        asset,
                        product_draft_id=int(item.get("product_draft_id") or 0),
                    ),
                }.items()
                if key not in {"workspace_id", "managed_path"}
            }
            for asset in self.canvas_repository.list_assets(item["id"], workspace_id)
        ]
        return result

    def _sanitize_change_set(self, change_set: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in change_set.items() if key != "workspace_id"}
        result["items"] = [self._sanitize_change_item(item) for item in change_set.get("items") or []]
        return result

    def _sanitize_change_item(self, item: dict[str, Any]) -> dict[str, Any]:
        base = dict(item.get("base_asset") or {})
        replacement = dict(item.get("replacement_asset") or {})
        old_url = self._safe_preview_value(str((base.get("slot") or {}).get("url") or ""))
        new_url = self._asset_preview_url(
            replacement,
            product_draft_id=int(item.get("product_draft_id") or 0),
        )
        return {
            **{key: value for key, value in item.items() if key not in {"workspace_id", "base_asset", "replacement_asset"}},
            "old_image_url": old_url,
            "base_asset_url": old_url,
            "new_image_url": new_url,
            "replacement_asset_url": new_url,
            "replacement_asset_id": str(replacement.get("id") or ""),
            "replacement_content_hash": str(replacement.get("content_hash") or ""),
        }

    def _asset_preview_url(self, asset: dict[str, Any], *, product_draft_id: int = 0) -> str:
        # 本地已落盘文件最可靠：优先走 /pp-media 静态代理，避免浏览器直连外部图床
        # 遭遇防盗链（如 1688 alicdn 偶发 420）导致空白或一直加载。
        managed_path = str(asset.get("managed_path") or "").strip()
        if managed_path:
            path = Path(managed_path).resolve()
            output_root = self.assets.output_root.resolve()
            if output_root == path or output_root in path.parents:
                relative = path.relative_to(output_root).as_posix()
                return f"/pp-media/{relative}"
        source_url = str(asset.get("source_url") or "").strip()
        asset_id = str(asset.get("id") or "").strip()
        if asset_id and source_url and self._is_public_dimension_url(source_url):
            # 外部图床统一走后端同源代理（SSRF 校验 + 后端下载），浏览器不直连外部域名。
            return f"/api/product-processing/dimension-assets/{asset_id}/image"
        if product_draft_id > 0 and str(asset.get("role") or "") in {"source", "task_source"}:
            return f"/api/product-processing/drafts/{product_draft_id}/image"
        return ""

    def dimension_asset_image_path(self, asset_id: str, *, workspace_id: str) -> Path:
        """Resolve one canvas asset to a local image file, downloading external URLs on demand.

        Serves as the browser-facing image proxy: remote 1688/other CDN sources are fetched
        server-side (SSRF-safe, size-limited) and cached under the workspace namespace, so the
        browser never hits external hotlink protection directly.
        """
        asset = self.canvas_repository.find_asset(asset_id, workspace_id)
        if asset is None:
            raise DimensionCanvasNotFound("dimension canvas asset not found")
        managed_path = str(asset.get("managed_path") or "").strip()
        if managed_path:
            return self.assets.require_workspace_dimension_asset(managed_path, workspace_id=workspace_id)
        source_url = str(asset.get("source_url") or "").strip()
        if not source_url or not self._is_public_dimension_url(source_url):
            raise DimensionCanvasNotFound("dimension canvas asset is not fetchable")
        content = self.source_loader(dict(asset))
        if not content:
            raise DimensionCanvasNotFound("dimension canvas asset download returned empty content")
        suffix = ".png" if str(asset.get("content_type") or "").lower() == "image/png" else ".jpg"
        path = self.assets.save_dimension_asset(
            content,
            kind="source",
            suffix=suffix,
            workspace_id=workspace_id,
        )
        digest = hashlib.sha256(content).hexdigest()
        self.canvas_repository.materialize_asset(
            str(asset.get("id") or ""),
            str(asset.get("item_id") or ""),
            workspace_id,
            managed_path=str(path),
            content_hash=digest,
            content_type=str(asset.get("content_type") or ""),
        )
        return path

    @staticmethod
    def _is_public_dimension_url(value: str) -> bool:
        return str(value or "").strip().lower().startswith("https://") and is_safe_external_url(value)

    @staticmethod
    def _safe_preview_value(value: str) -> str:
        normalized = str(value or "").strip()
        if normalized.startswith("/pp-media/"):
            return normalized
        return normalized if is_safe_external_url(normalized) else ""

    def _load_local_source(self, asset: dict[str, Any]) -> bytes:
        managed_path = str(asset.get("managed_path") or "")
        if not managed_path:
            raise ValueError("source asset is metadata-only; materialize it before rendering")
        return self.assets.require_managed_file(managed_path).read_bytes()

    def _materialize_selected_asset(self, item: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        asset_id = str(item.get("selected_source_asset_id") or "")
        if not asset_id:
            raise ValueError("selected source asset is required")
        asset = self.canvas_repository.get_asset(asset_id, item["id"], workspace_id)
        if asset is None:
            raise DimensionCanvasNotFound("dimension canvas asset not found")
        if asset.get("managed_path") and asset.get("content_hash"):
            self.assets.require_workspace_dimension_asset(
                str(asset["managed_path"]),
                workspace_id=workspace_id,
            )
            return asset
        content = self.source_loader(dict(asset))
        if not content:
            raise ValueError("source asset materialization returned empty content")
        digest = hashlib.sha256(content).hexdigest()
        suffix = ".png" if str(asset.get("content_type") or "").lower() == "image/png" else ".jpg"
        path = self.assets.save_dimension_asset(
            content,
            kind="source",
            suffix=suffix,
            workspace_id=workspace_id,
        )
        return self.canvas_repository.materialize_asset(
            asset_id,
            item["id"],
            workspace_id,
            managed_path=str(path),
            content_hash=digest,
            width=int(asset.get("width") or 0),
            height=int(asset.get("height") or 0),
            content_type=str(asset.get("content_type") or ("image/png" if suffix == ".png" else "image/jpeg")),
        )

    @staticmethod
    def _validate_save_patch(patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "selected_source_asset_id",
            "target_slot_id",
            "physical_dimensions",
            "annotations",
            "canvas_settings",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"unsupported canvas fields: {sorted(unknown)}")
        cleaned = dict(patch)
        if "physical_dimensions" in cleaned:
            cleaned["physical_dimensions"] = PhysicalDimensions.model_validate(
                cleaned["physical_dimensions"] or {}
            ).model_dump(mode="json")
        if "annotations" in cleaned:
            annotations = cleaned["annotations"] or []
            if not isinstance(annotations, list):
                raise ValueError("annotations must be a list")
            cleaned["annotations"] = [DimensionCanvasService._normalize_annotation(value) for value in annotations]
        if "canvas_settings" in cleaned and not isinstance(cleaned["canvas_settings"], dict):
            raise ValueError("canvas_settings must be an object")
        if "canvas_settings" in cleaned:
            settings = dict(cleaned["canvas_settings"] or {})
            unknown_settings = set(settings) - {"fit", "style", "display_unit", "custom_value_cm"}
            if unknown_settings:
                raise ValueError(f"unsupported canvas settings: {sorted(unknown_settings)}")
            if str(settings.get("fit") or "contain") not in {"contain", "cover"}:
                raise ValueError("canvas fit is invalid")
            if str(settings.get("style") or "auto") not in {"auto", "dark", "light"}:
                raise ValueError("canvas style is invalid")
            if str(settings.get("display_unit") or "cm") not in {"cm", "mm", "in", "ft"}:
                raise ValueError("canvas display unit is invalid")
            custom_value = settings.get("custom_value_cm")
            if custom_value is not None and float(custom_value) <= 0:
                raise ValueError("custom dimension value must be positive")
            cleaned["canvas_settings"] = settings
        if "target_slot_id" in cleaned:
            slot = str(cleaned["target_slot_id"] or "")
            if slot and slot != "carousel.dimension_background":
                raise ValueError("target_slot_id must be the dimension background slot")
            cleaned["target_slot_id"] = slot
        return cleaned

    @staticmethod
    def _normalize_annotation(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("annotation must be an object")
        key = str(value.get("key") or "")
        if key not in {"length", "width", "height", "custom"}:
            raise ValueError("annotation key is invalid")
        normalized = {
            "id": str(value.get("id") or ""),
            "key": key,
            "value_cm": float(value.get("value_cm")),
            "start": DimensionCanvasService._point(value.get("start"), "start"),
            "end": DimensionCanvasService._point(value.get("end"), "end"),
            "label": DimensionCanvasService._point(value.get("label"), "label"),
            "style": str(value.get("style") or "auto"),
            "unit": str(value.get("unit") or "cm"),
        }
        # Run the actual renderer contract at the API boundary too.
        DimensionCanvasService._renderer_annotation(normalized)
        return normalized

    @staticmethod
    def _point(value: Any, name: str) -> dict[str, float]:
        if isinstance(value, dict):
            x, y = value.get("x"), value.get("y")
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            x, y = value
        else:
            raise ValueError(f"annotation {name} must be a point")
        point = {"x": float(x), "y": float(y)}
        if not (0 <= point["x"] <= 1 and 0 <= point["y"] <= 1):
            raise ValueError(f"annotation {name} must be normalized")
        return point

    @staticmethod
    def _renderer_annotation(value: dict[str, Any]) -> DimensionAnnotation:
        return DimensionAnnotation(
            key=value["key"],
            value_cm=float(value["value_cm"]),
            start=(float(value["start"]["x"]), float(value["start"]["y"])),
            end=(float(value["end"]["x"]), float(value["end"]["y"])),
            label=(float(value["label"]["x"]), float(value["label"]["y"])),
            style=value.get("style") or "auto",
            unit=value.get("unit") or "cm",
        )

    def _validate_complete(self, item: dict[str, Any]) -> None:
        selected = str(item.get("selected_source_asset_id") or "")
        assets = {asset["id"]: asset for asset in item.get("assets") or []}
        if selected not in assets:
            raise ValueError("selected source asset is required")
        if not str(item.get("target_slot_id") or ""):
            raise ValueError("target slot is required")
        dimensions = PhysicalDimensions.model_validate(item.get("physical_dimensions") or {})
        if dimensions.conflict:
            raise ValueError("physical dimensions are conflicting")
        annotations = item.get("annotations") or []
        if not annotations:
            raise ValueError("at least one dimension annotation is required")
        allowed = {"source_confirmed", "manual_confirmed"}
        for annotation in annotations:
            normalized = self._normalize_annotation(annotation)
            if normalized["key"] == "custom":
                continue
            dimension = getattr(dimensions, normalized["key"])
            if dimension.value_cm is None or dimension.value_cm <= 0 or dimension.provenance not in allowed:
                raise ValueError(f"{normalized['key']} dimension is not confirmed")
            if abs(float(dimension.value_cm) - float(normalized["value_cm"])) > 1e-9:
                raise ValueError(f"{normalized['key']} annotation is stale")
