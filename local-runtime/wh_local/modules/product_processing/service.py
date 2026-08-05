from __future__ import annotations

import importlib.util
import hashlib
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wh_local.data_collection.public_image_fetch import FetchedPublicImage, fetch_public_image

from .domain.handoff import candidate_from_handoff
from .domain.models import DEFAULT_PROMPTS, DailySelectionHandoffEnvelope, DailySelectionRun
from .domain.workbooks import read_product_workbook
from .infrastructure.assets import ProductProcessingAssets
from .infrastructure.repository import ProductProcessingRepository


class ProductProcessingNotFound(LookupError):
    pass


class ProductProcessingConflict(RuntimeError):
    pass


class ProductProcessingService:
    def __init__(
        self,
        repository: ProductProcessingRepository,
        assets: ProductProcessingAssets,
        public_image_fetcher: Callable[[str], FetchedPublicImage] = fetch_public_image,
    ):
        self.repository = repository
        self.assets = assets
        self._public_image_fetcher = public_image_fetcher

    def engine_status(self) -> dict[str, Any]:
        dependency_status = {
            "openpyxl": importlib.util.find_spec("openpyxl") is not None,
            "python_multipart": importlib.util.find_spec("multipart") is not None,
            "pillow": importlib.util.find_spec("PIL") is not None,
            "opencv": importlib.util.find_spec("cv2") is not None,
            "rapidocr": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
        }
        # The local fallback keeps the screen operational without external AI keys.
        # Integrators can replace it behind this service without changing the API.
        config = {
            "ai_provider": "local-deterministic",
            "ai_model": "product-processing-local-v1",
            "ai_configured": True,
            "backup_ai_configured": False,
            "image_provider": "local-source-pass-through",
            "image_model": "source-image-preservation-v1",
            "image_configured": True,
            "backup_image_configured": False,
            "cos_configured": False,
            "cos_upload_prefix": "product-processing",
        }
        return {
            "available": True,
            "ready": dependency_status["openpyxl"] and dependency_status["python_multipart"],
            "app_dir": str(Path(__file__).parent),
            "app_file": str(Path(__file__)),
            "python": sys.executable,
            "worker": "local-synchronous-v1",
            "message": "本地产品处理引擎已就绪；外部 AI/COS 可由系统配置模块后续替换。",
            "diagnostics": {
                "config": config,
                "tenant_ai_capability": {"text": True, "image": True, "mode": "local_fallback"},
                "dependencies": dependency_status,
                "storage_root": str(self.assets.root),
            },
        }

    def prompts(self) -> dict[str, Any]:
        custom = self.repository.prompts()
        prompts = {
            key: {
                "key": key,
                "custom": custom.get(key, ""),
                "default": default,
                "effective": custom.get(key) or default,
            }
            for key, default in DEFAULT_PROMPTS.items()
        }
        return {"prompts": prompts, "config": self.engine_status()["diagnostics"]["config"]}

    def update_prompts(self, prompts: dict[str, str]) -> dict[str, Any]:
        unknown = set(prompts) - set(DEFAULT_PROMPTS)
        if unknown:
            raise ValueError(f"unsupported prompt keys: {', '.join(sorted(unknown))}")
        self.repository.save_prompts({key: str(value or "").strip() for key, value in prompts.items()})
        return {**self.prompts(), "message": "产品处理提示词已保存"}

    def reset_prompts(self) -> dict[str, Any]:
        self.repository.reset_prompts()
        return {**self.prompts(), "message": "产品处理提示词已恢复默认值"}

    def create_draft(
        self,
        payload: dict[str, Any],
        *,
        selection_run_id: str | None = None,
        workspace_id: str = "local",
        handoff_id: str | None = None,
        handoff_idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        raw = dict(payload)
        candidate_id = self._text(raw.get("candidate_id")) or None
        existing = self.repository.draft_by_candidate(candidate_id or "", workspace_id)
        if existing and existing["status"] != "deleted":
            return existing, False
        title = self._text(raw.get("title") or raw.get("source_title") or raw.get("product_name"))
        product_name = self._text(raw.get("product_name") or title)
        image_url = self._text(
            raw.get("image_url")
            or raw.get("main_image_url")
            or self._first(raw.get("source_image_urls"))
        )
        source_ref = self._text(
            raw.get("source_ref")
            or raw.get("source_url")
            or raw.get("product_link")
            or candidate_id
            or raw.get("offer_id")
        )
        cost = self._number(raw.get("cost") if raw.get("cost") is not None else raw.get("price_cny"))
        declared_price = self._number(raw.get("declared_price"))
        source_type = self._text(raw.get("source_type")) or (
            "daily_selection" if selection_run_id is not None else "manual"
        )
        values = {
            "workspace_id": workspace_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "candidate_id": candidate_id,
            "selection_run_id": selection_run_id,
            "handoff_id": handoff_id,
            "handoff_idempotency_key": handoff_idempotency_key,
            "skc": self._text(raw.get("skc")) or None,
            "sku": self._text(raw.get("sku")) or None,
            "product_name": product_name,
            "title": title,
            "description": self._text(raw.get("description")),
            "image_url": image_url,
            "image_path": self._text(raw.get("image_path")),
            "cost": cost,
            "declared_price": declared_price,
            "status": "draft",
            "raw_payload_json": self._json(raw),
        }
        if existing is not None:
            values.pop("raw_payload_json")
            revived = self.repository.update_draft(
                existing["id"],
                values,
                raw,
                workspace_id=workspace_id,
            )
            if revived is None:
                raise ProductProcessingNotFound("product draft not found")
            self._seed_draft_source_images(revived, raw)
            return revived, True
        draft = self.repository.create_draft(values)
        self._seed_draft_source_images(draft, raw)
        return draft, True

    def demo_draft(self, workspace_id: str = "local") -> dict[str, Any]:
        draft, created = self.create_draft(
            {
                "source_type": "demo",
                "candidate_id": "local-demo:product-processing",
                "source_ref": "local-demo",
                "title": "本地演示商品",
                "product_name": "本地演示商品",
                "category": "家居",
                "image_url": "https://example.invalid/product-processing-demo.jpg",
                "price_cny": 8.5,
                "source_platform": "local-demo",
                "source_image_urls": ["https://example.invalid/product-processing-demo.jpg"],
            },
            workspace_id=workspace_id,
        )
        return {"draft": draft, "created": created, "message": "本地演示草稿已准备完成"}

    def get_draft(self, draft_id: int, workspace_id: str = "local") -> dict[str, Any]:
        draft = self.repository.get_draft(draft_id, workspace_id=workspace_id)
        if draft is None:
            raise ProductProcessingNotFound("product draft not found")
        return draft

    def list_drafts(
        self,
        status: str | None,
        limit: int,
        offset: int,
        *,
        summary: bool,
        selection_run_id: str | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        drafts, has_more = self.repository.list_drafts(
            status,
            limit,
            offset,
            selection_run_id=selection_run_id,
            workspace_id=workspace_id,
        )
        if summary:
            drafts = [self._draft_summary(draft) for draft in drafts]
        return {
            "drafts": drafts,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(drafts),
                "has_more": has_more,
                "view": "summary" if summary else "full",
            },
        }

    def update_draft(
        self,
        draft_id: int,
        payload: dict[str, Any],
        *,
        allow_image_path: bool = False,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        current = self.get_draft(draft_id, workspace_id)
        raw = dict(current["raw_payload"])
        payload = dict(payload)
        if "title" in payload and not self._text(payload.get("title")):
            raise ValueError("商品标题不能为空")
        if not allow_image_path:
            payload.pop("image_path", None)
        fields: dict[str, Any] = {}
        direct_fields = {
            "source_ref",
            "skc",
            "sku",
            "product_name",
            "title",
            "description",
            "image_url",
            "image_path",
            "cost",
            "declared_price",
            "status",
        }
        for key in direct_fields:
            if key in payload:
                value = payload[key]
                if key in {"cost", "declared_price"}:
                    value = self._number(value)
                elif key not in {"skc", "sku"}:
                    value = self._text(value)
                fields[key] = value
        if "main_image_url" in payload and "image_url" not in fields:
            fields["image_url"] = self._text(payload["main_image_url"])
        self._apply_sku_changes(raw, payload.get("sku_name_edits"), payload.get("sku_name_deletes"))
        for key, value in payload.items():
            if key not in {"status", "sku_name_edits", "sku_name_deletes"}:
                raw[key] = value
        updated = self.repository.update_draft(draft_id, fields, raw, workspace_id=workspace_id)
        if updated is None:
            raise ProductProcessingNotFound("product draft not found")
        return updated

    def save_draft_image(
        self,
        draft_id: int,
        content: bytes,
        filename: str,
        content_type: str,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        path = self.assets.save_draft_image(content, filename, content_type)
        return self.update_draft(
            draft_id,
            {"image_url": "", "main_image_url": "", "image_path": str(path)},
            allow_image_path=True,
            workspace_id=workspace_id,
        )

    def draft_image_path(self, draft_id: int, workspace_id: str = "local") -> Path:
        draft = self.get_draft(draft_id, workspace_id)
        path = self._text(draft.get("image_path") or draft["raw_payload"].get("image_path"))
        if not path:
            raise ProductProcessingNotFound("draft does not have a local image")
        try:
            return self.assets.require_managed_file(path)
        except (ValueError, FileNotFoundError) as exc:
            raise ProductProcessingNotFound(str(exc)) from exc

    def delete_drafts(self, draft_ids: list[int] | None, workspace_id: str = "local") -> dict[str, Any]:
        ids = self.repository.delete_drafts(draft_ids, workspace_id)
        return {"deleted_count": len(ids), "ids": ids, "status": "deleted"}

    def import_workbook(
        self,
        filename: str,
        content: bytes,
        source_type: str,
        max_products: int = 0,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        if not content:
            raise ValueError("uploaded product file is empty")
        rows = read_product_workbook(filename, content)
        selected = rows[: max_products or None]
        drafts: list[dict[str, Any]] = []
        skipped = 0
        for row in selected:
            row.update({"source_type": source_type, "source_filename": filename})
            draft, created = self.create_draft(row, workspace_id=workspace_id)
            if created:
                drafts.append(draft)
            else:
                skipped += 1
        return {
            "created": len(drafts),
            "skipped": skipped,
            "ids": [draft["id"] for draft in drafts],
            "drafts": drafts,
            "filename": filename,
        }

    def intake_daily_selection(self, run: DailySelectionRun) -> dict[str, Any]:
        created: list[dict[str, Any]] = []
        skipped: list[str] = []
        for candidate in run.candidates:
            payload = candidate.model_dump(mode="json")
            payload.update(
                {
                    "source_type": "daily_selection",
                    "selection_run_id": run.run_id,
                    "selection_criteria": run.criteria.model_dump(mode="json"),
                    "selection_counts": run.counts,
                }
            )
            draft, was_created = self.create_draft(
                payload,
                selection_run_id=run.run_id,
                workspace_id=run.workspace_id,
            )
            if was_created:
                created.append(draft)
            else:
                skipped.append(candidate.candidate_id)
        receipt = self.repository.save_intake(
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            status=run.status,
            criteria=run.criteria.model_dump(mode="json"),
            counts=run.counts or {
                key: int(value)
                for key, value in run.metadata.items()
                if key in {"api_calls", "search_calls", "image_search_calls", "detail_calls"}
                and isinstance(value, int)
            },
            errors=run.errors or list(run.metadata.get("errors") or []),
            candidate_count=len(run.candidates),
            created_count=len(created),
            skipped_count=len(skipped),
        )
        return {
            "receipt": receipt,
            "created": len(created),
            "skipped": len(skipped),
            "ids": [draft["id"] for draft in created],
            "skipped_candidate_ids": skipped,
            "drafts": created,
            "exchange_contract": "daily-selection-product-processing-v1",
        }

    def daily_selection_intake(self, run_id: str, workspace_id: str = "local") -> dict[str, Any]:
        receipt = self.repository.get_intake(run_id, workspace_id)
        if receipt is None:
            raise ProductProcessingNotFound("daily selection intake not found")
        drafts = self.list_drafts(
            None,
            500,
            0,
            summary=False,
            selection_run_id=run_id,
            workspace_id=workspace_id,
        )["drafts"]
        return {"receipt": receipt, "drafts": drafts}

    def source_images(
        self,
        draft_id: int | None = None,
        task_id: int | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        images = self.repository.list_source_images(
            product_draft_id=draft_id,
            task_id=task_id,
            workspace_id=workspace_id,
        )
        return {"images": images, "count": len(images)}

    def sync_draft_source_images(self, draft_id: int, workspace_id: str = "local") -> dict[str, int]:
        self.get_draft(draft_id, workspace_id)
        ready = failed = 0
        for image in self.repository.claim_syncable_source_images(draft_id, workspace_id):
            try:
                fetched = self._public_image_fetcher(image["url"])
                path = self.assets.save_source_image(fetched.content, fetched.final_url, fetched.media_type)
            except Exception as error:
                if self.repository.fail_source_image(image["id"], str(error), image["_sync_claim_token"], workspace_id):
                    failed += 1
            else:
                if self.repository.complete_source_image(image["id"], str(path), image["_sync_claim_token"], workspace_id):
                    ready += 1
        return {"ready": ready, "failed": failed}

    def retry_draft_source_images(self, draft_id: int, workspace_id: str = "local") -> dict[str, int]:
        return self.sync_draft_source_images(draft_id, workspace_id)

    def _seed_draft_source_images(self, draft: dict[str, Any], raw: dict[str, Any]) -> None:
        source_urls = [self._text(draft.get("image_url"))]
        source_urls.extend(self._url_list(raw.get("source_image_urls")))
        self.repository.preserve_source_images(
            task_id=None,
            product_draft_id=int(draft["id"]),
            source_urls=source_urls,
            detail_urls=self._url_list(raw.get("source_detail_image_urls")),
        )

    def consume_daily_selection_handoffs(
        self, handoffs: list[DailySelectionHandoffEnvelope]
    ) -> dict[str, Any]:
        receipts: list[dict[str, Any]] = []
        drafts: list[dict[str, Any]] = []
        created_count = 0
        for handoff in handoffs:
            existing_receipt = self.repository.handoff_receipt(handoff.handoff_id, handoff.workspace_id)
            if existing_receipt is not None:
                receipts.append(existing_receipt)
                draft = self.repository.get_draft(
                    existing_receipt["product_draft_id"],
                    include_deleted=True,
                    workspace_id=handoff.workspace_id,
                )
                if draft:
                    drafts.append(draft)
                continue
            if handoff.status == "failed":
                raise ValueError("failed daily-selection handoffs cannot be consumed")
            candidate = candidate_from_handoff(handoff)
            payload = candidate.model_dump(mode="json")
            payload.update(
                {
                    "source_type": "daily_selection_handoff",
                    "selection_run_id": handoff.run_id,
                    "workspace_id": handoff.workspace_id,
                    "daily_selection_handoff_id": handoff.handoff_id,
                    "daily_selection_handoff_idempotency_key": handoff.idempotency_key,
                }
            )
            draft, created = self.create_draft(
                payload,
                selection_run_id=handoff.run_id,
                workspace_id=handoff.workspace_id,
                handoff_id=handoff.handoff_id,
                handoff_idempotency_key=handoff.idempotency_key,
            )
            receipt = self.repository.save_handoff_receipt(
                handoff_id=handoff.handoff_id,
                idempotency_key=handoff.idempotency_key,
                workspace_id=handoff.workspace_id,
                run_id=handoff.run_id,
                candidate_id=handoff.candidate_id,
                product_draft_id=draft["id"],
                source_status=handoff.status,
                payload_sha256=hashlib.sha256(handoff.payload_json.encode("utf-8")).hexdigest(),
            )
            receipts.append(receipt)
            drafts.append(draft)
            created_count += int(created)
        return {
            "contract_version": "daily-selection-handoff-consumer-v1",
            "consumer_status": "consumed",
            "received": len(handoffs),
            "created": created_count,
            "replayed": len(handoffs) - created_count,
            "receipts": receipts,
            "drafts": drafts,
            "upstream_ack_required": True,
        }

    def process_drafts(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        existing = self.repository.task_by_idempotency_key(idempotency_key, workspace_id)
        if existing is not None:
            return self._task_response(existing, "重复提交已返回原任务")
        draft_ids = list(dict.fromkeys(int(item) for item in payload.get("draft_ids") or [] if int(item) > 0))
        if not draft_ids:
            raise ValueError("draft_ids is required")
        max_products = max(0, int(payload.get("max_products") or 0))
        if max_products:
            draft_ids = draft_ids[:max_products]
        drafts = self.repository.get_drafts(draft_ids, workspace_id=workspace_id)
        missing = sorted(set(draft_ids) - {draft["id"] for draft in drafts})
        if missing:
            raise ProductProcessingNotFound(f"product drafts not found: {missing}")
        preflight_only = bool(payload.get("preflight_only") or payload.get("category_preflight_only"))
        task = self.repository.create_task(
            title=self._text(payload.get("title")) or "产品处理任务-草稿池商品",
            preflight_only=preflight_only,
            settings=payload,
            drafts=drafts,
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
        )
        completed = self._execute_task(task["id"], workspace_id)
        return self._task_response(completed, "草稿池预检已完成" if preflight_only else "产品处理任务已完成")

    def task_outputs(
        self, task_id: int, *, summary_only: bool = False, workspace_id: str = "local"
    ) -> dict[str, Any]:
        task = self._require_task(task_id, workspace_id)
        response = self._task_response(task)
        if summary_only and len(response["items"]) > 20:
            response["items"] = response["items"][:20]
            response["summary_only"] = True
        else:
            response["summary_only"] = False
        response["item_count"] = len(task["items"])
        return response

    def task_history(self, limit: int, workspace_id: str = "local") -> dict[str, Any]:
        tasks = self.repository.list_tasks(limit, workspace_id)
        history = []
        for task in tasks:
            downloadable = {
                "dxm": bool(task["output_file"]),
                "errors": bool(task["error_report_file"]),
                "video_manifest": bool(task["video_manifest_file"]),
            }
            settings = task["settings"]
            history.append(
                {
                    "task_id": task["id"],
                    "title": task["title"],
                    "status": task["status"],
                    "created_at": task["created_at"],
                    "updated_at": task["updated_at"],
                    "date": task["created_at"][:10],
                    "total_count": task["total_count"],
                    "success_count": task["success_count"],
                    "failed_count": task["failed_count"],
                    "skipped_count": task["skipped_count"],
                    "downloadable": downloadable,
                    "downloadable_count": sum(downloadable.values()),
                    "has_downloadable_output": any(downloadable.values()),
                    "cleared_from_product_processing": task["cleared_from_product_processing"],
                    "target_site": settings.get("target_site", "US"),
                    "target_language": settings.get("target_language", "en"),
                    "target_language_label": "英语" if settings.get("target_language", "en") == "en" else "西班牙语",
                    "language_contract_version": "product-processing-language-v1",
                }
            )
        return {"tasks": history, "limit": limit}

    def pause_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self._require_task(task_id, workspace_id)
        if task["status"] in {"completed", "failed", "partial_failure"}:
            return {**self._task_response(task), "message": "任务已结束，无需暂停"}
        task = self.repository.set_task_status(task_id, "paused", workspace_id) or task
        return {**self._task_response(task), "message": "产品处理任务已暂停"}

    def resume_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self._require_task(task_id, workspace_id)
        if task["status"] in {"completed", "failed", "partial_failure"}:
            return {**self._task_response(task), "message": "任务已结束，返回现有结果"}
        self.repository.set_task_status(task_id, "queued", workspace_id)
        return self._task_response(self._execute_task(task_id, workspace_id), "产品处理任务已继续并完成")

    def retry_attention(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self._require_task(task_id, workspace_id)
        if not any(item["status"] in {"failed", "attention_required"} for item in task["items"]):
            return {**self._task_response(task), "message": "当前任务没有可重试的失败商品"}
        self.repository.reset_failed_items(task_id, workspace_id)
        return self._task_response(self._execute_task(task_id, workspace_id), "失败商品已重新处理")

    def clear_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self.repository.clear_task(task_id, workspace_id)
        if task is None:
            raise ProductProcessingNotFound("product processing task not found")
        return {"status": "cleared", "task_id": task_id, "cleared_count": 1, "message": "已清空当前产品处理进度"}

    def download_path(self, task_id: int, kind: str, workspace_id: str = "local") -> Path:
        task = self._require_task(task_id, workspace_id)
        normalized = self._text(kind).lower()
        field = {
            "dxm": "output_file",
            "errors": "error_report_file",
            "video_manifest": "video_manifest_file",
        }.get(normalized)
        if field is None:
            raise ValueError("kind must be dxm, errors or video_manifest")
        try:
            return self.assets.require_managed_file(task[field])
        except FileNotFoundError as exc:
            raise ProductProcessingNotFound(str(exc)) from exc

    def process_workbook(
        self,
        filename: str,
        content: bytes,
        form: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        imported = self.import_workbook(
            filename,
            content,
            self._text(form.get("source_type")) or "excel",
            int(form.get("max_products") or 0),
            workspace_id,
        )
        if not imported["ids"]:
            raise ValueError("workbook did not create any processable drafts")
        payload = {**form, "draft_ids": imported["ids"], "title": form.get("title") or "产品处理任务-Excel 导入"}
        return self.process_drafts(payload, idempotency_key=idempotency_key, workspace_id=workspace_id)

    def process_single(
        self,
        form: dict[str, Any],
        *,
        image_content: bytes | None = None,
        image_filename: str = "",
        image_content_type: str = "",
        idempotency_key: str | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        draft, _ = self.create_draft(
            {
                "source_type": "manual",
                "title": form.get("title"),
                "product_name": form.get("title"),
                "category": form.get("category"),
                "image_url": form.get("image_url"),
                "price": form.get("price"),
                "product_link": form.get("link"),
            },
            workspace_id=workspace_id,
        )
        if image_content:
            draft = self.save_draft_image(
                draft["id"],
                image_content,
                image_filename,
                image_content_type,
                workspace_id,
            )
        return self.process_drafts(
            {**form, "draft_ids": [draft["id"]], "title": form.get("task_title") or "产品处理任务-单品"},
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
        )

    def _execute_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self._require_task(task_id, workspace_id)
        if task["status"] == "paused":
            return task
        self.repository.set_task_status(task_id, "running", workspace_id)
        settings = task["settings"]
        preflight_only = bool(task["preflight_only"])
        draft_ids = [item["product_draft_id"] for item in task["items"] if item["product_draft_id"]]
        drafts = {
            draft["id"]: draft
            for draft in self.repository.get_drafts(
                draft_ids,
                include_deleted=True,
                workspace_id=workspace_id,
            )
        }
        item_results: list[dict[str, Any]] = []
        successes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        source_images: list[str] = []
        for item in task["items"]:
            draft = drafts.get(item["product_draft_id"])
            processed = self._process_one(item, draft, settings, preflight_only)
            item_results.append(processed)
            if processed["status"] == "completed":
                result = processed["result"]
                successes.append(result)
                source_images.extend(result.get("source_image_urls") or [])
                if draft and not preflight_only:
                    raw = dict(draft["raw_payload"])
                    raw["product_processing_receipt"] = {
                        "task_id": task_id,
                        "status": "completed",
                        "target_site": settings.get("target_site", "US"),
                        "target_language": settings.get("target_language", "en"),
                    }
                    self.repository.update_draft(
                        draft["id"],
                        {"status": "processed"},
                        raw,
                        workspace_id=workspace_id,
                    )
            else:
                failures.append(processed)
        preserve = settings.get("source_image_to_library")
        if preserve is None:
            preserve = settings.get("preserve_source_images", True)
        source_manifest = ""
        if preserve and source_images and not preflight_only:
            source_manifest = str(self.assets.materialize_source_manifest(task_id, source_images))
            for row in successes:
                row["source_image_manifest"] = source_manifest
                row["source_image_library"] = self.repository.preserve_source_images(
                    task_id=task_id,
                    product_draft_id=int(row["product_draft_id"]),
                    source_urls=list(row.get("source_image_urls") or []),
                    detail_urls=list(row.get("source_detail_image_urls") or []),
                )
        paths = self.assets.write_task_outputs(
            task_id,
            successes,
            failures,
            include_video_manifest=bool(settings.get("product_video_template")) and not preflight_only,
        )
        return self.repository.finish_task(
            task_id,
            item_results,
            output_file=str(paths.workbook),
            error_report_file=str(paths.errors),
            video_manifest_file=str(paths.video_manifest) if paths.video_manifest else "",
            workspace_id=workspace_id,
        )

    def _process_one(
        self,
        item: dict[str, Any],
        draft: dict[str, Any] | None,
        settings: dict[str, Any],
        preflight_only: bool,
    ) -> dict[str, Any]:
        if draft is None or draft["status"] == "deleted":
            return {**item, "status": "failed", "reason": "product draft not found", "result": {}}
        raw = draft["raw_payload"]
        title = self._text(draft.get("title") or draft.get("product_name") or raw.get("source_title"))
        image_url = self._text(draft.get("image_url") or raw.get("main_image_url") or self._first(raw.get("source_image_urls")))
        source_url = self._text(raw.get("source_url") or raw.get("product_link") or draft.get("source_ref"))
        missing = [name for name, value in (("title", title), ("image", image_url)) if not value]
        if missing:
            reason = f"missing required fields: {', '.join(missing)}"
            return {
                **item,
                "title": title,
                "image_url": image_url,
                "status": "attention_required",
                "reason": reason,
                "result": {"error_type": "validation", "operator_hint": "补充标题和主图后重试", "retryable": True},
            }
        skc = self._text(draft.get("skc")) or f"PP-{draft['id']:06d}"
        sku = self._text(draft.get("sku")) or skc
        target_site = self._text(settings.get("target_site")) or "US"
        target_language = self._text(settings.get("target_language")) or "en"
        optimized_title = title if preflight_only or not settings.get("title_optimize", True) else self._normalized_title(title)
        description = self._text(draft.get("description") or raw.get("description"))
        if not description and not preflight_only:
            description = f"{optimized_title}. Source information preserved for operator review."
        result = {
            "product_draft_id": draft["id"],
            "candidate_id": raw.get("candidate_id") or draft.get("candidate_id"),
            "skc": skc,
            "sku": sku,
            "optimized_title": optimized_title,
            "description": description,
            "image_url": image_url,
            "source_url": source_url,
            "source_platform": raw.get("source_platform") or raw.get("platform") or "",
            "source_image_urls": raw.get("source_image_urls") or ([image_url] if image_url else []),
            "source_detail_image_urls": raw.get("source_detail_image_urls") or [],
            "source_attributes": raw.get("source_attributes") or [],
            "source_variant_records": raw.get("source_variant_records") or [],
            "cost": draft.get("cost"),
            "declared_price": draft.get("declared_price"),
            "target_site": target_site,
            "target_language": target_language,
            "selection_run_id": draft.get("selection_run_id"),
            "selection_keyword": raw.get("selection_keyword") or "",
            "selection_score": raw.get("selection_score"),
            "risk_tags": raw.get("risk_tags") or [],
            "preflight_only": preflight_only,
            "status": "preflight_passed" if preflight_only else "completed",
            "exchange_contract": "daily-selection-product-processing-v1" if draft.get("selection_run_id") else None,
        }
        return {
            **item,
            "skc": skc,
            "title": optimized_title,
            "image_url": image_url,
            "status": "completed",
            "reason": "",
            "result": result,
        }

    def _task_response(self, task: dict[str, Any], message: str = "") -> dict[str, Any]:
        items = task["items"]
        attention = sum(item["status"] == "attention_required" for item in items)
        technical = sum(
            item["status"] in {"failed", "attention_required"} and bool(item.get("result", {}).get("retryable"))
            for item in items
        )
        outputs = {
            "dxm_import": task["output_file"],
            "error_report": task["error_report_file"],
            "log_file": "",
            "product_video_manifest": task["video_manifest_file"],
        }
        task_projection = {
            "id": task["id"],
            "task_id": task["id"],
            "title": task["title"],
            "status": task["status"],
            "total_count": task["total_count"],
            "success_count": task["success_count"],
            "failed_count": task["failed_count"],
            "skipped_count": task["skipped_count"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "metadata": {
                "module": "product_processing",
                "engine": "local_sqlalchemy",
                "settings": task["settings"],
                "preflight_only": task["preflight_only"],
                "cleared_from_product_processing": task["cleared_from_product_processing"],
            },
        }
        return {
            "task_id": task["id"],
            "total_count": task["total_count"],
            "success_count": task["success_count"],
            "failed_count": task["failed_count"],
            "auto_recovery_pending_count": 0,
            "identity_review_required_count": attention,
            "logistics_review_required_count": 0,
            "technical_retryable_count": technical,
            "configuration_blocked_count": 0,
            "skipped_count": task["skipped_count"],
            "output_file": task["output_file"],
            "error_report_file": task["error_report_file"],
            "items": items,
            "task": task_projection,
            "outputs": outputs,
            "message": message,
        }

    def _require_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self.repository.get_task(task_id, workspace_id)
        if task is None:
            raise ProductProcessingNotFound("product processing task not found")
        return task

    @staticmethod
    def _draft_summary(draft: dict[str, Any]) -> dict[str, Any]:
        raw = draft["raw_payload"]
        return {
            **draft,
            "raw_payload": {
                "platform": raw.get("platform") or raw.get("source_platform") or "",
                "source_platform": raw.get("source_platform") or "",
                "product_link": raw.get("product_link") or raw.get("source_url") or "",
                "source_url": raw.get("source_url") or "",
                "image_path": raw.get("image_path") or draft.get("image_path") or "",
                "category": raw.get("category") or "",
                "variant_complexity": raw.get("variant_complexity") or {},
                "captured_fields": raw.get("captured_fields") or {},
                "source_variant_records": raw.get("source_variant_records") or [],
                "raw_variant_combinations_count": len(raw.get("raw_variant_combinations") or []),
            },
            "raw_payload_summary": True,
        }

    @staticmethod
    def _apply_sku_changes(raw: dict[str, Any], edits: Any, deletes: Any) -> None:
        edits = edits if isinstance(edits, dict) else {}
        delete_values = {str(item).strip() for item in deletes or [] if str(item).strip()}
        variants = raw.get("source_variant_records")
        if not isinstance(variants, list):
            return
        kept = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            attributes = variant.get("attributes") if isinstance(variant.get("attributes"), dict) else {}
            label = "/".join(str(value) for value in attributes.values())
            variant_id = str(variant.get("sku_id") or variant.get("source_sku_id") or "")
            if label in delete_values or variant_id in delete_values:
                continue
            if label in edits:
                variant["display_name"] = str(edits[label]).strip()
            kept.append(variant)
        raw["source_variant_records"] = kept
        raw["sku_name_edits"] = edits
        raw["sku_name_deletes"] = sorted(delete_values)

    @staticmethod
    def _normalized_title(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()[:200]

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _first(value: Any) -> Any:
        return value[0] if isinstance(value, list) and value else ""

    @staticmethod
    def _url_list(value: Any) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            match = re.search(r"-?\d+(?:\.\d+)?", str(value))
            return float(match.group()) if match else None

    @staticmethod
    def _json(value: Any) -> str:
        from .infrastructure.repository import dumps

        return dumps(value)
