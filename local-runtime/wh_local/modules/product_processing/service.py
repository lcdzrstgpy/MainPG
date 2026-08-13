from __future__ import annotations

import base64
import importlib.util
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wh_local.data_collection.contracts import DailySelectionError
from wh_local.data_collection.public_image_fetch import FetchedPublicImage, fetch_public_image

from .ai_client import AiClient, AiProviderError
from .domain.content_reference_library import (
    append_content_reference,
    select_image_reference,
    select_title_reference,
)
from .domain.language_contract import (
    apply_language_contract_to_prompt,
    ensure_target_language_result,
    language_profile,
    normalize_target_language,
)
from .domain.description_contract import DescriptionContractError, normalize_five_point_description
from .domain.image_slots import DEFAULT_SLOT_IDS, apply_slot_overrides
from .domain.models import DEFAULT_PROMPTS, DailySelectionHandoffEnvelope, DailySelectionRun
from .domain.physical_dimensions import extract_physical_dimensions
from .domain.policy import PolicyIssue, is_safe_external_url, product_policy_issue, strict_external_url_issue
from .domain.prompts import DESCRIPTION_REPAIR_PROMPT, GRID_RUNTIME_CONTRACT, format_prompt
from .domain.visual_planner import listing_prompt_context
from .domain.workbooks import read_product_workbook
from .infrastructure.assets import ProductProcessingAssets
from .infrastructure.ocr_gate import (
    detect_chinese_text,
    inspect_visible_text,
    max_repair_rounds,
    ocr_diagnostics,
    ocr_gate_enabled,
)
from .infrastructure.repository import ProductProcessingRepository
from .infrastructure.preview_image_repository import (
    PreviewIdempotencyConflict,
    PreviewImageRepository,
    PreviewPublicationConflict,
    PreviewRevisionConflict,
)
from .preview_image_service import PreviewImageService
from .provider_config import resolve_ai_provider

_MEDIA_TYPES: tuple | None = None

# 来源尺寸/重量确定性提取（对齐原项目 five-stage 的 deterministic_fact_build，0 AI）
_DIMENSION_TRIPLE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|毫米|厘米)?",
    re.IGNORECASE,
)
_WEIGHT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|千克|公斤|克)", re.IGNORECASE)

# 阶段缓存 key 的易变簿记字段：处理完成时会写入 raw_payload（如 product_processing_receipt），
# 这些字段不影响提示词内容，必须从指纹中剔除，否则同一商品重跑会 key 变化导致缓存 miss。
_CACHE_VOLATILE_RAW_KEYS = frozenset(
    {
        "product_processing_receipt",
        "ai_notes",
        "result",
        "optimized_title",
        "carousel_image_paths",
        "grid_image_summary_path",
        "detail_image_paths",
        "processed_at",
        "task_ids",
    }
)

_STAGE_CACHE_VERSION = 3


