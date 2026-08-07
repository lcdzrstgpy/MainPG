from __future__ import annotations

import base64
import importlib.util
import hashlib
import json
import os
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wh_local.data_collection.contracts import DailySelectionError
from wh_local.data_collection.public_image_fetch import FetchedPublicImage, fetch_public_image

from .ai_client import AiClient, AiProviderError
from .domain.language_contract import (
    apply_language_contract_to_prompt,
    ensure_target_language_result,
    language_profile,
    normalize_target_language,
)
from .domain.models import DEFAULT_PROMPTS, DailySelectionHandoffEnvelope, DailySelectionRun
from .domain.policy import PolicyIssue, is_safe_external_url, product_policy_issue, strict_external_url_issue
from .domain.prompts import format_prompt
from .domain.visual_planner import listing_prompt_context
from .domain.workbooks import read_product_workbook
from .infrastructure.assets import ProductProcessingAssets
from .infrastructure.ocr_gate import detect_chinese_text, max_repair_rounds, ocr_diagnostics, ocr_gate_enabled
from .infrastructure.repository import ProductProcessingRepository
from .provider_config import resolve_ai_provider

_MEDIA_TYPES: tuple | None = None


def _ai_enabled() -> bool:
    """外部 AI 总开关：WH_PRODUCT_AI_ENABLED=0 时回退本地透传（测试/离线场景）。"""
    return str(os.environ.get("WH_PRODUCT_AI_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从 AI 回复中提取 JSON 对象（容忍代码围栏与前后说明文字）。"""
    value = str(text or "").strip()
    if not value:
        return None
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(value[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _ai_error_reason(exc: Exception) -> str:
    """将 AI 失败异常转成可展示的原因（超时/HTTP 状态/语言违规等）。"""
    message = str(exc).strip()
    return message[:200] if message else type(exc).__name__


def _media_types() -> tuple:
    """Lazily import the image adapter; requests/Pillow are optional at import time."""
    global _MEDIA_TYPES
    if _MEDIA_TYPES is None:
        try:
            from .infrastructure.media import (  # noqa: PLC0415
                MediaConfigurationError,
                MediaProcessingError,
                ProductImageProcessor,
            )
            _MEDIA_TYPES = (ProductImageProcessor, MediaConfigurationError, MediaProcessingError)
        except ModuleNotFoundError:
            _MEDIA_TYPES = ()
    return _MEDIA_TYPES


class ProductProcessingNotFound(LookupError):
    pass


class ProductProcessingConflict(RuntimeError):
    pass


class MediaUnavailableError(RuntimeError):
    """Image processing dependencies are missing."""


def _as_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce form/JSON booleans (including string 'true'/'false') into bool."""
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
        self._ai_instance: AiClient | None = None
        self._media_instance = None  # ProductImageProcessor (懒加载，可选依赖)

    def engine_status(self) -> dict[str, Any]:
        dependency_status = {
            "openpyxl": importlib.util.find_spec("openpyxl") is not None,
            "python_multipart": importlib.util.find_spec("multipart") is not None,
            "pillow": importlib.util.find_spec("PIL") is not None,
            "opencv": importlib.util.find_spec("cv2") is not None,
            "rapidocr": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
        }
        # 真实 AI 中转已通过 provider_config 提供；未配置时保持本地透传兜底，
        # 保证页面在无外部 key 时仍可操作。
        provider = resolve_ai_provider()
        media: dict[str, Any] = {}
        media_types = _media_types()
        if media_types:
            media = media_types[0](config_provider=self._media_config_provider).status()
        config = {
            "ai_provider": provider["provider"] if provider.get("api_key") else "local-deterministic",
            "ai_model": provider.get("text_model") or "product-processing-local-v1",
            "ai_configured": bool(provider.get("api_key")),
            "backup_ai_configured": False,
            "image_provider": provider["provider"] if (provider.get("api_key") and media.get("image_configured")) else "local-source-pass-through",
            "image_model": provider.get("reference_image_model") or provider.get("image_model") or "source-image-preservation-v1",
            "image_configured": media.get("image_configured", False),
            "backup_image_configured": media.get("backup_image_configured", False),
            "cos_configured": media.get("cos_configured", False),
            "cos_upload_prefix": "product-processing",
        }
        return {
            "available": True,
            "ready": dependency_status["openpyxl"] and dependency_status["python_multipart"],
            "app_dir": str(Path(__file__).parent),
            "app_file": str(Path(__file__)),
            "python": sys.executable,
            "worker": "local-synchronous-v1",
            "message": "本地产品处理引擎已就绪；标题/描述/图片通过 AI 中转生成，失败时自动回退本地透传。",
            "diagnostics": {
                "config": config,
                "tenant_ai_capability": {"text": config["ai_configured"], "image": config["image_configured"], "mode": "openai_compatible_relay"},
                "dependencies": dependency_status,
                "ocr_gate": ocr_diagnostics(),
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
            # A OneBound candidate may legitimately recur in a later preview.
            # Keep its single draft, but replace the run-scoped provenance with
            # the evidence and criteria from the current collection run.
            if self._text(raw.get("source_type")) == "onebound_api" and selection_run_id:
                refreshed = self.repository.update_draft(
                    existing["id"],
                    {"selection_run_id": selection_run_id},
                    raw,
                    workspace_id=workspace_id,
                )
                if refreshed is None:
                    raise ProductProcessingNotFound("product draft not found")
                self._seed_draft_source_images(refreshed, raw)
                return refreshed, False
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
        source_type: str | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        drafts, has_more = self.repository.list_drafts(
            status,
            limit,
            offset,
            selection_run_id=selection_run_id,
            source_type=source_type,
            workspace_id=workspace_id,
        )
        ready_source_paths = self.repository.ready_primary_source_image_paths(
            (draft["id"] for draft in drafts),
            workspace_id=workspace_id,
        )
        primary_source_images = self.repository.primary_source_images(
            (draft["id"] for draft in drafts),
            workspace_id=workspace_id,
        )
        drafts = [
            {
                **draft,
                "image_path": draft["image_path"] or ready_source_paths.get(draft["id"], ""),
                "primary_source_image": primary_source_images.get(draft["id"]),
            }
            for draft in drafts
        ]
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
            path = self.repository.ready_primary_source_image_paths([draft_id], workspace_id=workspace_id).get(draft_id, "")
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
        drafts: list[dict[str, Any]] = []
        created: list[dict[str, Any]] = []
        skipped: list[str] = []
        intake_errors = list(getattr(run, "errors", []) or run.metadata.get("errors") or [])
        criteria_source = run.criteria
        criteria = (
            dict(criteria_source)
            if isinstance(criteria_source, dict)
            else criteria_source.model_dump(mode="json")
        )
        counts = dict(getattr(run, "counts", {}) or {})
        for candidate in run.candidates:
            payload = candidate.model_dump(mode="json")
            payload.update(
                {
                    "source_type": "onebound_api",
                    "selection_run_id": run.run_id,
                    "collection_mode": criteria.get("collection_mode", "keyword"),
                    "source_evidence": payload.get("evidence", []),
                    "selection_criteria": criteria,
                    "selection_counts": counts,
                }
            )
            try:
                draft, was_created = self.create_draft(
                    payload,
                    selection_run_id=run.run_id,
                    workspace_id=run.workspace_id,
                )
            except Exception as error:
                intake_errors.append(
                    DailySelectionError(
                        code="PRODUCT_DRAFT_INTAKE_FAILED",
                        message="候选商品写入产品草稿池失败",
                        context={
                            "candidate_id": candidate.candidate_id,
                            "reason": str(error),
                        },
                    ).model_dump(mode="json")
                )
                continue
            drafts.append(draft)
            if was_created:
                created.append(draft)
            else:
                skipped.append(candidate.candidate_id)
        receipt = self.repository.save_intake(
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            status="partial" if intake_errors else run.status,
            criteria=criteria,
            counts=counts or {
                key: int(value)
                for key, value in run.metadata.items()
                if key in {"api_calls", "search_calls", "image_search_calls", "detail_calls"}
                and isinstance(value, int)
            },
            errors=intake_errors,
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
            "drafts": drafts,
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
            draft = self.repository.draft_by_candidate(
                handoff.candidate_id, handoff.workspace_id
            )
            if draft is None or draft["status"] == "deleted":
                raise ValueError(
                    "daily-selection handoff requires an ingressed onebound_api draft"
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
            # Confirmation is selection state only. The OneBound preview
            # ingress owns draft creation, so a handoff can never add a
            # second candidate draft.
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

    @staticmethod
    def _normalize_settings(settings: dict[str, Any]) -> dict[str, Any]:
        """Normalize prototype-style options and legacy booleans into a single shape."""
        s = dict(settings)
        scope: list[str] = []
        raw_scope = s.get("processing_scope") or []
        if isinstance(raw_scope, str):
            scope = [x.strip() for x in raw_scope.split(",") if x.strip()]
        elif isinstance(raw_scope, (list, tuple)):
            scope = [str(x).strip() for x in raw_scope if str(x).strip()]
        scope = list(dict.fromkeys(scope))
        valid_scope = {
            "title", "details", "product_dimensions", "four_grid",
            "detail_images", "sku_images", "qualification",
        }
        scope = [x for x in scope if x in valid_scope]

        if not scope:
            # Derive scope from legacy booleans.
            if s.get("title_optimize", True):
                scope.append("title")
            if s.get("description", True):
                scope.append("details")
            if s.get("size", True):
                scope.append("product_dimensions")
            if s.get("grid_image", True):
                scope.append("four_grid")
            if s.get("detail_image", True):
                scope.append("detail_images")
            if s.get("image_rewrite", True):
                scope.append("sku_images")
            qm = s.get("qualification_mode", False)
            if isinstance(qm, bool) and qm:
                scope.append("qualification")
            elif isinstance(qm, str) and qm in {"standard", "strict"}:
                scope.append("qualification")

        s["processing_scope"] = scope

        qm = s.get("qualification_mode", False)
        if isinstance(qm, bool):
            s["qualification_mode"] = "strict" if (qm and "qualification" in scope) else "standard"
        elif qm not in {"standard", "strict"}:
            s["qualification_mode"] = "standard"
        elif "qualification" not in scope:
            s["qualification_mode"] = "standard"

        # Legacy booleans must stay in sync so existing code paths keep working.
        s["title_optimize"] = "title" in scope
        s["description"] = "details" in scope
        s["size"] = "product_dimensions" in scope
        s["grid_image"] = "four_grid" in scope
        s["detail_image"] = "detail_images" in scope
        s["image_rewrite"] = "sku_images" in scope
        raw_video = s.get("include_product_video")
        include_video = (
            bool(raw_video)
            if isinstance(raw_video, bool)
            else str(raw_video or "").strip().lower() in {"1", "true", "yes", "on"}
        )
        s["include_product_video"] = include_video
        if include_video:
            s["product_video_template"] = True
        s["skip_duplicates"] = _as_bool(s.get("skip_duplicates"), default=False)
        s["ip_check"] = _as_bool(s.get("ip_check"), default=True)
        return s

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
        payload = self._normalize_settings(payload)
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
        if payload.get("skip_duplicates"):
            drafts = [draft for draft in drafts if draft["status"] != "processed"]
        if not drafts:
            return {
                "status": "skipped",
                "message": "本次勾选商品均为已处理状态（已勾选“跳过已处理”），未创建处理任务",
                "total_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
            }
        preflight_only = bool(payload.get("preflight_only") or payload.get("category_preflight_only"))
        task = self.repository.create_task(
            title=self._text(payload.get("title")) or "产品处理任务-草稿池商品",
            preflight_only=preflight_only,
            settings=payload,
            drafts=drafts,
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
        )
        if bool(payload.get("async_mode", True)):
            self._launch_background_execute(task["id"], workspace_id)
            return {**self._task_response(task, "任务已提交，正在后台处理"), "async_mode": True}
        completed = self._execute_task(task["id"], workspace_id)
        return self._task_response(completed, "草稿池预检已完成" if preflight_only else "产品处理任务已完成")

    def _launch_background_execute(self, task_id: int, workspace_id: str) -> None:
        """后台线程执行任务，立即返回让前端轮询实时进度。"""

        def _run() -> None:
            try:
                self._execute_task(task_id, workspace_id)
            except Exception:
                # 兜底：任务执行异常时标记失败，避免任务卡在 running 状态
                try:
                    self.repository.set_task_status(task_id, "failed", workspace_id)
                except Exception:
                    pass

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"pp-task-{task_id}",
        ).start()

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
        task = self._require_task(task_id, workspace_id)
        if bool(task["settings"].get("async_mode", True)):
            self._launch_background_execute(task_id, workspace_id)
            return {**self._task_response(task, "产品处理任务已继续，正在后台处理"), "async_mode": True}
        return self._task_response(self._execute_task(task_id, workspace_id), "产品处理任务已继续并完成")

    def retry_attention(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self._require_task(task_id, workspace_id)
        if not any(item["status"] in {"failed", "attention_required"} for item in task["items"]):
            return {**self._task_response(task), "message": "当前任务没有可重试的失败商品"}
        self.repository.reset_failed_items(task_id, workspace_id)
        task = self._require_task(task_id, workspace_id)
        if bool(task["settings"].get("async_mode", True)):
            self._launch_background_execute(task_id, workspace_id)
            return {**self._task_response(task, "失败商品已重新处理，正在后台执行"), "async_mode": True}
        return self._task_response(self._execute_task(task_id, workspace_id), "失败商品已重新处理")

    def clear_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any]:
        task = self.repository.clear_task(task_id, workspace_id)
        if task is None:
            raise ProductProcessingNotFound("product processing task not found")
        return {"status": "cleared", "task_id": task_id, "cleared_count": 1, "message": "已清空当前产品处理进度"}

    def download_path(self, task_id: int, kind: str, workspace_id: str = "local") -> Path:
        task = self._require_task(task_id, workspace_id)
        if task["status"] not in {"completed", "failed", "partial_failure"}:
            raise ProductProcessingConflict(
                f"任务尚未完成（当前状态：{task['status']}），输出文件将在处理后生成"
            )
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
        max_workers = max(1, min(20, int(settings.get("max_parallel_drafts", 1))))
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
        lock = threading.Lock()

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _process(item: dict[str, Any]) -> dict[str, Any]:
            draft = drafts.get(item["product_draft_id"])
            return self._process_one(item, draft, settings, preflight_only, task_id=task_id)

        def _persist_progress(processed: dict[str, Any]) -> None:
            """逐项写入处理结果并实时刷新任务计数，供前端进度轮询读取。"""
            item_id = processed.get("item_id")
            if item_id is None:
                return
            try:
                self.repository.update_item_progress(
                    task_id,
                    int(item_id),
                    status=str(processed.get("status") or "failed"),
                    reason=str(processed.get("reason") or ""),
                    skc=processed.get("skc"),
                    spu=processed.get("spu"),
                    title=processed.get("title"),
                    image_url=processed.get("image_url"),
                    result=processed.get("result") or {},
                    workspace_id=workspace_id,
                )
            except LookupError:
                # 任务已被清理时忽略进度写入，不阻塞整体流程
                pass

        if max_workers <= 1:
            # 串行模式：保持原有行为，便于调试和问题排查
            for item in task["items"]:
                processed = _process(item)
                item_results.append(processed)
                _persist_progress(processed)
                if processed["status"] == "completed":
                    result = processed["result"]
                    successes.append(result)
                    source_images.extend(result.get("source_image_urls") or [])
                    if (draft := drafts.get(item["product_draft_id"])) and not preflight_only:
                        self._mark_draft_processed(draft, task_id, settings, workspace_id)
                else:
                    failures.append(processed)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures_map = {
                    executor.submit(_process, item): item
                    for item in task["items"]
                }
                for future in as_completed(futures_map):
                    item = futures_map[future]
                    try:
                        processed = future.result()
                    except Exception as exc:
                        processed = {
                            "item_id": item["item_id"],
                            "product_draft_id": item["product_draft_id"],
                            "status": "failed",
                            "result": {
                                "failure_class": "technical_retryable",
                                "reason": f"并行处理异常: {_ai_error_reason(exc)}",
                                "retryable": True,
                            },
                        }
                    with lock:
                        item_results.append(processed)
                        _persist_progress(processed)
                        if processed["status"] == "completed":
                            result = processed["result"]
                            successes.append(result)
                            source_images.extend(result.get("source_image_urls") or [])
                            if (draft := drafts.get(item["product_draft_id"])) and not preflight_only:
                                self._mark_draft_processed(draft, task_id, settings, workspace_id)
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

    def _mark_draft_processed(
        self, draft: dict[str, Any], task_id: int, settings: dict[str, Any], workspace_id: str
    ) -> None:
        """标记草稿为已处理（线程安全，由锁保护的外部调用保证）。"""
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

    def _process_one(
        self,
        item: dict[str, Any],
        draft: dict[str, Any] | None,
        settings: dict[str, Any],
        preflight_only: bool,
        *,
        task_id: int,
    ) -> dict[str, Any]:
        if draft is None or draft["status"] == "deleted":
            return {
                **item,
                "status": "failed",
                "reason": "product draft not found",
                "result": {
                    "error_type": "not_found",
                    "failure_class": "technical_retryable",
                    "operator_hint": "草稿不存在或已被删除",
                    "retryable": True,
                },
            }
        raw = draft["raw_payload"]
        title = self._text(draft.get("title") or draft.get("product_name") or raw.get("source_title"))
        image_url = self._text(draft.get("image_url") or raw.get("main_image_url") or self._first(raw.get("source_image_urls")))
        source_url = self._text(raw.get("source_url") or raw.get("product_link") or draft.get("source_ref"))
        missing = [name for name, value in (("title", title), ("image", image_url)) if not value]
        if missing:
            reason = f"缺少必填字段: {', '.join(missing)}"
            return {
                **item,
                "title": title,
                "image_url": image_url,
                "status": "attention_required",
                "reason": reason,
                "result": {
                    "error_type": "validation",
                    "failure_class": "configuration_blocked",
                    "operator_hint": "补充标题和主图后重试",
                    "retryable": True,
                },
            }

        scope = set(settings.get("processing_scope") or [])
        qualification_enabled = "qualification" in scope
        issue: PolicyIssue | None = None
        if settings.get("strict_external"):
            issue = strict_external_url_issue(source_url=source_url, image_url=image_url)
        if issue is None:
            category = self._text(raw.get("category") or raw.get("source_category_path"))
            issue = product_policy_issue(
                raw,
                title=title,
                category=category,
                ip_check=_as_bool(settings.get("ip_check"), default=True),
                qualification_enabled=qualification_enabled,
                extra_infringement_terms=raw.get("extra_infringement_terms") or [],
            )
        if issue is not None:
            failure_class = self._failure_class_from_issue(issue)
            return {
                **item,
                "title": title,
                "image_url": image_url,
                "status": "attention_required",
                "reason": issue.message,
                "result": {
                    "error_type": issue.code,
                    "failure_class": failure_class,
                    "operator_hint": issue.operator_hint,
                    "retryable": failure_class in {"technical_retryable", "configuration_blocked"},
                },
            }

        skc = self._text(draft.get("skc")) or f"PP-{draft['id']:06d}"
        sku = self._text(draft.get("sku")) or skc
        target_site = self._text(settings.get("target_site")) or "US"
        target_language = self._text(settings.get("target_language")) or "en"
        try:
            target_language = normalize_target_language(target_language)
        except ValueError:
            target_language = "en"
        category = self._text(raw.get("category") or raw.get("source_category_path"))
        source_image_urls = self._url_list(raw.get("source_image_urls")) or ([image_url] if image_url else [])
        source_detail_image_urls = self._url_list(raw.get("source_detail_image_urls"))
        source_attributes = self._source_attributes_text(raw)

        ai_notes: list[str] = []
        optimized_title = title
        description = self._text(draft.get("description") or raw.get("description"))
        if not preflight_only:
            needs_title = "title" in scope and settings.get("title_optimize", True)
            needs_desc = "details" in scope and not description
            if needs_title and needs_desc:
                combined = self._generate_combined_text(
                    title,
                    category,
                    raw,
                    target_language,
                    target_site,
                    ai_notes,
                )
                if combined:
                    if combined.get("title"):
                        optimized_title = self._normalized_title(combined["title"])
                    if combined.get("description"):
                        description = combined["description"]
                    ai_notes.append("text:ai-combined")
                    needs_title = needs_desc = False
            if needs_title:
                generated_title = self._generate_title(
                    title,
                    category,
                    raw,
                    target_language,
                    target_site,
                    ai_notes,
                )
                if generated_title:
                    optimized_title = generated_title
                    ai_notes.append("title:ai")
            if needs_desc:
                generated_desc = self._generate_description(
                    optimized_title,
                    category,
                    raw,
                    target_language,
                    target_site,
                    ai_notes,
                )
                if generated_desc:
                    description = generated_desc
                    ai_notes.append("details:ai")

        if not description:
            # AI 未启用或生成失败时保留旧模板兜底，避免导入表描述为空
            description = f"{optimized_title}. Source information preserved for operator review."

        # 目标语言强制校验：AI 启用时不允许把未翻译标题/描述导出（对齐原型“已阻止导出”行为）
        if not preflight_only and _ai_enabled():
            try:
                ensure_target_language_result("标题", optimized_title, target_language)
                ensure_target_language_result("描述", description, target_language)
            except ValueError as exc:
                return {
                    **item,
                    "title": optimized_title,
                    "image_url": image_url,
                    "status": "attention_required",
                    "reason": str(exc),
                    "result": {
                        "error_type": "target_language_unmet",
                        "failure_class": "technical_retryable",
                        "operator_hint": "AI 输出未通过目标语言校验（标题/描述仍含其他语言文本），请重试或检查 AI 配置",
                        "retryable": True,
                        "ai_notes": ai_notes,
                    },
                }

        # 变种属性值翻译（对齐原型 VARIANT_VALUE_TRANSLATION_PROMPT）：来源中文规格值 → 目标语言可读显示名
        variant_value_translations: dict[str, str] = {}
        if not preflight_only:
            variant_value_translations = self._translate_variant_values(
                raw, optimized_title, target_language, target_site, ai_notes
            )

        product_dimensions: dict[str, Any] = {}
        if not preflight_only and "product_dimensions" in scope:
            product_dimensions = self._generate_size(raw, optimized_title, category, ai_notes) or {}
            if product_dimensions:
                ai_notes.append("product_dimensions:ai")

        grid_image_paths: list[str] = []
        grid_summary_path = ""
        detail_image_paths: list[str] = []
        need_grid = (
            not preflight_only
            and "four_grid" in scope
            and _as_bool(settings.get("ai_media_opt_in"), default=True)
        )
        need_detail = (
            not preflight_only
            and "detail_images" in scope
            and _as_bool(settings.get("ai_media_opt_in"), default=True)
        )
        # 商品主体视觉识别：用主图确认可售主体，替换生图提示词 Product: 行的标题猜测（减少主体误判）
        vision_subject = ""
        if (need_grid or need_detail) and source_image_urls:
            vision_subject = self._identify_subject(source_image_urls[0], optimized_title, category, ai_notes)
        if need_grid and need_detail:
            # 四宫格与详情图并行生成（对齐原型 build_grid_images / build_detail_image 双线程并行）
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _run_grid() -> tuple[list[str], str]:
                return self._generate_grid_images(
                    task_id,
                    draft["id"],
                    raw,
                    optimized_title,
                    category,
                    source_image_urls,
                    target_language,
                    target_site,
                    ai_notes,
                    vision_subject,
                )

            def _run_detail() -> list[str]:
                return self._generate_detail_images(
                    task_id,
                    draft["id"],
                    raw,
                    optimized_title,
                    category,
                    source_detail_image_urls or source_image_urls,
                    target_language,
                    target_site,
                    ai_notes,
                    vision_subject,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_grid = executor.submit(_run_grid)
                future_detail = executor.submit(_run_detail)
                for future in as_completed((future_grid, future_detail)):
                    if future is future_grid:
                        grid_image_paths, grid_summary_path = future.result()
                    else:
                        detail_image_paths = future.result()
        else:
            if need_grid:
                grid_image_paths, grid_summary_path = self._generate_grid_images(
                    task_id,
                    draft["id"],
                    raw,
                    optimized_title,
                    category,
                    source_image_urls,
                    target_language,
                    target_site,
                    ai_notes,
                    vision_subject,
                )
            if need_detail:
                detail_image_paths = self._generate_detail_images(
                    task_id,
                    draft["id"],
                    raw,
                    optimized_title,
                    category,
                    source_detail_image_urls or source_image_urls,
                    target_language,
                    target_site,
                    ai_notes,
                    vision_subject,
                )
        if grid_image_paths:
            ai_notes.append("four_grid:ai")
        if detail_image_paths:
            ai_notes.append("detail_images:ai")

        result = {
            "product_draft_id": draft["id"],
            "candidate_id": raw.get("candidate_id") or draft.get("candidate_id"),
            "skc": skc,
            "sku": sku,
            "category": category,
            "category_path": self._text(raw.get("category_path") or raw.get("source_category_path") or category),
            "category_id": self._text(raw.get("category_id") or raw.get("leaf_category_id")),
            "optimized_title": optimized_title,
            "description": description,
            "image_url": image_url,
            "source_url": source_url,
            "source_platform": raw.get("source_platform") or raw.get("platform") or "",
            "source_image_urls": source_image_urls,
            "source_detail_image_urls": source_detail_image_urls,
            "source_attributes": raw.get("source_attributes") or [],
            "source_variant_records": raw.get("source_variant_records") or [],
            "variant_value_translations": variant_value_translations,
            "cost": draft.get("cost"),
            "declared_price": draft.get("declared_price"),
            "suggested_price": draft.get("cost"),
            "product_dimensions": product_dimensions,
            "stock": self._source_stock(raw),
            "ship_days": 2,
            "target_site": target_site,
            "target_language": target_language,
            "target_language_label": language_profile(target_language)["label"],
            "carousel_image_paths": grid_image_paths,
            "grid_image_summary_path": grid_summary_path,
            "detail_image_paths": detail_image_paths,
            "ai_notes": ai_notes,
            "selection_run_id": draft.get("selection_run_id"),
            "selection_keyword": raw.get("selection_keyword") or "",
            "selection_score": raw.get("selection_score"),
            "risk_tags": raw.get("risk_tags") or [],
            "preflight_only": preflight_only,
            "status": "preflight_passed" if preflight_only else "completed",
            "processing_scope": sorted(scope),
            "qualification_mode": settings.get("qualification_mode", "standard"),
            "failure_class": None,
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

    @staticmethod
    def _note_ai_failure(ai_notes: list[str] | None, stage: str, reason: str) -> None:
        """向 ai_notes 追加带真实原因的失败标记，便于操作员判断重试/换配置。"""
        if ai_notes is not None:
            ai_notes.append(f"{stage}:ai-failed: {reason}")

    def _generate_combined_text(
        self,
        source_title: str,
        category: str,
        raw: dict[str, Any],
        target_language: str,
        target_site: str,
        ai_notes: list[str] | None = None,
    ) -> dict[str, str] | None:
        """一次调用同时生成标题与描述（交接文档 §9.3）；失败返回 None。"""
        if not _ai_enabled():
            return None
        template = self._effective_prompt("combined_text")
        contracted = apply_language_contract_to_prompt(template, "combined_text", target_language, target_site)
        context = listing_prompt_context(raw, title=source_title, category=category)
        prompt = format_prompt(contracted, title=source_title, **context)
        try:
            text = self._ai_client().chat([{"role": "user", "content": prompt}])
            data = _extract_json_object(text)
            if not isinstance(data, dict) or not data.get("optimized_title"):
                self._note_ai_failure(ai_notes, "text", "combined 输出未包含可用的 optimized_title")
                return None
            ensure_target_language_result("标题", data.get("optimized_title"), target_language)
            ensure_target_language_result("详情", data.get("description"), target_language)
            return {
                "title": self._normalized_title(data["optimized_title"]),
                "description": self._normalized_title(data.get("description") or ""),
            }
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "text", _ai_error_reason(exc))
            return None

    def _generate_title(
        self,
        source_title: str,
        category: str,
        raw: dict[str, Any],
        target_language: str,
        target_site: str,
        ai_notes: list[str] | None = None,
    ) -> str:
        """按目标语言生成标题；失败时返回空串（由调用方决定回退）。"""
        if not _ai_enabled():
            return ""
        template = self._effective_prompt("title")
        contracted = apply_language_contract_to_prompt(template, "title", target_language, target_site)
        context = listing_prompt_context(raw, title=source_title, category=category)
        prompt = format_prompt(
            contracted,
            title=source_title,
            title_identity_context=source_title,
            title_formula="product type + key real attributes + intended use, concise and scannable",
            title_priority_terms="",
            title_avoid_terms="",
            **context,
        )
        try:
            text = self._ai_client().chat([{"role": "user", "content": prompt}])
            ensure_target_language_result("标题", text, target_language)
            return self._normalized_title(text)
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "title", _ai_error_reason(exc))
            return ""

    def _generate_description(
        self,
        optimized_title: str,
        category: str,
        raw: dict[str, Any],
        target_language: str,
        target_site: str,
        ai_notes: list[str] | None = None,
    ) -> str:
        if not _ai_enabled():
            return ""
        template = self._effective_prompt("desc")
        contracted = apply_language_contract_to_prompt(template, "desc", target_language, target_site)
        context = listing_prompt_context(raw, title=optimized_title, category=category)
        prompt = format_prompt(contracted, title=optimized_title, **context)
        try:
            text = self._ai_client().chat([{"role": "user", "content": prompt}])
            ensure_target_language_result("详情", text, target_language)
            return self._normalized_title(text)
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "details", _ai_error_reason(exc))
            return ""

    def _translate_variant_values(
        self,
        raw: dict[str, Any],
        title: str,
        target_language: str,
        target_site: str,
        ai_notes: list[str] | None = None,
    ) -> dict[str, str]:
        """对齐原型 VARIANT_VALUE_TRANSLATION_PROMPT：把来源变种属性值翻译成目标语言可读显示名。

        返回 {原始值: 翻译值}；仅在 AI 启用且存在含中文的变种值时调用。
        """
        if not _ai_enabled():
            return {}
        variants = raw.get("source_variant_records") or []
        unique_values: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            attributes = variant.get("attributes")
            if not isinstance(attributes, dict):
                continue
            for value in attributes.values():
                text = str(value or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    unique_values.append(text)
        if not unique_values or not any(re.search(r"[\u4e00-\u9fff]", value) for value in unique_values):
            return {}
        profile = language_profile(target_language)
        template = self._effective_prompt("variant_values")
        contracted = apply_language_contract_to_prompt(template, "variant_values", target_language, target_site)
        prompt = format_prompt(
            contracted,
            title=str(title or "").strip()[:200],
            variant_options="\n".join(f"- {value}" for value in unique_values),
            target_language_name=profile.get("ai_language", target_language),
            language_code=target_language,
        )
        try:
            text = self._ai_client().chat([{"role": "user", "content": prompt}])
            data = _extract_json_object(text)
            mappings = data.get("mappings") if isinstance(data, dict) else None
            if not isinstance(mappings, list):
                self._note_ai_failure(ai_notes, "variant_values", "翻译输出缺少 mappings")
                return {}
            translations: dict[str, str] = {}
            for item in mappings:
                if not isinstance(item, dict):
                    continue
                raw_value = str(item.get("raw_value") or "").strip()
                export_value = str(item.get("export_value") or "").strip()
                if raw_value and export_value and raw_value in seen:
                    translations[raw_value] = export_value
            if translations:
                ai_notes.append("variant_values:ai")
            return translations
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "variant_values", _ai_error_reason(exc))
            return {}

    def _generate_grid_images(
        self,
        task_id: int,
        draft_id: int,
        raw: dict[str, Any],
        optimized_title: str,
        category: str,
        reference_urls: list[str],
        target_language: str,
        target_site: str,
        ai_notes: list[str] | None = None,
        vision_subject: str = "",
    ) -> tuple[list[str], str]:
        """按原型逻辑生成 2x2 四宫格并本地拆分为 4 张轮播图 + 1 张汇总图。"""
        if not _ai_enabled() or not reference_urls:
            return [], ""
        media_types = _media_types()
        if not media_types:
            return [], ""
        processor_cls, media_config_error, media_error = media_types
        try:
            processor = self._media_processor()
            template = self._effective_prompt("grid_image")
            contracted = apply_language_contract_to_prompt(template, "grid_image", target_language, target_site)
            context = listing_prompt_context(raw, title=optimized_title, category=category)
            if vision_subject:
                context["product_visual_identity"] = vision_subject
            prompt = format_prompt(contracted, title=optimized_title, **context)
            media = processor.generate(stage="grid_image", prompt=prompt, reference_values=reference_urls)
            # OCR 质量门：检出中文 → 定向重绘为英文（本地 OCR 后置验证器，对齐原型）
            media = self._repair_until_clean(processor, "grid_image", "four_grid", media, reference_urls, ai_notes)
            parts = processor.split_four_grid(media)
        except (media_config_error, media_error, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "four_grid", _ai_error_reason(exc))
            return [], ""
        carousel: list[str] = []
        summary_path = ""
        published = self._publish_media(processor, parts, task_id, draft_id)
        for part, value in zip(parts, published):
            if part.stage.startswith("grid_image_summary"):
                summary_path = str(value)
            elif part.stage.startswith("grid_image_"):
                carousel.append(str(value))
        return carousel[:4], summary_path

    def _generate_detail_images(
        self,
        task_id: int,
        draft_id: int,
        raw: dict[str, Any],
        optimized_title: str,
        category: str,
        reference_urls: list[str],
        target_language: str,
        target_site: str,
        ai_notes: list[str] | None = None,
        vision_subject: str = "",
    ) -> list[str]:
        if not _ai_enabled() or not reference_urls:
            return []
        media_types = _media_types()
        if not media_types:
            return []
        processor_cls, media_config_error, media_error = media_types
        try:
            processor = self._media_processor()
            template = self._effective_prompt("detail_image")
            contracted = apply_language_contract_to_prompt(template, "detail_image", target_language, target_site)
            context = listing_prompt_context(raw, title=optimized_title, category=category)
            if vision_subject:
                context["product_visual_identity"] = vision_subject
            prompt = format_prompt(contracted, title=optimized_title, **context)
            media = processor.generate(stage="detail_image", prompt=prompt, reference_values=reference_urls)
            # OCR 质量门：检出中文 → 定向重绘为英文（本地 OCR 后置验证器，对齐原型）
            media = self._repair_until_clean(processor, "detail_image", "detail_images", media, reference_urls, ai_notes)
        except (media_config_error, media_error, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "detail_images", _ai_error_reason(exc))
            return []
        return self._publish_media(processor, [media], task_id, draft_id)

    def _repair_until_clean(
        self,
        processor: Any,
        stage: str,
        note_key: str,
        media: Any,
        reference_urls: list[str],
        ai_notes: list[str] | None = None,
    ) -> Any:
        """OCR 质量门：生成后本地检出中文 → 把该图回传模型定向重绘（中文换成英文），最多 N 轮。

        对齐交接文档 §11.4/§15：OCR 是后置验证器，走「确定性验证 → AI 修复 → 确定性复验」。
        检出中文后保留商品与构图，仅把文字替换为英文；重绘失败或仍含中文时保留最后一次
        生成图并记 ai_note，不回退来源图（来源图必带中文促销文案，更差）。
        """
        if not ocr_gate_enabled():
            return media
        chinese = detect_chinese_text(media.content)
        if chinese is None:
            return media
        if not chinese:
            if ai_notes is not None:
                ai_notes.append(f"{note_key}:ocr_passed")
            return media
        media_types = _media_types()
        if not media_types:
            return media
        _, media_config_error, media_error = media_types
        rounds = 0
        while chinese and rounds < max_repair_rounds():
            rounds += 1
            try:
                media = processor.repair_generated(
                    stage=stage,
                    prompt=self._effective_prompt("image_repair_chinese"),
                    prior_content=media.content,
                    prior_content_type=media.content_type,
                    reference_values=reference_urls,
                )
            except (media_config_error, media_error, ValueError, OSError) as exc:
                if ai_notes is not None:
                    ai_notes.append(f"{note_key}:chinese_repair_failed")
                return media
            chinese = detect_chinese_text(media.content)
        if ai_notes is not None:
            if chinese:
                ai_notes.append(f"{note_key}:chinese_unresolved")
            else:
                ai_notes.append(f"{note_key}:chinese_repaired")
        return media

    def _effective_prompt(self, key: str) -> str:
        custom = self.repository.prompts()
        return str(custom.get(key) or DEFAULT_PROMPTS.get(key) or "")

    def _generate_size(
        self,
        raw: dict[str, Any],
        title: str,
        category: str,
        ai_notes: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """AI 尺寸预估（对齐原型 SIZE_PROMPT）：返回产品物流包装尺寸/重量；失败返回 None。"""
        if not _ai_enabled():
            return None
        template = self._effective_prompt("size")
        context = listing_prompt_context(raw, title=title, category=category)
        prompt = format_prompt(
            template,
            title=title,
            category=context["category"],
            category_path=context["category_path"],
            required_attributes=context["required_attributes"],
            source_data=self._size_source_text(raw, title),
        )
        try:
            text = self._ai_client().chat([{"role": "user", "content": prompt}])
            data = _extract_json_object(text)
            if not isinstance(data, dict):
                self._note_ai_failure(ai_notes, "product_dimensions", "size 输出未包含 JSON")
                return None
            length = self._number(data.get("length_cm"))
            width = self._number(data.get("width_cm"))
            height = self._number(data.get("height_cm"))
            weight = self._number(data.get("weight_g"))
            if not all(value is not None and value > 0 for value in (length, width, height, weight)):
                self._note_ai_failure(ai_notes, "product_dimensions", "size 输出缺少有效的长/宽/高/重量")
                return None
            return {
                "length_cm": length,
                "width_cm": width,
                "height_cm": height,
                "weight_g": weight,
                "confidence": self._text(data.get("confidence")) or "medium",
                "package_profile": self._text(data.get("package_profile")),
                "reason": self._text(data.get("reason")),
                "source": "ai_estimated",
            }
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "product_dimensions", _ai_error_reason(exc))
            return None

    @staticmethod
    def _size_source_text(raw: dict[str, Any], title: str) -> str:
        """将来源文本/属性/变种记录拼成 SIZE_PROMPT 的 source_data（对齐原型 _size_source_text）。"""
        parts: list[str] = []
        if title:
            parts.append(f"title: {title}")
        category = raw.get("category") or raw.get("source_category_path")
        if category:
            parts.append(f"category: {category}")
        attrs = ProductProcessingService._source_attributes_text(raw)
        if attrs:
            parts.append(f"attributes: {attrs}")
        for key in ("weight_text", "package_info_text", "freight_cny"):
            value = raw.get(key)
            if value not in (None, ""):
                parts.append(f"{key}: {value}")
        for variant in raw.get("source_variant_records") or []:
            if not isinstance(variant, dict):
                continue
            variant_attrs = variant.get("attributes")
            if isinstance(variant_attrs, dict) and variant_attrs:
                pairs = "; ".join(f"{key}: {value}" for key, value in variant_attrs.items() if value not in (None, ""))
                parts.append(f"variant: {pairs}")
        return " | ".join(parts)[:1200]

    def _source_stock(self, raw: dict[str, Any]) -> int:
        for key in ("stock", "stock_count", "quantity", "inventory"):
            value = self._number(raw.get(key))
            if value is not None and value > 0:
                return int(value)
        for variant in raw.get("source_variant_records") or []:
            if not isinstance(variant, dict):
                continue
            value = self._number(variant.get("stock"))
            if value is not None and value > 0:
                return int(value)
        return 0

    @staticmethod
    def _source_attributes_text(raw: dict[str, Any]) -> str:
        attributes = raw.get("source_attributes") or []
        if isinstance(attributes, dict):
            attributes = attributes.items()
        parts: list[str] = []
        for item in attributes:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
            else:
                try:
                    name, value = str(item[0] or "").strip(), str(item[1] or "").strip()
                except (TypeError, IndexError, KeyError):
                    continue
            if name and value and name.casefold() not in {"来源", "平台", "链接", "图片"}:
                parts.append(f"{name}: {value}")
        return "; ".join(parts[:12])

    def _ai_client(self) -> AiClient:
        if self._ai_instance is None:
            self._ai_instance = AiClient()
        return self._ai_instance

    def _image_to_data_url(self, image_url: str) -> str:
        """安全下载图片并转 base64 data URL（供多模态视觉识别，隔离下载/限字节）。"""
        if not is_safe_external_url(image_url):
            return ""
        try:
            image = fetch_public_image(image_url, max_bytes=8 * 1024 * 1024, timeout_seconds=30)
        except Exception:
            return ""
        content = getattr(image, "content", None) or b""
        if not content:
            return ""
        content_type = str(getattr(image, "content_type", None) or "image/jpeg").split(";", 1)[0].strip()
        return f"data:{content_type or 'image/jpeg'};base64,{base64.b64encode(content).decode('ascii')}"

    def _identify_subject(
        self,
        image_url: str,
        title: str,
        category: str,
        ai_notes: list[str] | None = None,
    ) -> str:
        """多模态识别主图中的可售主体（对齐原型 NativeVisualModelClient.judge）。

        返回英文主体描述，用于替换生图提示词 ``Product:`` 行的标题猜测，减少主体误判；
        任何失败返回空串，调用方回退原标题描述。
        """
        if not _ai_enabled() or not image_url:
            return ""
        data_url = self._image_to_data_url(image_url)
        if not data_url:
            return ""
        prompt = (
            "Identify the actual sellable product shown in the main image. "
            "The foreground sellable subject is the product to sell; ignore houses, rooms, tables, "
            "people, props, and background scenes. Reply with strict JSON only: "
            '{"sellable_subject": "<one short English noun phrase describing the sellable product, '
            'e.g. a round acrylic keychain with letter charms>", '
            '"material_evidence": "<visible material and structure details>", '
            '"background_scene": "<what the background shows>"}'
        )
        try:
            # 视觉识别与文本统一走便宜优先链：gpt-5.6-terra 打头（低价档+支持图像输入），
            # chat 降级链自动落 gpt-5.6-luna / gpt-5.4-mini
            text = self._ai_client().chat(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                model="gpt-5.6-terra",
            )
            data = _extract_json_object(text)
            subject = self._text((data or {}).get("sellable_subject"))
            if not subject:
                return ""
            ai_notes.append("subject_identity:ai")
            return str(subject).strip()[:160]
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "subject_identity", _ai_error_reason(exc))
            return ""

    def _media_processor(self) -> Any:
        if self._media_instance is None:
            media_types = _media_types()
            if not media_types:
                raise MediaUnavailableError("图片处理依赖缺失：需要安装 requests 与 Pillow")
            processor_cls, _, _ = media_types
            self._media_instance = processor_cls(config_provider=self._media_config_provider)
        return self._media_instance

    @staticmethod
    def _media_config_provider() -> dict[str, Any]:
        provider = resolve_ai_provider()
        image_section: dict[str, Any] = {}
        if provider.get("api_key"):
            image_section = {
                "base_url": provider.get("base_url") or "",
                "api_key": provider.get("api_key") or "",
                "model": provider.get("image_model") or "",
                "reference_model": provider.get("reference_image_model") or "",
                # 图片模型池：同中转多模型轮巡（对齐原型 _provider_order 游标轮巡）
                "image_models": list(provider.get("image_models") or ()),
            }
        # COS 图床：gitignored 本地配置 cos.local.json 优先，环境变量 WH_COS_* 可覆盖。
        # 对齐原型出图保存逻辑——生成图上传 COS 转外链后写进导入表，店小秘可直接读取。
        cos_config: dict[str, Any] = {}
        local_cos = Path(__file__).resolve().parent / "cos.local.json"
        try:
            if local_cos.is_file():
                loaded = json.loads(local_cos.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cos_config = {
                        "bucket": str(loaded.get("bucket") or "").strip(),
                        "region": str(loaded.get("region") or "").strip(),
                        "secret_id": str(loaded.get("secret_id") or "").strip(),
                        "secret_key": str(loaded.get("secret_key") or "").strip(),
                    }
        except (OSError, ValueError):
            cos_config = {}
        bucket = os.environ.get("WH_COS_BUCKET", "").strip() or cos_config.get("bucket", "")
        region = os.environ.get("WH_COS_REGION", "").strip() or cos_config.get("region", "")
        secret_id = os.environ.get("WH_COS_SECRET_ID", "").strip() or cos_config.get("secret_id", "")
        secret_key = os.environ.get("WH_COS_SECRET_KEY", "").strip() or cos_config.get("secret_key", "")
        cos_config = {}
        if bucket and region and secret_id and secret_key:
            cos_config = {"bucket": bucket, "region": region, "secret_id": secret_id, "secret_key": secret_key}
        # 系统配置优先于 cos.local.json（通过 BasicSettings Web UI 管理）
        sys_cos = provider.get("_sys_cos")
        if sys_cos and sys_cos.get("bucket") and sys_cos.get("region"):
            sys_cos_secret = {}  # COS 密钥在 basic_settings 的 secret_values 中，_media_config_provider 暂不读取
            if bucket and region and secret_id and secret_key:
                sys_cos_secret = cos_config  # 优先用 cos.local.json/env 的密钥
            cos_config = {
                "bucket": sys_cos["bucket"],
                "region": sys_cos["region"],
                **sys_cos_secret,
            }
        sys_backup = provider.get("_sys_backup_image_ai")
        backup_image = (
            {
                "base_url": sys_backup.get("base_url", ""),
                "api_key": sys_backup.get("api_key", ""),
                "model": sys_backup.get("model", ""),
                "reference_model": sys_backup.get("reference_model", ""),
            }
            if sys_backup and sys_backup.get("base_url") and sys_backup.get("api_key")
            else {}
        )
        sys_limits = provider.get("_sys_limits") or {}
        limits = {
            "image_retry_attempts": sys_limits.get("image_retry_attempts", 2),
            "grid_image_reference_max_count": 4,
            "detail_image_reference_max_count": 2,
            "image_provider_strategy": sys_limits.get("image_provider_strategy", "balanced"),
        }
        sys_updates = provider.get("_sys_updates") or {}
        if sys_updates.get("cos_prefix"):
            limits["cos_prefix"] = sys_updates["cos_prefix"]
        return {
            "image": image_section,
            "backup_image": backup_image,
            "cos": cos_config,
            "limits": limits,
        }

    def _publish_media(
        self,
        processor: Any,
        parts: list[Any],
        task_id: int,
        draft_id: int,
    ) -> list[str]:
        """对齐原型出图保存逻辑：生成图优先整组上传 COS 取得外链，店小秘可直接读取；
        任一上传失败或未配置 COS 时，整组回退本地保存（导出表再回退来源 http 图片）。"""
        urls: list[str] = []
        try:
            urls = [processor.upload_to_cos(part, task_id=task_id, draft_id=draft_id) for part in parts]
        except Exception:
            urls = []
        if urls:
            return urls
        return [
            str(self.assets.save_generated_image(task_id, draft_id, part.stage, part.content, part.suffix))
            for part in parts
        ]

    def _task_response(self, task: dict[str, Any], message: str = "") -> dict[str, Any]:
        items = task["items"]
        attention = sum(item["status"] == "attention_required" for item in items)
        failed = sum(item["status"] == "failed" for item in items)
        failure_classes = [item.get("result", {}).get("failure_class") for item in items]
        technical = sum(
            item["status"] in {"failed", "attention_required"} and bool(item.get("result", {}).get("retryable"))
            for item in items
        )
        configuration_blocked = sum(c == "configuration_blocked" for c in failure_classes)
        identity_review = sum(c == "identity_review_required" for c in failure_classes)
        logistics_review = sum(c == "logistics_review_required" for c in failure_classes)
        technical_retryable = sum(c == "technical_retryable" for c in failure_classes)
        outputs = {
            "dxm_import": task["output_file"],
            "error_report": task["error_report_file"],
            "log_file": "",
            "product_video_manifest": task["video_manifest_file"],
        }
        artifacts: list[dict[str, Any]] = []
        if task["output_file"]:
            artifacts.append({
                "artifact_id": f"dxm_import_{task['id']}",
                "kind": "dxm_import_workbook",
                "name": f"dxm_import_task_{task['id']}.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "path": task["output_file"],
            })
        if task["error_report_file"]:
            artifacts.append({
                "artifact_id": f"failure_manifest_{task['id']}",
                "kind": "failure_manifest",
                "name": f"error_report_task_{task['id']}.csv",
                "content_type": "text/csv",
                "path": task["error_report_file"],
            })
        if task["video_manifest_file"]:
            artifacts.append({
                "artifact_id": f"video_manifest_{task['id']}",
                "kind": "product_video_manifest",
                "name": f"product_video_manifest_task_{task['id']}.csv",
                "content_type": "text/csv",
                "path": task["video_manifest_file"],
            })
        settings = task["settings"]
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
                "settings": settings,
                "preflight_only": task["preflight_only"],
                "cleared_from_product_processing": task["cleared_from_product_processing"],
            },
        }
        manifest = {
            "manifest_id": f"pp_manifest_{task['id']}",
            "task_id": task["id"],
            "contract_version": "product-processing-result-manifest-v1",
            "item_counts": {
                "total": task["total_count"],
                "succeeded": task["success_count"],
                "failed": failed,
                "skipped": task["skipped_count"],
                "not_processed": task["skipped_count"],
                "attention_required": attention,
                "auto_recovery_pending": 0,
                "identity_review_required": identity_review,
                "logistics_review_required": logistics_review,
                "technical_retryable": technical_retryable,
                "configuration_blocked": configuration_blocked,
            },
            "created_at": task["created_at"],
        }
        processed_count = task["success_count"] + task["failed_count"] + task["skipped_count"]
        return {
            "task_id": task["id"],
            "total_count": task["total_count"],
            "success_count": task["success_count"],
            "failed_count": failed,
            "processed_count": processed_count,
            "not_processed_count": max(0, task["total_count"] - processed_count),
            "attention_required_count": attention,
            "auto_recovery_pending_count": 0,
            "identity_review_required_count": identity_review,
            "logistics_review_required_count": logistics_review,
            "technical_retryable_count": technical_retryable,
            "configuration_blocked_count": configuration_blocked,
            "skipped_count": task["skipped_count"],
            "output_file": task["output_file"],
            "error_report_file": task["error_report_file"],
            "video_manifest_file": task["video_manifest_file"],
            "target_site": settings.get("target_site", "US"),
            "target_language": settings.get("target_language", "en"),
            "processing_scope": settings.get("processing_scope", []),
            "qualification_mode": settings.get("qualification_mode", "standard"),
            "include_product_video": settings.get("product_video_template", False),
            "items": items,
            "task": task_projection,
            "outputs": outputs,
            "manifest": manifest,
            "artifacts": artifacts,
            "message": message,
        }

    @staticmethod
    def _failure_class_from_issue(issue: PolicyIssue) -> str:
        if issue.code in {"ip_risk_tagged", "ip_term_matched", "qualification_review_required",
                          "strict_external_source_missing", "strict_external_url_invalid"}:
            return "configuration_blocked"
        return "configuration_blocked" if issue.status == "attention_required" else "technical_retryable"

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
                "source_title": raw.get("source_title") or "",
                "main_image_url": raw.get("main_image_url") or "",
                "product_link": raw.get("product_link") or raw.get("source_url") or "",
                "source_url": raw.get("source_url") or "",
                "image_path": raw.get("image_path") or draft.get("image_path") or "",
                "category": raw.get("category") or "",
                "selection_criteria": raw.get("selection_criteria") or {},
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