def _ai_enabled() -> bool:
    """外部 AI 总开关：WH_PRODUCT_AI_ENABLED=0 时回退本地透传（测试/离线场景）。"""
    return str(os.environ.get("WH_PRODUCT_AI_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _media_public_base_url() -> str:
    """后端静态图床对外地址：WH_MEDIA_BASE_URL（如 https://media.example.com 或 http://公网IP:8010）。

    生成图本地保存于 assets/outputs，后端通过 /pp-media 静态挂载对外提供；
    设置该地址后，即使未配置 COS，导出表也能写入可访问的生成图 URL（出图效果不再回退来源图）。
    """
    return str(os.environ.get("WH_MEDIA_BASE_URL", "")).strip().rstrip("/")


def _cos_local_config_paths() -> list[Path]:
    """cos.local.json 候选位置：源码目录 + 打包资源目录（PyInstaller）。

    安装包构建时把 cos.local.json 放进可执行文件同目录（onedir）或打包资源
    （onefile 的 _MEIPASS），用户安装后零配置即可把生成图上传 COS 转外链。
    """
    candidates = [Path(__file__).resolve().parent / "cos.local.json"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "cos.local.json")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "cos.local.json")
    return candidates


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


def _is_non_retryable_provider_4xx(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and 400 <= status_code < 500 and status_code != 429


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


class ListingTextConfigurationError(RuntimeError):
    """A provider-side 4xx cannot be repaired by another listing-text stage."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GridImageOutput:
    carousel_urls: tuple[str, ...] = ()
    summary_url: str = ""
    carousel_media: tuple[Any, ...] = ()
    attempt_count: int = 0
    provider_status_class: str = ""
    stage_timings_ms: dict[str, int] = field(default_factory=dict)

    def __iter__(self):
        # Keep existing direct-call tests and integrations source compatible.
        yield list(self.carousel_urls)
        yield self.summary_url


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
        self._ai_lock = threading.Lock()  # 保护 AiClient 懒加载（多线程并行处理时避免重复创建）
        self._media_instance = None  # ProductImageProcessor (懒加载，可选依赖)
        self.preview_images = PreviewImageService(
            PreviewImageRepository(repository.database),
            repository,
            assets,
            publisher=self.publish_preview_media,
            trusted_public_url=self.is_trusted_cos_url,
            public_image_fetcher=public_image_fetcher,
            max_publish_workers=4,
        )
        # 主体识别结果缓存：同一来源主图只识别一次（批量任务大量重复商品时省 N 次 AI 调用）
        self._subject_cache: dict[str, dict[str, str]] = {}
        self._subject_cache_lock = threading.Lock()

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
            "media_base_url_configured": bool(_media_public_base_url()),
            "media_publish_configured": bool(media.get("cos_configured", False)) or bool(_media_public_base_url()),
            "cos_upload_prefix": "product-processing",
        }
        return {
            "available": True,
            "ready": dependency_status["openpyxl"] and dependency_status["python_multipart"],
            "app_dir": str(Path(__file__).parent),
            "app_file": str(Path(__file__)),
            "python": sys.executable,
            "worker": "local-synchronous-v1",
            "message": "本地产品处理引擎已就绪（five-stage 对齐：文本合并一次调用 + 尺寸确定性提取 + 四宫格出图 + 详情图本地合成）；失败时自动回退来源透传。",
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

    def drafts_revision(self, workspace_id: str = "local") -> str:
        """草稿池变更指纹，供前端轮询做容器级自动刷新（指纹不变则全量数据不变）。"""
        return self.repository.drafts_revision(workspace_id)

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

    def load_dimension_source(self, asset: dict[str, Any]) -> bytes:
        """Materialize only a server-registered canvas asset; never accepts a client path.

        The canvas repository has already proved workspace/item ownership before this
        adapter is invoked.  Remote sources still pass through the existing SSRF-safe,
        size-limited public image fetcher and are fetched only when the user renders.
        """
        managed_path = self._text(asset.get("managed_path"))
        if managed_path:
            return self.assets.require_managed_file(managed_path).read_bytes()
        source_url = self._text(asset.get("source_url"))
        if not source_url:
            raise ValueError("dimension source asset is unavailable")
        fetched = self._public_image_fetcher(source_url)
        return bytes(fetched.content)

    def publish_preview_media(
        self,
        content: bytes,
        content_type: str,
        suffix: str,
        content_hash: str,
        workspace_id: str,
    ) -> str:
        """Publish immutable original bytes for the final retained precheck set."""
        media_types = _media_types()
        if not media_types:
            raise MediaUnavailableError("图片处理依赖缺失：无法发布最终预审图片")
        from .infrastructure.media import GeneratedMedia  # noqa: PLC0415

        digest = hashlib.sha256(content).hexdigest()
        if content_hash and digest != str(content_hash).strip().lower():
            raise ValueError("preview image hash mismatch")
        namespace = hashlib.sha256(str(workspace_id).encode("utf-8")).hexdigest()[:20]
        media = GeneratedMedia(
            stage="preview-final",
            content=bytes(content),
            content_type=str(content_type or "image/jpeg"),
            suffix=str(suffix or ".jpg"),
            provider="preview-finalizer",
            model="original-bytes",
            reference_count=0,
        )
        url = self._media_processor().upload_content_addressed_to_cos(
            media,
            namespace=namespace,
            content_hash=digest,
            collection="preview-final",
        )
        if not url.lower().startswith("https://") or not is_safe_external_url(url):
            raise ValueError("COS returned a non-public preview image URL")
        return url

    def is_trusted_cos_url(self, value: str) -> bool:
        """Verify a legacy/final URL against the configured bucket with COS HEAD."""
        return self._media_processor().is_configured_cos_url(value, require_public=True)

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
                # 确认入池是草稿池的唯一入口：preview 不再自动建草稿，
                # 首次确认时用 handoff 载荷创建草稿（候选级幂等由 create_draft 保证）。
                draft, _created = self.create_draft(
                    self._draft_payload_from_handoff(handoff),
                    selection_run_id=handoff.run_id,
                    workspace_id=handoff.workspace_id,
                    handoff_id=handoff.handoff_id,
                    handoff_idempotency_key=handoff.idempotency_key,
                )
                created_count += 1
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
    def _draft_payload_from_handoff(
        handoff: DailySelectionHandoffEnvelope,
    ) -> dict[str, Any]:
        """Build a ``create_draft`` payload from a confirmed handoff.

        Preview no longer ingresses drafts, so confirmation is the only entry
        into the draft pool.  The handoff payload carries the candidate
        snapshot captured at confirmation time.
        """
        try:
            payload = json.loads(handoff.payload_json)
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        candidate = payload.get("candidate") or {}
        images = payload.get("images") or {}
        gallery = [str(value) for value in (images.get("gallery") or []) if value]
        detail = [str(value) for value in (images.get("detail") or []) if value]
        attributes = payload.get("attributes") or {}
        selection = payload.get("selection_metadata") or {}
        title = str(candidate.get("source_title") or "").strip()
        source_ref = str(
            candidate.get("source_url")
            or candidate.get("candidate_id")
            or candidate.get("offer_id")
            or ""
        ).strip()
        return {
            "source_type": "onebound_api",
            "candidate_id": str(candidate.get("candidate_id") or "").strip() or None,
            "offer_id": str(candidate.get("offer_id") or "").strip() or None,
            "source_platform": str(candidate.get("source_platform") or "1688").strip(),
            "source_ref": source_ref,
            "source_url": str(candidate.get("source_url") or "").strip() or None,
            "source_title": title,
            "title": title,
            "product_name": title,
            "image_url": str(images.get("main") or (gallery[0] if gallery else "")) or None,
            "source_image_urls": gallery,
            "source_detail_image_urls": detail,
            "source_variant_records": payload.get("skus") or [],
            "source_attributes": dict(attributes) if isinstance(attributes, dict) else {},
            "price_cny": candidate.get("price_cny"),
            "freight_cny": candidate.get("freight_cny"),
            "min_order_quantity": candidate.get("min_order_quantity"),
            "category_path": str(candidate.get("category_path") or "").strip() or None,
            "category_id": str(candidate.get("category_id") or "").strip() or None,
            "evidence": payload.get("source_evidence") or [],
            "selection_score": selection.get("selection_score"),
            "selection_reasons": list(selection.get("selection_reasons") or []),
            "risk_tags": list(selection.get("risk_tags") or []),
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
        # 生图提示词模板：A=标准商品海报（现有），B=高端模特视觉（防比价）。
        s["image_template"] = "B" if str(s.get("image_template") or "A").strip().upper() == "B" else "A"
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
        # 提交处理即把涉及草稿置为 processing：草稿池立即隐藏（前端过滤该状态），
        # 处理完成置 processed，失败/待确认回退 draft 以便重新出现在草稿池重试。
        if not preflight_only:
            self.repository.mark_drafts_status(draft_ids, "processing", workspace_id=workspace_id)
        if bool(payload.get("async_mode", True)):
            self._launch_background_execute(task["id"], workspace_id)
            return {**self._task_response(task, "任务已提交，正在后台处理"), "async_mode": True}
        completed = self._execute_task(task["id"], workspace_id)
        return self._task_response(completed, "草稿池预检已完成" if preflight_only else "产品处理任务已完成")

    def _launch_background_execute(self, task_id: int, workspace_id: str) -> None:
        """后台线程执行任务，立即返回让前端轮询实时进度。"""

        def _run() -> None:
            try:
                # 预热 OCR 引擎（首次加载 2-5s），避免首个草稿在图片质检时等待模型加载
                if ocr_gate_enabled():
                    ocr_diagnostics()
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
                    "elapsed_seconds": self._elapsed_seconds(task),
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
        if normalized == "dxm_final":
            # A fixed legacy path cannot prove workspace, snapshot revision or COS
            # completion. New clients must use the run-specific gated endpoint.
            raise ProductProcessingConflict(
                "请使用预审完成记录的专属下载链接，旧版固定路径已停用"
            )
        field = {
            "dxm": "output_file",
            "errors": "error_report_file",
            "video_manifest": "video_manifest_file",
        }.get(normalized)
        if field is None:
            raise ValueError("kind must be dxm, dxm_final, errors or video_manifest")
        try:
            return self.assets.require_managed_file(task[field])
        except FileNotFoundError as exc:
            raise ProductProcessingNotFound(str(exc)) from exc

    def task_preview(
        self, task_id: int, *, workspace_id: str = "local"
    ) -> dict[str, Any]:
        """预检数据：任务完成后逐商品展示标题/原图/生成图轮播/详情图/核心字段。

        用户已保存的预览覆盖优先展示；未覆盖时展示生成结果原值。
        """
        task = self._require_task(task_id, workspace_id)
        if task["status"] not in {"completed", "failed", "partial_failure"}:
            raise ProductProcessingConflict(f"任务尚未完成（当前状态：{task['status']}）")
        items = []
        for item in task["items"]:
            result = item.get("result") or {}
            draft_id = item.get("product_draft_id")
            draft = self.repository.get_draft(draft_id, workspace_id=workspace_id) if draft_id else None
            saved = (draft or {}).get("preview_overrides") or {}
            if not isinstance(saved, dict):
                saved = {}
            projected = self.preview_images.project_item_images(
                task_id=task_id,
                product_draft_id=int(draft_id or 0),
                result=result,
                saved=saved,
                workspace_id=workspace_id,
            )
            items.append({
                **self._preview_item(
                    item,
                    result,
                    saved,
                    preview_revision=int((draft or {}).get("preview_revision") or 0),
                ),
                **projected,
            })
        return {
            "task_id": task_id,
            "task": {
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "total_count": task["total_count"],
                "success_count": task["success_count"],
                "failed_count": task["failed_count"],
                "skipped_count": task["skipped_count"],
            },
            "item_count": len(items),
            "items": items,
        }

    def save_task_preview(
        self,
        task_id: int,
        items: list[dict[str, Any]],
        *,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        """保存预检覆盖：按 product_draft_id 写入草稿 preview_overrides_json。

        用户可改（标题/图片/核心字段）也可不修改默认保存；导出最终版表格时合并应用。
        """
        self._require_task(task_id, workspace_id)
        normalized = [
            {
                **entry,
                "overrides": self._clean_preview_overrides(
                    dict(entry.get("overrides") or {})
                ),
            }
            for entry in items
            if isinstance(entry, dict)
        ]
        try:
            saved_items = self.preview_images.save_preview(
                task_id,
                normalized,
                workspace_id=workspace_id,
            )
        except PreviewRevisionConflict as exc:
            raise ProductProcessingConflict(str(exc)) from exc
        except LookupError as exc:
            raise ProductProcessingNotFound(str(exc)) from exc
        return {"task_id": task_id, "saved_count": len(saved_items), "items": saved_items}

    def upload_preview_image(
        self,
        task_id: int,
        draft_id: int,
        content: bytes,
        filename: str,
        content_type: str,
        *,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        """Compatibility delegate: uploads are local assets until finalization."""
        return self.register_preview_upload(
            task_id,
            draft_id,
            content,
            filename,
            content_type,
            workspace_id=workspace_id,
        )

    def require_preview_target(
        self,
        task_id: int,
        draft_id: int,
        *,
        workspace_id: str = "local",
    ) -> None:
        try:
            self.preview_images.require_task_draft(task_id, draft_id, workspace_id)
        except LookupError as exc:
            raise ProductProcessingNotFound(str(exc)) from exc

    def register_preview_upload(
        self,
        task_id: int,
        draft_id: int,
        content: bytes,
        filename: str,
        content_type: str,
        *,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        try:
            return self.preview_images.register_upload(
                task_id=task_id,
                product_draft_id=draft_id,
                workspace_id=workspace_id,
                filename=filename,
                content_type=content_type,
                content=content,
            )
        except LookupError as exc:
            raise ProductProcessingNotFound(str(exc)) from exc

    def begin_preview_finalize(
        self,
        task_id: int,
        items: list[dict[str, Any]],
        *,
        workspace_id: str = "local",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        self._require_task(task_id, workspace_id)
        if not bool(self.engine_status()["diagnostics"]["config"].get("cos_configured")):
            raise ProductProcessingConflict("COS 图床未配置，请先在系统设置完成配置")
        normalized = [
            {
                **entry,
                "overrides": self._clean_preview_overrides(
                    dict(entry.get("overrides") or {})
                ),
            }
            for entry in items
        ]
        try:
            return self.preview_images.begin_finalize(
                task_id,
                normalized,
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
            )
        except (PreviewRevisionConflict, PreviewIdempotencyConflict, PreviewPublicationConflict) as exc:
            raise ProductProcessingConflict(str(exc)) from exc
        except LookupError as exc:
            raise ProductProcessingNotFound(str(exc)) from exc

    def preview_finalize_status(
        self,
        task_id: int,
        run_id: str,
        *,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        self._require_task(task_id, workspace_id)
        try:
            run = self.preview_images.get_finalize(run_id, workspace_id=workspace_id)
        except LookupError as exc:
            raise ProductProcessingNotFound(str(exc)) from exc
        if int(run.get("task_id") or 0) != int(task_id):
            raise ProductProcessingNotFound("preview finalization run not found")
        return run

    def retry_preview_finalize(
        self,
        task_id: int,
        run_id: str,
        *,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        self.preview_finalize_status(task_id, run_id, workspace_id=workspace_id)
        try:
            return self.preview_images.retry_finalize(run_id, workspace_id=workspace_id)
        except PreviewPublicationConflict as exc:
            raise ProductProcessingConflict(str(exc)) from exc

    def preview_finalize_download_path(
        self,
        task_id: int,
        run_id: str,
        *,
        workspace_id: str = "local",
    ) -> Path:
        self.preview_finalize_status(task_id, run_id, workspace_id=workspace_id)
        try:
            return self.preview_images.finalize_download_path(
                run_id,
                task_id,
                workspace_id=workspace_id,
            )
        except (LookupError, FileNotFoundError) as exc:
            raise ProductProcessingNotFound(str(exc)) from exc

    def export_final_workbook(self, task_id: int, *, workspace_id: str = "local") -> dict[str, Any]:
        """导出最终版店小秘表格：合并各商品已保存的预检覆盖后重新生成 xlsx。

        字段规则与原版一致（workbooks._dxm_export_rows 逐 SKU 行 + 规格组合去重）。
        """
        task = self._require_task(task_id, workspace_id)
        if task["status"] not in {"completed", "failed", "partial_failure"}:
            raise ProductProcessingConflict(f"任务尚未完成（当前状态：{task['status']}）")
        rows: list[dict[str, Any]] = []
        for item in task["items"]:
            result = item.get("result") or {}
            if not result.get("optimized_title"):
                continue
            merged = dict(result)
            draft_id = item.get("product_draft_id")
            draft = self.repository.get_draft(draft_id, workspace_id=workspace_id) if draft_id else None
            if draft and draft.get("preview_overrides"):
                merged["preview_overrides"] = draft["preview_overrides"]
            rows.append(merged)
        if not rows:
            raise ValueError("task has no successful products to export")
        from .domain import workbooks as wb_module  # noqa: PLC0415

        exported_rows = [export for row in rows for export in wb_module._dxm_export_rows(row)]
        if not exported_rows:
            raise ValueError("task has no exportable rows")
        workbook_path = self.assets.output_root / f"task_{task_id}" / f"dxm_import_task_{task_id}_final.xlsx"
        wb_module.create_result_workbook(rows, workbook_path)
        return {
            "task_id": task_id,
            "file": workbook_path.name,
            "row_count": len(exported_rows),
            "product_count": len(rows),
            "download": f"/api/product-processing/tasks/{task_id}/download?kind=dxm_final",
        }

    @staticmethod
    def _clean_preview_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
        """Normalize full desired state while retaining explicit empty manifests."""
        from .domain.preview_images import MANIFEST_KEY, PreviewImageManifest  # noqa: PLC0415

        cleaned: dict[str, Any] = {}
        for key in ("title", "description", "main_image"):
            value = str(overrides.get(key) or "").strip()
            if value:
                cleaned[key] = value
        for key in ("carousel_images", "detail_images"):
            values = [str(value).strip() for value in (overrides.get(key) or []) if str(value or "").strip()]
            if values:
                cleaned[key] = values
        image_slot_overrides = overrides.get("image_slot_overrides") or {}
        if isinstance(image_slot_overrides, dict):
            slot_patches: dict[str, dict[str, str]] = {}
            for raw_slot_id, raw_patch in image_slot_overrides.items():
                slot_id = str(raw_slot_id or "").strip()
                if slot_id not in DEFAULT_SLOT_IDS or not isinstance(raw_patch, dict):
                    continue
                url = str(raw_patch.get("url") or "").strip()
                if not url.lower().startswith(("http://", "https://")) and not url.startswith("/pp-media/"):
                    continue
                patch = {"url": url}
                asset_id = str(raw_patch.get("asset_id") or "").strip()
                if asset_id:
                    patch["asset_id"] = asset_id
                slot_patches[slot_id] = patch
            if slot_patches:
                cleaned["image_slot_overrides"] = slot_patches
        core_fields = overrides.get("core_fields") or {}
        if isinstance(core_fields, dict):
            core: dict[str, Any] = {}
            for key, value in core_fields.items():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                core[key] = value
            if core:
                cleaned["core_fields"] = core
        if MANIFEST_KEY in overrides:
            cleaned[MANIFEST_KEY] = PreviewImageManifest.from_value(
                overrides.get(MANIFEST_KEY)
            ).as_dict()
        return cleaned

    def _preview_item(
        self,
        item: dict[str, Any],
        result: dict[str, Any],
        saved: dict[str, Any],
        *,
        preview_revision: int = 0,
    ) -> dict[str, Any]:
        core_fields = saved.get("core_fields") or {}
        if not isinstance(core_fields, dict):
            core_fields = {}
        dimensions = result.get("product_dimensions") or {}
        if not isinstance(dimensions, dict):
            dimensions = {}
        # 标题/描述：覆盖优先，其次生成结果
        title = str(saved.get("title") or result.get("optimized_title") or "").strip()
        description = str(saved.get("description") or result.get("description") or "").strip()
        # 图片：覆盖优先，其次生成结果
        slots = apply_slot_overrides(result, saved)
        override_detail = [str(v).strip() for v in (saved.get("detail_images") or []) if str(v or "").strip()]
        override_main = str(saved.get("main_image") or "").strip()
        carousel_sources = [str(slot.get("value") or "").strip() for slot in slots if str(slot.get("value") or "").strip()]
        detail_sources = override_detail or list(result.get("detail_image_paths") or [])
        main_source = override_main or (carousel_sources[0] if carousel_sources else "")
        return {
            "item_id": item.get("id") or item.get("item_id"),
            "product_draft_id": item.get("product_draft_id"),
            "skc": item.get("skc") or "",
            "status": item.get("status") or "",
            "reason": item.get("reason") or "",
            "title": title,
            "description": description,
            "source_image_urls": [self._display_url(value) for value in (result.get("source_image_urls") or [])],
            "carousel_images": [self._display_url(value) for value in carousel_sources],
            "main_image": self._display_url(main_source),
            "detail_images": [self._display_url(value) for value in detail_sources],
            "image_slots": [
                {**slot, "value": self._display_url(slot.get("value"))}
                for slot in slots
            ],
            "physical_dimensions": result.get("physical_dimensions") or {},
            "preview_revision": preview_revision,
            "core_fields": {
                "sku": str(core_fields.get("sku") or result.get("sku") or "").strip(),
                "declared_price": core_fields.get("declared_price", result.get("declared_price")),
                "suggested_price": core_fields.get("suggested_price", result.get("suggested_price")),
                "stock": core_fields.get("stock", result.get("stock")),
                "category_path": str(core_fields.get("category_path") or result.get("category_path") or "").strip(),
                "category_id": str(core_fields.get("category_id") or result.get("category_id") or "").strip(),
                "length_cm": core_fields.get("length_cm", dimensions.get("length_cm")),
                "width_cm": core_fields.get("width_cm", dimensions.get("width_cm")),
                "height_cm": core_fields.get("height_cm", dimensions.get("height_cm")),
                "weight_g": core_fields.get("weight_g", dimensions.get("weight_g")),
            },
            "overrides": saved,
        }

    def _display_url(self, value: Any) -> str:
        """本地生成图路径 → /pp-media/ 相对 URL（后端静态图床）；http(s) 外链原样返回。"""
        text = str(value or "").strip()
        if not text:
            return ""
        if text.lower().startswith(("http://", "https://")):
            return text
        try:
            relative = Path(text).resolve().relative_to(self.assets.output_root.resolve())
        except (ValueError, OSError):
            return text
        return f"/pp-media/{relative.as_posix()}"

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
            return self._process_one(
                item,
                draft,
                settings,
                preflight_only,
                task_id=task_id,
                workspace_id=workspace_id,
            )

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
                    if (draft := drafts.get(item["product_draft_id"])) and not preflight_only:
                        self._mark_draft_failed(draft, workspace_id)
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
                            if (draft := drafts.get(item["product_draft_id"])) and not preflight_only:
                                self._mark_draft_failed(draft, workspace_id)

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

    def _mark_draft_failed(self, draft: dict[str, Any], workspace_id: str) -> None:
        """处理失败/待确认后把草稿状态回退为 draft，使其重新出现在草稿池供重试。

        仅在草稿仍处于 processing（本次提交刚置上的状态）时回退，避免影响已完成草稿。
        """
        if not draft or draft.get("status") != "processing":
            return
        self.repository.mark_drafts_status([draft["id"]], "draft", workspace_id=workspace_id)

    def _process_one(
        self,
        item: dict[str, Any],
        draft: dict[str, Any] | None,
        settings: dict[str, Any],
        preflight_only: bool,
        *,
        task_id: int,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        processing_started = time.perf_counter()
        stage_timings_ms: dict[str, int] = {}

        def record_stage(stage: str, started_at: float) -> None:
            key = stage if stage.endswith("_ms") else f"{stage}_ms"
            stage_timings_ms[key] = max(0, round((time.perf_counter() - started_at) * 1000))

        def timing_snapshot() -> dict[str, int]:
            return {
                **stage_timings_ms,
                "total_processing_ms": max(0, round((time.perf_counter() - processing_started) * 1000)),
            }

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
                    "stage_timings_ms": timing_snapshot(),
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
                    "stage_timings_ms": timing_snapshot(),
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
                    "stage_timings_ms": timing_snapshot(),
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
        provider_attempts: dict[str, int] = {}
        provider_status_classes: dict[str, str] = {}
        optimized_title = title
        description = self._text(draft.get("description") or raw.get("description"))
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
        vision_subject = ""
        vision_preliminary_title = ""
        combined_variant_translations: dict[str, str] = {}
        if not preflight_only:
            # 视觉识别先行：主图 → 可售主体 + 图像初步标题。初步标题是标题/描述生成的关键
            # 图像证据（标题必须基于真实商品生成而非直译来源标题），因此文本生成依赖其结果，
            # 不再与识别并行（识别失败时回退原来源标题流程，兼容无图像/未启用图像优化场景）。
            if source_image_urls and (
                (need_grid or need_detail)
                or ("title" in scope and settings.get("title_optimize", True))
            ):
                stage_started = time.perf_counter()
                vision_subject, vision_preliminary_title = self._identify_subject(
                    source_image_urls[0], title, category, ai_notes
                )
                record_stage("subject_identity", stage_started)

            local_title = title
            local_desc = description
            translations: dict[str, str] = {}
            description_candidate = ""
            description_contract_error = ""
            needs_title = "title" in scope and settings.get("title_optimize", True)
            # Selecting description processing means regenerate it from the active operator prompt;
            # do not silently preserve an arbitrary source description.
            needs_desc = "details" in scope
            if needs_title and needs_desc:
                stage_started = time.perf_counter()
                note_start = len(ai_notes)
                try:
                    combined = self._generate_combined_text(
                        title,
                        category,
                        raw,
                        target_language,
                        target_site,
                        ai_notes,
                        image_derived_title=vision_preliminary_title,
                    )
                except ListingTextConfigurationError as exc:
                    record_stage("combined_text", stage_started)
                    return {
                        **item,
                        "title": local_title,
                        "image_url": image_url,
                        "status": "attention_required",
                        "reason": str(exc),
                        "result": {
                            "error_type": "text_provider_configuration",
                            "failure_class": "configuration_blocked",
                            "operator_hint": "文本模型返回不可重试的 4xx；请检查模型路由、密钥或请求配置后重试",
                            "retryable": True,
                            "ai_notes": ai_notes,
                            "provider_attempts": {"combined_text": 1},
                            "provider_status_classes": {"combined_text": "non_retryable_4xx"},
                            "stage_timings_ms": timing_snapshot(),
                        },
                    }
                record_stage("combined_text", stage_started)
                provider_attempts["combined_text"] = (
                    0 if "text:cache-hit" in ai_notes[note_start:] else 1
                )
                provider_status_classes["combined_text"] = (
                    "output_contract_failed"
                    if any(
                        note.startswith("description_contract:ai-failed:")
                        for note in ai_notes[note_start:]
                    )
                    else "success"
                )
                if combined:
                    if combined.get("title"):
                        local_title = self._normalized_title(combined["title"])
                        needs_title = False
                    if combined.get("description"):
                        local_desc = combined["description"]
                        needs_desc = False
                    description_candidate = str(combined.get("description_candidate") or "")
                    description_contract_error = str(combined.get("description_contract_error") or "")
                    if combined.get("variant_translations"):
                        translations = combined["variant_translations"]
                    ai_notes.append("text:ai-combined")
            if needs_title:
                stage_started = time.perf_counter()
                try:
                    generated_title = self._generate_title(
                        title,
                        category,
                        raw,
                        target_language,
                        target_site,
                        ai_notes,
                        image_derived_title=vision_preliminary_title,
                    )
                except ListingTextConfigurationError as exc:
                    record_stage("title_generation", stage_started)
                    return {
                        **item,
                        "title": local_title,
                        "image_url": image_url,
                        "status": "attention_required",
                        "reason": str(exc),
                        "result": {
                            "error_type": "text_provider_configuration",
                            "failure_class": "configuration_blocked",
                            "operator_hint": "文本模型返回不可重试的 4xx；请检查模型路由、密钥或请求配置后重试",
                            "retryable": True,
                            "ai_notes": ai_notes,
                            "provider_attempts": {"title_generation": 1},
                            "provider_status_classes": {"title_generation": "non_retryable_4xx"},
                            "stage_timings_ms": timing_snapshot(),
                        },
                    }
                record_stage("title_generation", stage_started)
                provider_attempts["title_generation"] = 1
                provider_status_classes["title_generation"] = (
                    "success" if generated_title else "failed"
                )
                if generated_title:
                    local_title = generated_title
                    ai_notes.append("title:ai")
            if needs_desc:
                stage_started = time.perf_counter()
                try:
                    generated_desc = self._generate_description(
                        local_title,
                        category,
                        raw,
                        target_language,
                        target_site,
                        ai_notes,
                        image_derived_title=vision_preliminary_title,
                        prior_description=description_candidate,
                        contract_error=description_contract_error,
                    )
                except ListingTextConfigurationError as exc:
                    record_stage("description_repair", stage_started)
                    return {
                        **item,
                        "title": local_title,
                        "image_url": image_url,
                        "status": "attention_required",
                        "reason": str(exc),
                        "result": {
                            "error_type": "text_provider_configuration",
                            "failure_class": "configuration_blocked",
                            "operator_hint": "文本模型返回不可重试的 4xx；请检查模型路由、密钥或请求配置后重试",
                            "retryable": True,
                            "ai_notes": ai_notes,
                            "provider_attempts": {"description_repair": 1},
                            "provider_status_classes": {"description_repair": "non_retryable_4xx"},
                            "stage_timings_ms": timing_snapshot(),
                        },
                    }
                record_stage("description_repair", stage_started)
                provider_attempts["description_repair"] = 1
                provider_status_classes["description_repair"] = (
                    "success" if generated_desc else "failed"
                )
                if generated_desc:
                    local_desc = generated_desc
                    ai_notes.append("details:ai")
                    if description_candidate:
                        ai_notes.append("description_contract:repaired")
                        provider_status_classes["combined_text"] = "repaired_contract"
                    needs_desc = False
            if "details" in scope and needs_desc:
                return {
                    **item,
                    "title": local_title,
                    "image_url": image_url,
                    "status": "attention_required",
                    "reason": "产品描述未通过 Amazon 五点结构校验",
                    "result": {
                        "error_type": "description_contract_unmet",
                        "failure_class": "technical_retryable",
                        "operator_hint": "描述必须是 5 条不重复的英文要点（80-150 词）；已阻止劣质占位描述进入店小秘",
                        "retryable": True,
                        "ai_notes": ai_notes,
                        "provider_attempts": provider_attempts,
                        "provider_status_classes": provider_status_classes,
                        "stage_timings_ms": timing_snapshot(),
                    },
                }
            optimized_title, description, combined_variant_translations = local_title, local_desc, translations

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
                        "provider_attempts": provider_attempts,
                        "provider_status_classes": provider_status_classes,
                        "stage_timings_ms": timing_snapshot(),
                    },
                }

        # 变种属性值翻译（对齐原型 VARIANT_VALUE_TRANSLATION_PROMPT）：来源中文规格值 → 目标语言可读显示名。
        # combined 文本调用已并入翻译时直接复用（省一次独立 AI 调用）；否则按需单独调用。
        variant_value_translations: dict[str, str] = {}
        if not preflight_only:
            stage_started = time.perf_counter()
            if combined_variant_translations:
                variant_value_translations = combined_variant_translations
                ai_notes.append("variant_values:combined")
            else:
                variant_value_translations = self._translate_variant_values(
                    raw, optimized_title, target_language, target_site, ai_notes
                )
            record_stage("variant_translation", stage_started)

        product_dimensions: dict[str, Any] = {}
        if not preflight_only and "product_dimensions" in scope:
            stage_started = time.perf_counter()
            product_dimensions = self._generate_size(raw, optimized_title, category, ai_notes) or {}
            record_stage("product_dimensions", stage_started)
        physical_dimensions = extract_physical_dimensions(raw).model_dump(mode="json")

        grid_image_paths: list[str] = []
        grid_summary_path = ""
        grid_carousel_media: list[Any] = []
        detail_image_paths: list[str] = []
        # 图片编排（对齐原项目 five-stage：media_sku_local 一次出图 + 详情图本地合成 0 AI）：
        # 先出四宫格（1 次图像调用 + OCR 重绘≤1 轮），再拿四宫格分图本地拼详情图；
        # 四宫格不可用或本地合成含中文时才回退 AI 详情图生成。
        if need_grid:
            stage_started = time.perf_counter()
            grid_output = self._generate_grid_images(
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
                image_template=str(settings.get("image_template") or "A"),
                workspace_id=workspace_id,
            )
            record_stage("grid_pipeline", stage_started)
            grid_image_paths, grid_summary_path = grid_output
            grid_carousel_media = list(grid_output.carousel_media)
            provider_attempts["four_grid"] = grid_output.attempt_count
            provider_status_classes["four_grid"] = grid_output.provider_status_class
            stage_timings_ms.update(grid_output.stage_timings_ms)
            if not grid_image_paths:
                reason = next(
                    (
                        note.split(":ai-failed: ", 1)[-1]
                        for note in reversed(ai_notes)
                        if note.startswith("four_grid:ai-failed:")
                    ),
                    "生成图未通过四宫格质量门",
                )
                return {
                    **item,
                    "title": optimized_title,
                    "image_url": image_url,
                    "status": "attention_required",
                    "reason": reason,
                    "result": {
                        "error_type": "four_grid_quality_failed",
                        "failure_class": "technical_retryable",
                        "operator_hint": "生成图含跨区内容、显著 AI 文字或分辨率不合格，已阻止写入店小秘；请重试图片阶段",
                        "retryable": True,
                        "ai_notes": ai_notes,
                        "provider_attempts": provider_attempts,
                        "provider_status_classes": provider_status_classes,
                        "stage_timings_ms": timing_snapshot(),
                    },
                }
        if need_detail:
            if grid_image_paths:
                stage_started = time.perf_counter()
                detail_image_paths = self._generate_detail_images_local(
                    task_id,
                    draft["id"],
                    grid_carousel_media or grid_image_paths,
                    optimized_title,
                    category,
                    target_language,
                    ai_notes,
                    workspace_id=workspace_id,
                )
                record_stage("local_detail", stage_started)
            if not detail_image_paths:
                stage_started = time.perf_counter()
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
                    workspace_id=workspace_id,
                )
                record_stage("detail_generation", stage_started)
        if grid_image_paths:
            ai_notes.append("four_grid:ai")
        if detail_image_paths:
            ai_notes.append("detail_images:ai")

        image_manifest: list[dict[str, str]] = []
        image_roles = (
            ("carousel.hero", "hero"),
            ("carousel.detail", "detail"),
            ("carousel.lifestyle", "lifestyle"),
            ("carousel.dimension_background", "dimension_background"),
        )
        for index, value in enumerate(grid_image_paths):
            slot_id, role = image_roles[index] if index < len(image_roles) else (f"carousel.extra.{index + 1}", "extra")
            image_manifest.append({"slot_id": slot_id, "role": role, "value": value})

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
            "physical_dimensions": physical_dimensions,
            "stock": self._source_stock(raw),
            "ship_days": 2,
            "target_site": target_site,
            "target_language": target_language,
            "target_language_label": language_profile(target_language)["label"],
            "carousel_image_paths": grid_image_paths,
            "image_manifest": image_manifest,
            "grid_image_summary_path": grid_summary_path,
            "detail_image_paths": detail_image_paths,
            "ai_notes": ai_notes,
            "provider_attempts": provider_attempts,
            "provider_status_classes": provider_status_classes,
            "stage_timings_ms": timing_snapshot(),
            "preview_overrides": draft.get("preview_overrides") or {},
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

    @staticmethod
    def _note_media_unconfigured(ai_notes: list[str] | None, stage: str) -> None:
        """生成成功但未拿到任何可对外访问的 http(s) URL：COS 未配置且未设 WH_MEDIA_BASE_URL，
        导出表会静默回退来源图——显式标记，避免“看起来没处理”的误判。"""
        if ai_notes is not None:
            ai_notes.append(f"{stage}:media-unconfigured（COS未配置且未设WH_MEDIA_BASE_URL，导出将回退来源图）")

    @staticmethod
    def _note_content_reference(ai_notes: list[str] | None, label: str, reference_id: str) -> None:
        """记录实际采用的内容参考；仅用于诊断，不进入店小秘字段。"""
        note = f"{label}:{reference_id}"
        if ai_notes is not None and note not in ai_notes:
            ai_notes.append(note)

    @staticmethod
    def _text_messages(prompt: str, *, image_derived_title: str = "") -> list[dict[str, Any]]:
        """组装文本 AI 消息：图像初步标题作为 system 级证据前置。

        这样即使操作员自定义的提示词未引用 {image_derived_title}，模型也一定能收到
        主图识别的商品理解（标题据此生成而非直译来源标题）；无图像证据时保持单条消息。
        """
        if not str(image_derived_title or "").strip():
            return [{"role": "user", "content": prompt}]
        system = (
            "Image analysis of the source product main image (authoritative visual evidence of the "
            "actual product being sold). Draft title based only on what is visible in the image: "
            f"{str(image_derived_title).strip()[:300]}\n\n"
            "Generate the requested listing text primarily from this image-derived understanding of "
            "the actual product, combined with the source facts and instructions in the prompt below. "
            "The source title in the prompt is supporting evidence only: do not literally translate it. "
            "Do not invent any feature that is neither visible in the image nor stated in the source facts."
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    def _generate_combined_text(
        self,
        source_title: str,
        category: str,
        raw: dict[str, Any],
        target_language: str,
        target_site: str,
        ai_notes: list[str] | None = None,
        image_derived_title: str = "",
    ) -> dict[str, Any] | None:
        """一次调用同时生成标题、描述与变种属性值翻译（交接文档 §9.3 + VARIANT_VALUE_TRANSLATION_PROMPT）。

        变种翻译并入 combined 调用（对齐原项目 five-stage 的 combined_generation 一次文本调用
        产出多份内容），命中阶段级 DB 缓存时 0 次调用。失败返回 None。
        """
        if not _ai_enabled():
            return None
        variant_values = self._unique_variant_values(raw)
        variant_options_text = "\n".join(f"- {value}" for value in variant_values)
        profile = language_profile(target_language)
        template = self._effective_prompt("combined_text")
        contracted = apply_language_contract_to_prompt(template, "combined_text", target_language, target_site)
        context = listing_prompt_context(raw, title=source_title, category=category)
        description_template = self._effective_prompt("desc")
        description_contracted = apply_language_contract_to_prompt(
            description_template,
            "desc",
            target_language,
            target_site,
        )
        description_instructions = format_prompt(
            description_contracted,
            title=source_title,
            image_derived_title=image_derived_title,
            **context,
        )
        prompt = format_prompt(
            contracted,
            title=source_title,
            image_derived_title=image_derived_title,
            description_instructions=description_instructions,
            variant_options=variant_options_text,
            target_language_name=profile.get("ai_language", target_language),
            language_code=target_language,
            **context,
        )
        reference = select_title_reference(raw, title=source_title, category=category)
        prompt = append_content_reference(prompt, reference, kind="title")
        self._note_content_reference(ai_notes, "title_reference", reference.reference_id)
        input_data = {
            "title": source_title,
            "category": category,
            "raw": self._stable_raw(raw),
            "image_derived_title": image_derived_title,
        }
        cache_key = self._ai_stage_cache_key("combined_text", prompt=prompt, input_data=input_data)
        cached = self._load_ai_stage_cache("combined_text", cache_key)
        if cached is not None:
            if isinstance(cached, dict) and cached.get("title"):
                try:
                    ensure_target_language_result("标题", cached.get("title"), target_language)
                    cached_description = normalize_five_point_description(cached.get("description") or "")
                    ensure_target_language_result("详情", cached_description, target_language)
                except (DescriptionContractError, ValueError) as exc:
                    self._note_ai_failure(ai_notes, "description_contract", _ai_error_reason(exc))
                    cached_description = ""
                if ai_notes is not None:
                    ai_notes.append("text:cache-hit")
                return {
                    "title": self._normalized_title(cached["title"]),
                    "description": cached_description,
                    "description_candidate": "",
                    "description_contract_error": "",
                    "variant_translations": cached.get("variant_translations") or {},
                }
            return None
        try:
            text = self._ai_client().chat(self._text_messages(prompt, image_derived_title=image_derived_title))
            data = _extract_json_object(text)
            if not isinstance(data, dict) or not data.get("optimized_title"):
                self._note_ai_failure(ai_notes, "text", "combined 输出未包含可用的 optimized_title")
                return None
            ensure_target_language_result("标题", data.get("optimized_title"), target_language)
            description = ""
            description_candidate = str(data.get("description") or "").strip()[:1600]
            description_contract_error = ""
            try:
                description = normalize_five_point_description(description_candidate)
                ensure_target_language_result("详情", description, target_language)
            except (DescriptionContractError, ValueError) as exc:
                description_contract_error = _ai_error_reason(exc)
                self._note_ai_failure(ai_notes, "description_contract", description_contract_error)
            result = {
                "title": self._normalized_title(data["optimized_title"]),
                "description": description,
                "description_candidate": "" if description else description_candidate,
                "description_contract_error": "" if description else description_contract_error,
                "variant_translations": self._combined_variant_translations(data, variant_values),
            }
            if description:
                self._save_ai_stage_cache(
                    "combined_text", cache_key, output_data=result, prompt=prompt, input_data=input_data
                )
            return result
        except AiProviderError as exc:
            self._note_ai_failure(ai_notes, "text", _ai_error_reason(exc))
            if _is_non_retryable_provider_4xx(exc):
                raise ListingTextConfigurationError(
                    _ai_error_reason(exc),
                    status_code=exc.status_code,
                ) from exc
            return None
        except (ValueError, OSError) as exc:
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
        image_derived_title: str = "",
    ) -> str:
        """按目标语言生成标题；失败时返回空串（由调用方决定回退）。

        image_derived_title：主图识别出的图像初步标题，作为标题生成的权威视觉证据
        （标题据此生成而非直译来源标题）。
        """
        if not _ai_enabled():
            return ""
        template = self._effective_prompt("title")
        contracted = apply_language_contract_to_prompt(template, "title", target_language, target_site)
        context = listing_prompt_context(raw, title=source_title, category=category)
        prompt = format_prompt(
            contracted,
            title=source_title,
            image_derived_title=image_derived_title,
            title_identity_context=source_title,
            title_formula="product type + key real attributes + intended use, concise and scannable",
            title_priority_terms="",
            title_avoid_terms="",
            **context,
        )
        reference = select_title_reference(raw, title=source_title, category=category)
        prompt = append_content_reference(prompt, reference, kind="title")
        self._note_content_reference(ai_notes, "title_reference", reference.reference_id)
        try:
            text = self._ai_client().chat(self._text_messages(prompt, image_derived_title=image_derived_title))
            ensure_target_language_result("标题", text, target_language)
            return self._normalized_title(text)
        except AiProviderError as exc:
            self._note_ai_failure(ai_notes, "title", _ai_error_reason(exc))
            if _is_non_retryable_provider_4xx(exc):
                raise ListingTextConfigurationError(
                    _ai_error_reason(exc),
                    status_code=exc.status_code,
                ) from exc
            return ""
        except (ValueError, OSError) as exc:
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
        image_derived_title: str = "",
        prior_description: str = "",
        contract_error: str = "",
    ) -> str:
        """按目标语言生成五点描述；失败时返回空串（由调用方决定回退）。"""
        if not _ai_enabled():
            return ""
        template = self._effective_prompt("desc")
        contracted = apply_language_contract_to_prompt(template, "desc", target_language, target_site)
        context = listing_prompt_context(raw, title=optimized_title, category=category)
        candidate = self._normalized_description(prior_description)[:1600]
        if candidate:
            repair_template = apply_language_contract_to_prompt(
                DESCRIPTION_REPAIR_PROMPT, "desc", target_language, target_site
            )
            prompt = format_prompt(
                repair_template,
                title=optimized_title,
                image_derived_title=image_derived_title,
                operator_description_instructions=contracted,
                candidate_description=candidate,
                contract_error=str(contract_error or "format validation failed")[:240],
                **context,
            )
            if ai_notes is not None:
                ai_notes.append("description_contract:repair-requested")
        else:
            prompt = format_prompt(
                contracted, title=optimized_title, image_derived_title=image_derived_title, **context
            )
        try:
            text = self._ai_client().chat(self._text_messages(prompt, image_derived_title=image_derived_title))
            ensure_target_language_result("详情", text, target_language)
            return normalize_five_point_description(text)
        except AiProviderError as exc:
            self._note_ai_failure(ai_notes, "details", _ai_error_reason(exc))
            if _is_non_retryable_provider_4xx(exc):
                raise ListingTextConfigurationError(
                    _ai_error_reason(exc),
                    status_code=exc.status_code,
                ) from exc
            return ""
        except (DescriptionContractError, ValueError, OSError) as exc:
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
        image_template: str = "A",
        workspace_id: str = "local",
    ) -> GridImageOutput:
        """按原型逻辑生成 2x2 四宫格并本地拆分为 4 张轮播图 + 1 张汇总图。"""
        if not _ai_enabled() or not reference_urls:
            return GridImageOutput()
        media_types = _media_types()
        if not media_types:
            return GridImageOutput()
        processor_cls, media_config_error, media_error = media_types
        attempt_count = 0
        provider_status_class = ""
        grid_timings_ms: dict[str, int] = {}
        try:
            processor = self._media_processor()
            prompt_key = "grid_image_b" if str(image_template).strip().upper() == "B" else "grid_image"
            template = self._effective_prompt(prompt_key)
            contracted = apply_language_contract_to_prompt(template, "grid_image", target_language, target_site)
            context = listing_prompt_context(raw, title=optimized_title, category=category)
            if vision_subject:
                context["product_visual_identity"] = vision_subject
            prompt = format_prompt(contracted, title=optimized_title, **context)
            reference = select_image_reference(raw, title=optimized_title, category=category)
            prompt = append_content_reference(prompt, reference, kind="image")
            # 用户自定义提示词和参考库可以改变风格，但不能覆盖拆图/无 AI 文字运行合同。
            prompt = f"{prompt.rstrip()}\n\n{GRID_RUNTIME_CONTRACT}"
            self._note_content_reference(ai_notes, "image_reference", reference.reference_id)
            is_b_template = str(image_template).strip().upper() == "B"
            generation_started = time.perf_counter()
            try:
                if is_b_template:
                    media = processor.generate(
                        stage="grid_image",
                        prompt=prompt,
                        reference_values=reference_urls,
                        layout_scaffold=True,
                    )
                else:
                    media = processor.generate(stage="grid_image", prompt=prompt, reference_values=reference_urls)
            finally:
                grid_timings_ms["grid_generation_ms"] = max(
                    0,
                    round((time.perf_counter() - generation_started) * 1000),
                )
            attempt_count = max(0, int(getattr(media, "attempt_count", 1) or 1))
            provider_status_class = str(getattr(media, "provider_status_class", "success") or "success")
            # OCR 质量门：四宫格禁止模型写字；显著中英文排版都会触发一次定向修复。
            validation_started = time.perf_counter()
            try:
                media = self._repair_until_clean(
                    processor,
                    "grid_image",
                    "four_grid",
                    media,
                    reference_urls,
                    ai_notes,
                    allow_paid_repair=not is_b_template,
                )
                parts = processor.split_four_grid(media)
            finally:
                grid_timings_ms["grid_validation_ms"] = max(
                    0,
                    round((time.perf_counter() - validation_started) * 1000),
                )
        except (media_config_error, media_error, ValueError, OSError) as exc:
            attempt_count = max(attempt_count, int(getattr(exc, "attempt_count", 0) or 0))
            provider_status_class = str(
                getattr(exc, "status_class", "") or provider_status_class or "failed"
            )
            self._note_ai_failure(ai_notes, "four_grid", _ai_error_reason(exc))
            return GridImageOutput(
                attempt_count=attempt_count,
                provider_status_class=provider_status_class,
                stage_timings_ms=grid_timings_ms,
            )
        carousel: list[str] = []
        carousel_media: list[Any] = []
        summary_path = ""
        persist_started = time.perf_counter()
        published = self._persist_media_for_preview(parts, task_id, draft_id, workspace_id)
        grid_timings_ms["persist_ms"] = max(
            0,
            round((time.perf_counter() - persist_started) * 1000),
        )
        for part, value in zip(parts, published):
            if part.stage.startswith("grid_image_summary"):
                summary_path = str(value)
            elif part.stage.startswith("grid_image_"):
                carousel.append(str(value))
                carousel_media.append(part)
        return GridImageOutput(
            tuple(carousel[:4]),
            summary_path,
            tuple(carousel_media[:4]),
            attempt_count,
            provider_status_class,
            grid_timings_ms,
        )

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
        workspace_id: str = "local",
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
            reference = select_image_reference(raw, title=optimized_title, category=category)
            prompt = append_content_reference(prompt, reference, kind="image")
            self._note_content_reference(ai_notes, "image_reference", reference.reference_id)
            media = processor.generate(stage="detail_image", prompt=prompt, reference_values=reference_urls)
            # OCR 质量门：检出中文 → 定向重绘为英文（本地 OCR 后置验证器，对齐原型）
            media = self._repair_until_clean(processor, "detail_image", "detail_images", media, reference_urls, ai_notes)
        except (media_config_error, media_error, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "detail_images", _ai_error_reason(exc))
            return []
        return self._persist_media_for_preview([media], task_id, draft_id, workspace_id)

    def _generate_detail_images_local(
        self,
        task_id: int,
        draft_id: int,
        source_values: list[Any],
        optimized_title: str,
        category: str,
        target_language: str,
        ai_notes: list[str] | None = None,
        workspace_id: str = "local",
    ) -> list[str]:
        """本地合成详情图（0 AI，对齐原项目 detail_image_local_synthesis）。

        用四宫格分图（已是英文干净图）Pillow 拼一张 1024 详情海报；本地合成文字为确定性
        英文，OCR 质量门正常直接通过。合成失败或合成结果仍含中文时返回 []，由调用方回退
        AI 详情图生成（含 OCR 修复循环）。
        """
        if not source_values:
            return []
        media_types = _media_types()
        if not media_types:
            return []
        processor_cls, media_config_error, media_error = media_types
        try:
            processor = self._media_processor()
            content = self._compose_local_detail_image(source_values, optimized_title, category, target_language)
            if not content:
                return []
            chinese = detect_chinese_text(content)
            if chinese:
                if ai_notes is not None:
                    ai_notes.append("detail_images:chinese_unresolved")
                return []
            if ai_notes is not None:
                ai_notes.append("detail_images:local_synthesis")
                ai_notes.append("detail_images:ocr_passed")
            from .infrastructure.media import GeneratedMedia  # noqa: PLC0415

            media = GeneratedMedia(
                stage="detail_image",
                content=content,
                content_type="image/jpeg",
                suffix=".jpg",
                provider="local-synthesis",
                model="pillow",
                reference_count=min(4, len(source_values)),
            )
            return self._persist_media_for_preview([media], task_id, draft_id, workspace_id)
        except (media_config_error, media_error, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "detail_images", _ai_error_reason(exc))
            return []

    @staticmethod
    def _local_source_bytes(value: Any) -> bytes | None:
        """读取本地路径或 http(s) 图片字节（供本地合成详情图用）；失败返回 None。"""
        if not value:
            return None
        if isinstance(value, bytes):
            return value
        content = getattr(value, "content", None)
        if isinstance(content, bytes):
            return content
        value = str(value)
        if Path(value).is_file():
            try:
                return Path(value).read_bytes()
            except OSError:
                return None
        if is_safe_external_url(value):
            try:
                image = fetch_public_image(value, max_bytes=8 * 1024 * 1024, timeout_seconds=30)
            except Exception:
                return None
            return getattr(image, "content", None) or b""
        return None

    @staticmethod
    def _compose_local_detail_image(
        source_values: list[Any],
        title: str,
        category: str,
        target_language: str,
    ) -> bytes | None:
        """用最多 4 张来源图（四宫格分图）本地合成 1024×1024 详情海报。

        模板卡池 D/E/F 随机抽取（每张详情图随机一种版式）：
          D 极简白底：标题置顶 + 居中大图 + 底部三小图 + 类目注脚；
          E 圆形拼贴：主图居中 + 三张圆形蒙版嵌图 + 顶部压暗标题条；
          F 混合形状：主图居中 + 圆形/圆角方形/菱形三种蒙版嵌图 + 顶部压暗标题条。

        文案全部为确定性英文（标题/类目），不产生中文；返回 JPEG 字节，素材不足返回 None。
        """
        import random  # noqa: PLC0415
        from io import BytesIO  # noqa: PLC0415
        from PIL import Image, ImageDraw, ImageFont  # type: ignore  # noqa: PLC0415

        images: list[Image.Image] = []
        for value in source_values[:4]:
            data = ProductProcessingService._local_source_bytes(value)
            if not data:
                continue
            try:
                with Image.open(BytesIO(data)) as opened:
                    images.append(opened.convert("RGB"))
            except Exception:
                continue
        if not images:
            return None
        while len(images) < 4:
            images.append(images[len(images) % len(images)])

        target = 1024

        def font(size: int, *, bold: bool = False):
            candidates = [
                "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
                "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            ]
            for candidate in candidates:
                try:
                    return ImageFont.truetype(candidate, size)
                except Exception:
                    continue
            return ImageFont.load_default()

        def cover(image: Image.Image, box_w: int, box_h: int) -> Image.Image:
            ratio = max(box_w / image.width, box_h / image.height)
            resized = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.Resampling.LANCZOS,
            )
            left = max((resized.width - box_w) // 2, 0)
            top = max((resized.height - box_h) // 2, 0)
            return resized.crop((left, top, left + box_w, top + box_h))

        def paste_rounded(base: Image.Image, image: Image.Image, box: tuple[int, int, int, int], radius: int = 22) -> None:
            part = cover(image, box[2] - box[0], box[3] - box[1])
            mask = Image.new("L", part.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, part.width, part.height), radius=radius, fill=255)
            base.paste(part, box[:2], mask)

        def shape_mask(size: int, shape: str) -> Image.Image:
            mask = Image.new("L", (size, size), 0)
            shape_draw = ImageDraw.Draw(mask)
            if shape == "circle":
                shape_draw.ellipse((0, 0, size - 1, size - 1), fill=255)
            elif shape == "square":
                shape_draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.18), fill=255)
            elif shape == "diamond":
                shape_draw.polygon(
                    [(size / 2, 0), (size - 1, size / 2), (size / 2, size - 1), (0, size / 2)], fill=255
                )
            return mask

        def paste_shaped(base: Image.Image, image: Image.Image, center, size: int, shape: str, ring: int = 10) -> None:
            """以指定形状蒙版把图嵌到画布上，外围带一圈白边（ring px）。"""
            cx, cy = center
            mask = shape_mask(size, shape)
            base.paste(Image.new("RGB", (size, size), (255, 255, 255)), (cx - size // 2, cy - size // 2), mask)
            inner = size - 2 * ring
            part = cover(image, inner, inner)
            base.paste(part, (cx - inner // 2, cy - inner // 2), shape_mask(inner, shape))

        measure_draw = ImageDraw.Draw(Image.new("RGB", (target, target), (255, 255, 255)))
        text_width = measure_draw.textlength

        def wrap(text: str, text_font, max_width: int, max_lines: int) -> list[str]:
            words = text.split()
            lines: list[str] = []
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and text_width(candidate, font=text_font) > max_width:
                    lines.append(current)
                    current = word
                    if len(lines) >= max_lines:
                        break
                else:
                    current = candidate
            if current and len(lines) < max_lines:
                lines.append(current)
            return lines or [text[:40]]

        clean_text = re.sub(r"\s+", " ", str(title or "")).strip(" -_|/")
        title_text = clean_text[:96] or ("Detalle del producto" if target_language == "es" else "Product Detail")
        category_text = (re.sub(r"\s+", " ", str(category or "")).strip(" -_|/")[:44]) or "Selected Detail"

        def compose_d() -> Image.Image:
            """D 极简白底：标题置顶 + 居中大图 + 底部三小图 + 类目注脚"""
            canvas = Image.new("RGB", (target, target), (255, 255, 255))
            text_draw = ImageDraw.Draw(canvas)
            title_font = font(38, bold=True)
            sub_font = font(19)
            y = 64
            for line in wrap(title_text, title_font, 880, 2):
                text_draw.text((52, y), line, font=title_font, fill=(28, 30, 32))
                y += 44
            text_draw.rounded_rectangle((54, y + 8, 118, y + 16), radius=4, fill=(232, 150, 62))
            y += 38
            text_draw.text((54, y), category_text.upper(), font=sub_font, fill=(120, 123, 124))
            hero_top = 210
            hero_h = 540
            paste_rounded(canvas, images[0], (62, hero_top, target - 62, hero_top + hero_h), 26)
            margin, gap, radius = 62, 20, 18
            thumbs_w = (target - 2 * margin - 2 * gap) // 3
            thumbs_h = target - hero_top - hero_h - 56
            for index in range(3):
                x0 = margin + index * (thumbs_w + gap)
                paste_rounded(
                    canvas,
                    images[index + 1],
                    (x0, hero_top + hero_h + 24, x0 + thumbs_w, hero_top + hero_h + 24 + thumbs_h),
                    radius,
                )
            return canvas

        def compose_e() -> Image.Image:
            """E 圆形拼贴：主图居中 + 三张圆形蒙版嵌图（无文字覆盖）"""
            canvas = Image.new("RGB", (target, target), (244, 242, 238))
            canvas.paste(cover(images[0], 820, 820), (102, 102))
            paste_shaped(canvas, images[1], (150, 150), 290, "circle")
            paste_shaped(canvas, images[2], (874, 150), 290, "circle")
            paste_shaped(canvas, images[3], (512, 950), 290, "circle")
            return canvas

        def compose_f() -> Image.Image:
            """F 混合形状：主图居中 + 圆形/圆角方形/菱形蒙版嵌图（无文字覆盖）"""
            canvas = Image.new("RGB", (target, target), (244, 242, 238))
            canvas.paste(cover(images[0], 820, 820), (102, 102))
            paste_shaped(canvas, images[1], (150, 150), 300, "circle")
            paste_shaped(canvas, images[2], (874, 150), 300, "square")
            paste_shaped(canvas, images[3], (512, 950), 320, "diamond")
            return canvas

        compositor = {"D": compose_d, "E": compose_e, "F": compose_f}[random.choice(("D", "E", "F"))]
        canvas = compositor()
        buffer = BytesIO()
        canvas.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()

    def _repair_until_clean(
        self,
        processor: Any,
        stage: str,
        note_key: str,
        media: Any,
        reference_urls: list[str],
        ai_notes: list[str] | None = None,
        *,
        allow_paid_repair: bool = True,
    ) -> Any:
        """Run deterministic text/structure gates, repair once on failure, then revalidate."""
        # 四宫格会直接进入店小秘，运行环境开关不得绕过显著文字门。
        if note_key != "four_grid" and not ocr_gate_enabled():
            return media
        media_types = _media_types()
        if not media_types:
            if note_key == "four_grid":
                raise ValueError("四宫格确定性质检不可用")
            return media
        _, media_config_error, media_error = media_types

        def inspect(value: Any) -> list[str]:
            inspection = inspect_visible_text(value.content)
            if inspection is None:
                if note_key == "four_grid":
                    raise ValueError("四宫格 OCR 质量门不可用，已阻止未验证生成图")
                return []
            found = list(inspection["chinese"])
            if note_key == "four_grid":
                found.extend(inspection["prominent"])
                try:
                    processor.validate_four_grid(value)
                except (media_config_error, media_error, ValueError, OSError):
                    found.append("grid_structure_invalid")
            return list(dict.fromkeys(found))

        found = inspect(media)
        if not found:
            if ai_notes is not None:
                ai_notes.append(f"{note_key}:ocr_passed")
            return media
        if not allow_paid_repair:
            if ai_notes is not None:
                ai_notes.append(f"{note_key}:quality_unresolved")
            raise ValueError("四宫格未通过文字、结构或独立性质量门；B 模板已停止付费重绘")
        rounds = 0
        while found and rounds < max_repair_rounds():
            rounds += 1
            try:
                media = processor.repair_generated(
                    stage=stage,
                    prompt=self._effective_prompt(
                        "image_repair_grid" if note_key == "four_grid" else "image_repair_chinese"
                    ),
                    prior_content=media.content,
                    prior_content_type=media.content_type,
                    reference_values=reference_urls,
                )
            except (media_config_error, media_error, ValueError, OSError) as exc:
                if ai_notes is not None:
                    ai_notes.append(f"{note_key}:quality_repair_failed")
                if note_key == "four_grid":
                    raise ValueError("四宫格文字或结构修复失败") from exc
                return media
            found = inspect(media)
        if ai_notes is not None:
            if found:
                ai_notes.append(f"{note_key}:quality_unresolved")
            else:
                ai_notes.append(f"{note_key}:quality_repaired")
        if found and note_key == "four_grid":
            raise ValueError("四宫格仍含显著 AI 文字或无有效中心分隔，已阻止拆图")
        return media

    def _effective_prompt(self, key: str) -> str:
        custom = self.repository.prompts()
        return str(custom.get(key) or DEFAULT_PROMPTS.get(key) or "")

    @staticmethod
    def _stable_raw(raw: dict[str, Any]) -> dict[str, Any]:
        """返回剔除易变簿记字段的来源数据副本，用于阶段缓存 key 的稳定指纹。"""
        return {key: value for key, value in (raw or {}).items() if key not in _CACHE_VOLATILE_RAW_KEYS}

    def _ai_stage_cache_key(self, stage: str, *, prompt: str = "", input_data: Any = None) -> str:
        """阶段级 AI 缓存 key：stage + 提示词哈希 + 输入内容哈希（对齐原项目 ai_stage_cache）。"""
        payload = {
            "version": _STAGE_CACHE_VERSION,
            "stage": stage,
            "prompt": str(prompt or ""),
            "input": input_data if input_data is not None else {},
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_ai_stage_cache(self, stage: str, cache_key: str) -> Any:
        """读取阶段级 AI 缓存；命中返回输出对象，否则 None。缓存异常不影响主流程。"""
        if not cache_key:
            return None
        try:
            cached = self.repository.get_ai_stage_cache(cache_key, workspace_id="local")
        except Exception:
            return None
        return cached.get("output") if cached else None

    def _save_ai_stage_cache(
        self,
        stage: str,
        cache_key: str,
        *,
        output_data: Any,
        prompt: str = "",
        input_data: Any = None,
    ) -> None:
        if not cache_key:
            return
        try:
            prompt_hash = hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()
            input_hash = hashlib.sha256(
                json.dumps(input_data if input_data is not None else {}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            self.repository.save_ai_stage_cache(
                cache_key,
                workspace_id="local",
                stage=stage,
                model_signature="",
                prompt_hash=prompt_hash,
                input_hash=input_hash,
                output_data=output_data,
            )
        except Exception:
            # 缓存写失败不影响处理主流程
            return

    def _generate_size(
        self,
        raw: dict[str, Any],
        title: str,
        category: str,
        ai_notes: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """物流尺寸/重量预估（对齐原项目 five-stage 的 deterministic_fact_build + SIZE_PROMPT）。

        优先从来源属性/变种/重量文本确定性提取显式数值（0 AI）；只有提取不完整时才走 AI
        补缺，并先用阶段级 DB 缓存复用相同输入的结果。失败时返回已提取的确定性部分。
        """
        deterministic = self._extract_deterministic_size(raw)
        has_full = deterministic and all(
            deterministic.get(key) for key in ("length_cm", "width_cm", "height_cm", "weight_g")
        )
        if has_full:
            if ai_notes is not None:
                ai_notes.append("product_dimensions:deterministic")
            return deterministic
        if not _ai_enabled():
            # AI 未启用时至少保留来源显式数值（可能缺字段，导出留空即可）
            return deterministic
        known = deterministic or {}
        input_data = {
            "title": title,
            "category": category,
            "raw": self._stable_raw(raw),
            "known": known,
        }
        cache_key = self._ai_stage_cache_key("size", input_data=input_data)
        cached = self._load_ai_stage_cache("size", cache_key)
        if cached is not None:
            if ai_notes is not None:
                ai_notes.append("product_dimensions:cache-hit")
            return cached if isinstance(cached, dict) else None
        template = self._effective_prompt("size")
        context = listing_prompt_context(raw, title=title, category=category)
        source_data = self._size_source_text(raw, title)
        if known:
            known_lines = "、".join(
                f"{key}={value}"
                for key, value in (
                    ("length_cm", known.get("length_cm")),
                    ("width_cm", known.get("width_cm")),
                    ("height_cm", known.get("height_cm")),
                    ("weight_g", known.get("weight_g")),
                )
                if value is not None
            )
            source_data = f"Known shipping evidence (preserve these exact values, estimate only the missing fields): {known_lines}\n{source_data}"
        prompt = format_prompt(
            template,
            title=title,
            category=context["category"],
            category_path=context["category_path"],
            required_attributes=context["required_attributes"],
            source_data=source_data,
        )
        try:
            text = self._ai_client().chat([{"role": "user", "content": prompt}])
            data = _extract_json_object(text)
            if not isinstance(data, dict):
                self._note_ai_failure(ai_notes, "product_dimensions", "size 输出未包含 JSON")
                return deterministic or None
            length = self._number(data.get("length_cm"))
            width = self._number(data.get("width_cm"))
            height = self._number(data.get("height_cm"))
            weight = self._number(data.get("weight_g"))
            if not all(value is not None and value > 0 for value in (length, width, height, weight)):
                self._note_ai_failure(ai_notes, "product_dimensions", "size 输出缺少有效的长/宽/高/重量")
                return deterministic or None
            # 用来源显式数值覆盖 AI 补缺结果，保证确定性证据优先
            result = {
                "length_cm": float(length),
                "width_cm": float(width),
                "height_cm": float(height),
                "weight_g": float(weight),
                "confidence": self._text(data.get("confidence")) or "medium",
                "package_profile": self._text(data.get("package_profile")),
                "reason": self._text(data.get("reason")),
                "source": "ai_estimated",
            }
            for key in ("length_cm", "width_cm", "height_cm", "weight_g"):
                if known.get(key) is not None:
                    result[key] = float(known[key])
            self._save_ai_stage_cache("size", cache_key, output_data=result, prompt=prompt, input_data=input_data)
            return result
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "product_dimensions", _ai_error_reason(exc))
            return deterministic or None

    @staticmethod
    def _extract_deterministic_size(raw: dict[str, Any]) -> dict[str, Any] | None:
        """从来源属性/变种记录/重量/包装文本中确定性提取物流尺寸与重量（0 AI）。

        只信任来源中的显式数值证据（如 ``15*10*4cm``、``180g``）。返回部分提取结果；
        由调用方判断是否完整，缺字段再走 AI 补缺。
        """
        texts: list[str] = []
        attributes = raw.get("source_attributes") or {}
        if isinstance(attributes, dict):
            texts.extend(str(value) for value in attributes.values() if value not in (None, ""))
        for variant in raw.get("source_variant_records") or []:
            if not isinstance(variant, dict):
                continue
            variant_attrs = variant.get("attributes")
            if isinstance(variant_attrs, dict):
                texts.extend(str(value) for value in variant_attrs.values() if value not in (None, ""))
        for key in ("weight_text", "package_info_text", "title", "product_name"):
            value = raw.get(key)
            if value not in (None, ""):
                texts.append(str(value))
        joined = " | ".join(texts)
        dimensions: dict[str, Any] = {}
        triple = _DIMENSION_TRIPLE.search(joined)
        if triple:
            values = [float(triple.group(index)) for index in (1, 2, 3)]
            unit = (triple.group(4) or "cm").casefold()
            scale = 0.1 if unit in {"mm", "毫米"} else 1.0
            if all(value > 0 for value in values):
                dimensions = {
                    "length_cm": values[0] * scale,
                    "width_cm": values[1] * scale,
                    "height_cm": values[2] * scale,
                }
        weight_match = _WEIGHT_PATTERN.search(joined)
        if weight_match:
            value = float(weight_match.group(1))
            unit = weight_match.group(2).casefold()
            if value > 0:
                dimensions["weight_g"] = value * 1000 if unit in {"kg", "千克", "公斤"} else value
        if not dimensions:
            return None
        dimensions["confidence"] = "high"
        dimensions["package_profile"] = ""
        dimensions["reason"] = "提取自来源属性/变种/重量文本的显式数值"
        dimensions["source"] = "deterministic_source_evidence"
        return dimensions

    @staticmethod
    def _unique_variant_values(raw: dict[str, Any]) -> list[str]:
        """收集来源变种记录中的唯一属性值（保持出现顺序）。"""
        unique: list[str] = []
        seen: set[str] = set()
        for variant in raw.get("source_variant_records") or []:
            if not isinstance(variant, dict):
                continue
            attributes = variant.get("attributes")
            if not isinstance(attributes, dict):
                continue
            for value in attributes.values():
                text = str(value or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    unique.append(text)
        return unique

    @staticmethod
    def _combined_variant_translations(
        data: dict[str, Any], variant_values: list[str]
    ) -> dict[str, str]:
        """从 combined 文本调用输出中解析变种属性值翻译（对齐 VARIANT_VALUE_TRANSLATION_PROMPT）。"""
        seen = set(variant_values)
        translations: dict[str, str] = {}
        mappings = data.get("variant_translations") if isinstance(data, dict) else None
        if not isinstance(mappings, list):
            return translations
        for item in mappings:
            if not isinstance(item, dict):
                continue
            raw_value = str(item.get("raw_value") or "").strip()
            export_value = str(item.get("export_value") or "").strip()
            if raw_value and export_value and raw_value in seen:
                translations[raw_value] = export_value
        return translations

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
        # AiClient 构造时缓存 base_url/api_key；系统配置（BasicSettings）改 key 后必须重建，
        # 否则单例一直拿旧凭据调用（文本 AI 报 401 而图片正常——图片走 config_provider 每次新解析）。
        provider = resolve_ai_provider()
        if (
            self._ai_instance is None
            or self._ai_instance.base_url != provider["base_url"]
            or self._ai_instance.api_key != provider["api_key"]
        ):
            with self._ai_lock:
                if (
                    self._ai_instance is None
                    or self._ai_instance.base_url != provider["base_url"]
                    or self._ai_instance.api_key != provider["api_key"]
                ):
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
    ) -> tuple[str, str]:
        """多模态识别主图中的可售主体 + 基于主图的初步标题（对齐原型 NativeVisualModelClient.judge）。

        返回 (subject, preliminary_title)：
        - subject：英文主体描述，用于替换生图提示词 ``Product:`` 行的标题猜测，减少主体误判；
        - preliminary_title：仅依据主图可见内容生成的英文初步标题草稿，作为文本生成
          （标题/描述）的图像证据——标题据此生成而非直译来源标题。
        任何失败返回 ("", "")，调用方回退原标题/描述流程。
        """
        if not _ai_enabled() or not image_url:
            return "", ""
        # 缓存：同一来源主图只识别一次（批量任务重复商品省 N 次多模态调用）
        with self._subject_cache_lock:
            cached = self._subject_cache.get(image_url)
        if cached:
            return cached["subject"], cached["preliminary_title"]
        data_url = self._image_to_data_url(image_url)
        if not data_url:
            return "", ""
        prompt = (
            "Analyze the actual sellable product shown in the main image. "
            "The foreground sellable subject is the product to sell; ignore houses, rooms, tables, "
            "people, props, and background scenes. Reply with strict JSON only: "
            '{"sellable_subject": "<one short English noun phrase describing the sellable product, '
            'e.g. a round acrylic keychain with letter charms>", '
            '"preliminary_title": "<one draft English listing title (80-180 letters) written ONLY from '
            'what is visible in the image: exact product type + 2-4 real visible attributes such as '
            'material, color, size, shape, quantity + intended use only if clearly shown; never invent '
            'facts that are not visible in the image>", '
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
            data = _extract_json_object(text) or {}
            subject = self._text(data.get("sellable_subject"))
            preliminary_title = self._text(data.get("preliminary_title"))
            if not subject and not preliminary_title:
                return "", ""
            ai_notes.append("subject_identity:ai")
            if preliminary_title:
                ai_notes.append("subject_identity:preliminary-title")
            result = {
                "subject": str(subject).strip()[:160],
                "preliminary_title": self._normalized_title(preliminary_title) if preliminary_title else "",
            }
            with self._subject_cache_lock:
                self._subject_cache[image_url] = result
            return result["subject"], result["preliminary_title"]
        except (AiProviderError, ValueError, OSError) as exc:
            self._note_ai_failure(ai_notes, "subject_identity", _ai_error_reason(exc))
            return "", ""

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
                "image_size": provider.get("image_size") or "2048x2048",
            }
        # COS 图床：gitignored 本地配置 cos.local.json 优先，环境变量 WH_COS_* 可覆盖。
        # 对齐原型出图保存逻辑——生成图上传 COS 转外链后写进导入表，店小秘可直接读取。
        # 已配置安装可从程序目录读取 gitignored 本地配置；公开安装包不携带密钥，
        # 新安装需由系统设置或环境变量提供 COS 凭据。
        cos_config: dict[str, Any] = {}
        for local_cos in _cos_local_config_paths():
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
                        break
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
            # resolve_ai_provider 只在后端内部携带解密密钥；公开 provider summary 会剔除。
            sys_secret_id = str(sys_cos.get("secret_id") or secret_id).strip()
            sys_secret_key = str(sys_cos.get("secret_key") or secret_key).strip()
            cos_config = {
                "bucket": sys_cos["bucket"],
                "region": sys_cos["region"],
                "secret_id": sys_secret_id,
                "secret_key": sys_secret_key,
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
            "image_provider_strategy": sys_limits.get("image_provider_strategy", "primary_first"),
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

    def _persist_media_for_preview(
        self,
        parts: list[Any],
        task_id: int,
        draft_id: int,
        workspace_id: str,
    ) -> list[str]:
        """Register original generated bytes locally; never call COS here."""
        values: list[str] = []
        for part in parts:
            asset = self.preview_images.register_generated(
                task_id=task_id,
                product_draft_id=draft_id,
                workspace_id=workspace_id,
                media=part,
            )
            values.append(str(asset.get("preview_url") or ""))
        return values

    @staticmethod
    def _iso_datetime(value: str | None) -> Any:
        """解析 ISO 时间戳（兼容无时区），失败返回 None。"""
        if not value:
            return None
        from datetime import datetime, timezone  # noqa: PLC0415

        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _elapsed_seconds(task: dict[str, Any]) -> int:
        """任务处理耗时：运行中按当前时间计算，已结束按 updated_at - created_at。"""
        from datetime import datetime, timezone  # noqa: PLC0415

        started = ProductProcessingService._iso_datetime(task.get("created_at"))
        if started is None:
            return 0
        if task.get("status") in {"completed", "failed", "partial_failure"}:
            end = ProductProcessingService._iso_datetime(task.get("updated_at")) or datetime.now(timezone.utc)
        else:
            end = datetime.now(timezone.utc)
        return max(0, int((end - started).total_seconds()))

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
        elapsed_seconds = self._elapsed_seconds(task)
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
            "elapsed_seconds": elapsed_seconds,
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
            "elapsed_seconds": elapsed_seconds,
        }
        processed_count = task["success_count"] + task["failed_count"] + task["skipped_count"]
        return {
            "task_id": task["id"],
            "total_count": task["total_count"],
            "success_count": task["success_count"],
            "failed_count": failed,
            "processed_count": processed_count,
            "elapsed_seconds": elapsed_seconds,
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
    def _normalized_description(value: str) -> str:
        """描述归一化：保留 Amazon 五点 bullet 的换行结构。

        _normalized_title 会折叠换行并截断到 200 字符，只适用于单行标题；
        五点描述若用它会把 5 条 bullet 挤成一行并砍到只剩 2 条（已修复的 bug）。
        这里逐行折叠行内空白、去掉空行，整段保留换行，上限 2000 字符。
        """
        lines = [re.sub(r"\s+", " ", line).strip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
        text = "\n".join(line for line in lines if line)
        return text[:2000]

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
