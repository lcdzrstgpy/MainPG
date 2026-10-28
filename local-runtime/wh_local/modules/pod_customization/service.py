from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
import sqlite3
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageDraw
from pydantic import ValidationError

from ...customer.contracts import CustomerBillingPermissionError
from ...runtime_logs import business_logger
from ...session import Actor
from . import spec_card
from .assets import PodAssetStore
from .billing_contract import (
    POD_BILLING_PROFILE_RANDOM,
    POD_BILLING_PROFILE_FISSION,
    POD_BILLING_PROFILE_REPLICA,
    PodBillingAuthorizationRequired,
    PodBillingCoordinator,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
    PodPlannedCall,
    TITLE_ATTEMPTS,
)
from .contracts import (
    BatchCreate,
    BatchRetryFailedCreate,
    BriefFieldRequest,
    COMPOSITION_NAME_MAX_LENGTH,
    COMPOSITION_PANEL_KEYS,
    COMPOSITION_SCOPE_FULL,
    COMPOSITION_SCOPE_FISSION,
    COMPOSITION_SCOPE_REPLICA,
    CompositionRenameRequest,
    CompositionRequest,
    CompositionUpdateRequest,
    DirectListingTrialCreate,
    FissionBatchCreate,
    FissionImageRole,
    FissionImageUploadResponse,
    GENERATION_LAYOUT_COLUMN,
    ReplicaAnalyzeRequest,
    ReplicaBatchCreate,
    ReplicaImageUploadResponse,
    SEMI_PATTERN_ROLES,
    SemiBatchCreate,
    validate_spec_card,
)
from .ai_runtime import resolve_pod_image_model
from .brief_runtime import PodBriefRequest
from .column_generation import group_roles_to_freeze
from .composition_runtime import (
    REPLICA_MAIN_PANEL_PLACEHOLDER_EN,
    REPLICA_MAIN_PANEL_PLACEHOLDER_ZH,
    PodCompositionLocalizeRequest,
    PodCompositionRequest,
)
from .fission_panel_template import FISSION_PANEL_PROMPT_VERSION
from .fission_prompts import default_fission_composition
from .fission_runtime import (
    FISSION_PLAN_PROMPT_VERSION,
    FissionAssetImage,
    FissionPlanRequest,
)
from .replica_analysis_runtime import (
    MAIN_PROMPT_MODEL,
    MAIN_PROMPT_REFERENCE_ORDER,
    MAIN_PROMPT_VERSION,
    CompositionTemplateOptions,
    ReplicaAssetImage,
)
from .replica_panel_template import DEFAULT_DERIVED_PANEL_DIRECTIONS, DERIVED_PANEL_KEYS
from .replica_specs import REPLICA_PLAN_VERSION, GenerationStrategy, ReplicaPlan
from .export import (
    DianxiaomiExport,
    PodWorkbookExport,
    analyze_dianxiaomi_export,
    build_pod_dianxiaomi_export,
    build_pod_miaoshou_export,
)
from .export_records import PodExportRecordStore
from .errors import image_provider_outcome_for_exception, safe_error_message
from .reconciliation import PodBillingReconciler, deadline_passed
from .remote_billing import attach_freeze_snapshot
from .repository import PodCustomizationRepository, PodRepositoryError, ReplicaBatchIdempotentReturn
from .spec_card_units import display_spec_card_cells
from .prompts import (
    DEFAULT_COLUMN_COMPOSITION_PANELS,
    DEFAULT_COLUMN_COMPOSITION_RAW_INPUT,
    DEFAULT_COMPOSITION_ID,
    DEFAULT_COMPOSITION_NAME,
    DEFAULT_COMPOSITION_PANELS,
    DEFAULT_COMPOSITION_RAW_INPUT,
    LISTING_IMAGE_ROLES,
    PATTERN_PROMPT_VERSION,
    assign_style_elements,
    build_direct_listing_prompt,
    build_style_listing_prompt,
)
from .runtime_contracts import (
    SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES,
    DirectListingGridRequest,
    PodAiRuntime,
)
from .structure_analysis import (
    STRUCTURE_ANALYSIS_VERSION,
    STRUCTURE_STATUS_FAILED,
    STRUCTURE_STATUS_NEEDS_CONFIRMATION,
    STRUCTURE_STATUS_READY,
    load_structure_facts,
    needs_confirmation,
)
from .template_views import load_generation_references
from .image_providers.catalog import model_supports_multi_view
from .title_runtime import PodTitleRequest, visual_signature
from .worker import (
    POD_PROGRESS_TIMEOUT_SECONDS,
    SPEC_CARD_ASSET_KIND,
    PodBatchWorker,
    PodBillingRun,
    build_spec_card_media,
)


class BatchNotTerminalForSpecCard(PodRepositoryError):
    """批次仍在生成中：配置冻结只读，重印不可用（方案 §8/§10.3），路由转 409。"""

    def __init__(self, message: str = "批次尚未完成，暂不能重新合成标注") -> None:
        super().__init__(message, 409)


_PYDANTIC_VALUE_ERROR_PREFIX = "Value error, "

# 结构事实表允许人工修订的键（与 structure_analysis 的输出契约保持一致）。
_STRUCTURE_FACT_KEYS = frozenset(
    {
        "product_type",
        "part_count",
        "tiers",
        "units_per_tier",
        "height_mm",
        "thickness_mm",
        "material",
        "transparency",
        "surface_finish",
        "symmetry",
        "connections",
        "printable_regions",
        "confidence",
    }
)
# 复刻/裂变识图入参的视觉长边上限：20MB 原图 base64 后约 27MB，后面还要被 JSON
# 序列化两次，峰值可到上百 MB。识图只需要看清版式与配色，1536 足够。
_VISION_MAX_EDGE = 1536


def _shrink_for_vision(content: bytes) -> tuple[bytes, str] | None:
    """超大图先缩成 JPEG 再 base64；不需要缩或缩图失败时返回 None（保持原图）。"""

    try:
        with Image.open(io.BytesIO(content)) as opened:
            try:
                opened.draft("RGB", (_VISION_MAX_EDGE, _VISION_MAX_EDGE))
            except Exception:
                pass
            image = opened.convert("RGB")
        if max(image.size) <= _VISION_MAX_EDGE:
            return None
        image.thumbnail((_VISION_MAX_EDGE, _VISION_MAX_EDGE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        return None


def _composition_default_name(brief: str) -> str:
    """默认模板名取原话摘要（压平空白 + 截断）；为空时给个占位名。"""

    text = " ".join(str(brief or "").split())[:COMPOSITION_NAME_MAX_LENGTH]
    return text or "未命名构图"


def _spec_card_validation_message(exc: ValidationError) -> str:
    """取 pydantic 校验错误里的中文文案（``Value error, 规格卡风格必须是…`` → ``规格卡风格必须是…``）。"""

    for error in exc.errors():
        message = str(error.get("msg") or "").strip()
        if message.startswith(_PYDANTIC_VALUE_ERROR_PREFIX):
            message = message[len(_PYDANTIC_VALUE_ERROR_PREFIX):].strip()
        if message:
            return message
    return "规格卡配置不正确"


def _replica_request_hash(request: ReplicaBatchCreate) -> str:
    """复刻创建请求的规范化哈希：同 ID 同请求判重的依据。"""
    payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fission_failure_diagnostics(exc: BaseException) -> dict[str, Any]:
    """出方案失败时落库的可诊断信息：异常原文 + 未通过的校验 + 模型原始输出。

    没有这些字段时，库里只剩 ``error_code=DoubaoArkError``，无法判断是模型缺字段、
    motifs 不足、main_prompt 太短还是多样性规则没过。``content`` 只截 20000 字符，
    避免单条记录撑大数据库。
    """
    return {
        "error": str(exc),
        "error_kind": str(getattr(exc, "error_kind", "") or type(exc).__name__),
        "validation": str(getattr(exc, "validation_error", "") or ""),
        "content": str(getattr(exc, "raw_content", "") or "")[:20000],
    }


def blank_spec_card_base_jpeg(side: int = 800) -> bytes:
    """预览兜底底图：纯白 ``side×side`` + 浅灰虚线框，示意卡片会印在这张图上。"""

    image = Image.new("RGB", (side, side), "#ffffff")
    draw = ImageDraw.Draw(image)
    inset = max(8, round(side * 0.03))
    step = max(12, round(side * 0.05))
    for offset in range(inset, side - inset, step):
        end = min(offset + step // 2, side - inset)
        draw.line((offset, inset, end, inset), fill="#d8d8d8", width=2)
        draw.line((offset, side - inset, end, side - inset), fill="#d8d8d8", width=2)
        draw.line((inset, offset, inset, end), fill="#d8d8d8", width=2)
        draw.line((side - inset, offset, side - inset, end), fill="#d8d8d8", width=2)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=92)
    return output.getvalue()


class PodCustomizationService:
    def __init__(
        self,
        database_path: Path,
        asset_root: Path,
        ai_runtime: PodAiRuntime,
        *,
        title_runtime: Any | None = None,
        brief_runtime: Any | None = None,
        composition_runtime: Any | None = None,
        analysis_runtime: Any | None = None,
        fission_analysis_runtime: Any | None = None,
        billing_coordinator: PodBillingCoordinator | None = None,
        start_workers: bool = True,
    ) -> None:
        self.database_path = Path(database_path)
        self.assets = PodAssetStore(asset_root)
        self.ai_runtime = ai_runtime
        self.title_runtime = title_runtime
        self.brief_runtime = brief_runtime
        self.composition_runtime = composition_runtime
        self.analysis_runtime = analysis_runtime
        self.fission_analysis_runtime = fission_analysis_runtime
        self.billing_coordinator = billing_coordinator
        self.repository = PodCustomizationRepository(self.database_path)
        self.export_records = PodExportRecordStore(self.database_path)
        if start_workers:
            self.repository.recover_interrupted_batches()
            self.repository.recover_billing_runs()
        self.worker = (
            PodBatchWorker(
                self.repository,
                self.assets,
                ai_runtime,
                title_runtime=title_runtime,
                coordinator_workers=getattr(ai_runtime, "batch_workers", 1),
            )
            if start_workers
            else None
        )
        self.start_workers = start_workers
        # The repository claim is the cross-process correctness boundary. This
        # short local critical section closes the pre-claim freeze window in a
        # single workbench process, so two rapid clicks cannot create separate
        # per-style billing reservations before one claim loses.
        self._regeneration_lock = threading.RLock()
        # The grant is request-local. If a provider call loses it, that call is
        # recorded as a normal failure and can be retried from the batch UI.
        self._reaper_stop: threading.Event = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        self._cache_sweep_stop: threading.Event = threading.Event()
        self._cache_sweep_thread: threading.Thread | None = None
        self._recon_stop: threading.Event = threading.Event()
        self._recon_thread: threading.Thread | None = None
        self._billing_reconciler_instance: PodBillingReconciler | None = None
        if start_workers:
            self._start_reaper()
            self._start_cache_sweeper()
            self._start_billing_reconciler()

    def upload_template(
        self,
        actor: Actor,
        *,
        name: str,
        filename: str,
        content: bytes,
        views: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """保存模板主图与可选结构视图，并尽力生成结构事实表。

        ``views`` 每项 ``{"role": "side"|"top"|"dimension", "filename": str, "content": bytes}``；
        角色不可重复、不含 ``primary``（主图就是 ``content``）。识图失败不阻塞上传：
        模板照常可用，结构状态落为 ``failed``，批次退化为单图锚定。
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("template name is required")
        # 先校验角色再落盘：非法视图不应该留下孤儿资产。
        self._validate_template_views(views)
        stored = self.assets.save_image(actor.workspace_id, actor.id, content)
        asset = self.repository.create_asset(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            kind="template",
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )
        view_rows = self._store_template_views(actor, views)
        template = self.repository.create_template(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            name=clean_name,
            asset=asset,
            views=view_rows,
        )
        self._analyze_template_structure(actor, template, view_rows)
        return self._template_payload(
            self.repository.get_template(template["template_id"], actor.workspace_id, actor.id)
        )

    def attach_template_views(
        self,
        actor: Actor,
        template_id: str,
        views: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """给已存在的个人模板补充/替换结构视图，并重跑一次结构识图。

        批次表单里的「结构视图锚定」走这里：视图最终挂在产品模板上，后续用同一
        模板的批次自动复用；系统模板是共享只读的，先要求保存成个人模板。
        """
        template = self.repository.get_template(template_id, actor.workspace_id, actor.id)
        if template["source"] != "personal":
            raise ValueError("系统模板不支持结构视图，请先保存为个人模板")
        self._validate_template_views(views)
        rows = self._store_template_views(actor, views)
        if not rows:
            return self._template_payload(template)
        all_views = self.repository.replace_template_views(
            template_id, actor.workspace_id, actor.id, views=rows
        )
        self._analyze_template_structure(actor, template, all_views)
        return self._template_payload(
            self.repository.get_template(template_id, actor.workspace_id, actor.id)
        )

    @staticmethod
    def _validate_template_views(views: Sequence[Mapping[str, Any]]) -> None:
        seen: set[str] = set()
        for raw in views:
            role = str(raw.get("role") or "").strip().lower()
            if role not in {"side", "top", "dimension"}:
                raise ValueError("结构视图角色必须是 side / top / dimension")
            if role in seen:
                raise ValueError("同一结构视图角色不能重复上传")
            seen.add(role)

    def _store_template_views(
        self, actor: Actor, views: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in views:
            role = str(raw.get("role") or "").strip().lower()
            if role not in {"side", "top", "dimension"}:
                raise ValueError("结构视图角色必须是 side / top / dimension")
            if role in seen:
                raise ValueError("同一结构视图角色不能重复上传")
            payload = bytes(raw.get("content") or b"")
            if not payload:
                continue
            seen.add(role)
            stored = self.assets.save_image(actor.workspace_id, actor.id, payload)
            asset = self.repository.create_asset(
                workspace_id=actor.workspace_id,
                owner_user_id=actor.id,
                kind="template_view",
                filename=str(raw.get("filename") or f"template-{role}.png"),
                relative_path=stored.relative_path,
                content_type=stored.content_type,
                byte_size=stored.byte_size,
                sha256=stored.sha256,
                width=stored.width,
                height=stored.height,
            )
            rows.append(
                {
                    "role": role,
                    "asset_id": asset["asset_id"],
                    "width": stored.width,
                    "height": stored.height,
                }
            )
        return rows

    def _analyze_template_structure(
        self,
        actor: Actor,
        template: Mapping[str, Any],
        view_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        """上传时把视图集转成结构事实表（模板级一次，纯 title 零计费）。

        任何失败都只把状态落为 ``failed``：模板仍然可用，批次退回单图锚定。
        """
        if not view_rows or self.analysis_runtime is None or self.billing_coordinator is None:
            return
        template_id = str(template["template_id"])
        images: list[dict[str, str]] = []
        try:
            primary = self.repository.get_asset(template["asset_id"], actor.workspace_id, actor.id)
            images.append({"role": "primary", "data_url": self._asset_data_url(primary)})
            for row in view_rows:
                asset = self.repository.get_asset(str(row["asset_id"]), actor.workspace_id, actor.id)
                images.append({"role": str(row["role"]), "data_url": self._asset_data_url(asset)})
        except Exception as exc:  # noqa: BLE001 - 读图失败不阻塞上传
            self.repository.update_template_structure(
                template_id,
                actor.workspace_id,
                actor.id,
                facts_json="",
                status=STRUCTURE_STATUS_FAILED,
                analysis_version=STRUCTURE_ANALYSIS_VERSION,
            )
            business_logger("pod_processing").warning(
                "POD 模板结构识图准备失败 | template=%s | %s", template_id, safe_error_message(exc)
            )
            return

        analysis_id = uuid.uuid4().hex
        try:
            run = self._freeze_structure_analysis(actor, analysis_id)
        except RuntimeError as exc:
            business_logger("pod_processing").warning(
                "POD 模板结构识图跳过（计费底座不可用） | template=%s | %s", template_id, exc
            )
            return

        call_ids = tuple(call.call_id for call in run.plan.calls if call.feature == "pod.title")
        facts: dict[str, Any] | None = None
        try:
            facts = self.analysis_runtime.analyze_structure_views(
                images,
                grant=run.grant,
                call_id=call_ids[0],
                call_ids=call_ids,
                on_start=lambda call_id: run.start(call_id, "pod.title"),
                on_outcome=lambda call_id, status: run.record(call_id, "pod.title", status),
            )
        except Exception as exc:  # noqa: BLE001 - 识图失败只降级，不影响模板可用
            self.repository.update_template_structure(
                template_id,
                actor.workspace_id,
                actor.id,
                facts_json="",
                status=STRUCTURE_STATUS_FAILED,
                analysis_version=STRUCTURE_ANALYSIS_VERSION,
            )
            business_logger("pod_processing").warning(
                "POD 模板结构识图失败 | template=%s | %s", template_id, safe_error_message(exc)
            )
        finally:
            try:
                run.settle()
            except Exception as exc:  # noqa: BLE001
                self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))

        if facts is None:
            return
        status = (
            STRUCTURE_STATUS_NEEDS_CONFIRMATION
            if needs_confirmation(facts)
            else STRUCTURE_STATUS_READY
        )
        self.repository.update_template_structure(
            template_id,
            actor.workspace_id,
            actor.id,
            facts_json=json.dumps(facts, ensure_ascii=False),
            status=status,
            analysis_version=STRUCTURE_ANALYSIS_VERSION,
        )

    def update_template_structure(
        self, actor: Actor, template_id: str, facts: Mapping[str, Any]
    ) -> dict[str, Any]:
        """人工确认/修订结构事实表；状态按剩余低置信度字段重算。"""
        template = self.repository.get_template(template_id, actor.workspace_id, actor.id)
        if template["source"] != "personal":
            raise ValueError("只有个人模板可以修订结构事实表")
        cleaned = {key: value for key, value in dict(facts).items() if key in _STRUCTURE_FACT_KEYS}
        confidence = cleaned.get("confidence")
        if not isinstance(confidence, Mapping):
            cleaned.pop("confidence", None)
        status = STRUCTURE_STATUS_NEEDS_CONFIRMATION if needs_confirmation(cleaned) else STRUCTURE_STATUS_READY
        updated = self.repository.update_template_structure(
            template_id,
            actor.workspace_id,
            actor.id,
            facts_json=json.dumps(cleaned, ensure_ascii=False),
            status=status,
            analysis_version=STRUCTURE_ANALYSIS_VERSION,
        )
        return self._template_payload(updated)

    def list_templates(self, actor: Actor) -> dict[str, Any]:
        return {
            "templates": [
                self._template_payload(template)
                for template in self.repository.list_templates(actor.workspace_id, actor.id)
            ]
        }

    def create_batch(self, actor: Actor, request: BatchCreate, *, enqueue: bool = True) -> dict[str, Any]:
        # 提示词版本以服务端为准：大提示词由本服务构造，客户端传值只作兼容。
        # 否则改了提示词却忘了升版本，新旧批次会被记成同一版本，产出无法归因。
        request = request.model_copy(update={"prompt_version": PATTERN_PROMPT_VERSION})
        batch_id = uuid.uuid4().hex
        self.repository.preflight_batch(actor.workspace_id, actor.id, request)
        # 全定制新建批次自动套用该账号「生效中」的构图模板（若有）；仓库层把它冻结进 prompt_snapshot。
        composition = self.repository.get_active_composition(actor.workspace_id, actor.id)
        column_layout = request.generation_layout == GENERATION_LAYOUT_COLUMN
        if column_layout:
            billing_run = (
                self._freeze_column_batch(actor, batch_id, request.count)
                if (enqueue or self.billing_coordinator)
                else None
            )
        else:
            billing_run = (
                self._freeze_batch(actor, batch_id, request.count)
                if (enqueue or self.billing_coordinator)
                else None
            )
        try:
            batch = self.repository.create_batch(
                actor.workspace_id,
                actor.id,
                request,
                batch_id=batch_id,
                composition=composition,
                # 生图模型在建批时冻结：运行中切到速创会让派生轮失去多参考图能力。
                generation_meta=(
                    {"image_model": resolve_pod_image_model()} if column_layout else None
                ),
            )
        except Exception:
            if billing_run is not None:
                billing_run.settle()
            raise
        if billing_run is not None and self.worker is not None:
            self.worker.register_billing_run(batch_id, billing_run)
        if enqueue and self.worker is not None:
            self.worker.submit(batch["batch_id"], billing_run)
        # 本地 POD 处理日志（pod_processing.log）：批次创建明细。
        try:
            business_logger("pod_processing").info(
                "========== POD 批次开始 | batch_id=%s | workspace=%s | 用户=%s | "
                "模板=%s | 款式数=%d | 类目=%s | 创意提示=%s | 批次标题=%s "
                "| 冻结计费=%s ==========",
                batch["batch_id"], actor.workspace_id, actor.id, request.template_id,
                request.count, request.business_fields.product_category,
                (request.creative_prompt or "-")[:200], (request.title or "-")[:120],
                "有" if billing_run is not None else "无")
        except Exception:  # noqa: BLE001 本地业务日志绝不影响业务
            pass
        return self._batch_payload(batch)

    @staticmethod
    def _semi_placeholder_png_bytes() -> bytes:
        """半定制占位图：纯白 PNG。

        尺寸必须满足 ``inspect_pod_image`` 的下限（宽高各 ≥16px），
        否则建批次会以「image dimensions are outside the supported range」失败。
        该图永不被读取，仅用于满足 batches 模板列的 NOT NULL + FK。
        """
        buffer = io.BytesIO()
        Image.new("RGB", (256, 256), "#ffffff").save(buffer, "PNG")
        return buffer.getvalue()

    def _ensure_semi_placeholder(self, actor: Actor) -> tuple[str, str, str]:
        stored = self.assets.save_image(actor.workspace_id, actor.id, self._semi_placeholder_png_bytes())
        asset = self.repository.create_asset(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            kind="template",
            filename="semi-placeholder.png",
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )
        return self.repository.ensure_semi_placeholder(actor.workspace_id, actor.id, asset)

    def create_semi_batch(self, actor: Actor, request: SemiBatchCreate, *, enqueue: bool = True) -> dict[str, Any]:
        # 同全定制：提示词版本以服务端为准，客户端传值只作兼容。
        request = request.model_copy(update={"prompt_version": PATTERN_PROMPT_VERSION})
        batch_id = uuid.uuid4().hex
        self._ensure_semi_placeholder(actor)
        billing_run = self._freeze_semi_batch(actor, batch_id, request.count) if (enqueue or self.billing_coordinator) else None
        try:
            batch = self.repository.create_semi_batch(actor.workspace_id, actor.id, request, batch_id=batch_id)
        except Exception:
            if billing_run is not None:
                billing_run.settle()
            raise
        if billing_run is not None and self.worker is not None:
            self.worker.register_billing_run(batch_id, billing_run)
        if enqueue and self.worker is not None:
            self.worker.submit(batch["batch_id"], billing_run)
        try:
            business_logger("pod_processing").info(
                "========== POD 半定制批次开始 | batch_id=%s | workspace=%s | 用户=%s | "
                "款数=%d | 组数=%d | 主题风格=%s | 创意提示=%s | 批次标题=%s | 冻结计费=%s ==========",
                batch["batch_id"], actor.workspace_id, actor.id, request.count, request.count // 4,
                request.business_fields.design_theme or "-", (request.creative_prompt or "-")[:200],
                (request.title or "-")[:120], "有" if billing_run is not None else "无")
        except Exception:  # noqa: BLE001
            pass
        return self._batch_payload(batch)

    def upload_replica_image(
        self,
        actor: Actor,
        *,
        role: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        """复刻独立图片上传：一次一图，role=source|target；返回资产、角色、宽高与授权预览路径。"""
        if role not in ("source", "target"):
            raise ValueError("复刻图片角色必须是 source 或 target")
        stored = self.assets.save_image(actor.workspace_id, actor.id, content)
        asset = self.repository.create_asset(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            kind=f"replica_{role}",
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )
        return ReplicaImageUploadResponse(
            asset_id=asset["asset_id"],
            role=role,
            width=stored.width,
            height=stored.height,
            preview_url=f"/api/pod-customization/assets/{asset['asset_id']}",
        ).model_dump()

    def upload_fission_image(
        self,
        actor: Actor,
        *,
        role: FissionImageRole,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        if role not in ("source_artwork", "target_product"):
            raise ValueError("裂变图片角色必须是 source_artwork 或 target_product")
        stored = self.assets.save_image(actor.workspace_id, actor.id, content)
        asset = self.repository.create_asset(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            kind=f"fission_{role}",
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )
        return FissionImageUploadResponse(
            asset_id=asset["asset_id"],
            role=role,
            width=stored.width,
            height=stored.height,
            preview_url=f"/api/pod-customization/assets/{asset['asset_id']}",
        ).model_dump()

    def _generate_fission_plans(
        self, actor: Actor, request: FissionBatchCreate, source: dict[str, Any], target: dict[str, Any]
    ) -> tuple[str, Any]:
        """一次双图调用产出 N 套方案，并落库分析记录（零计费）。"""
        if self.fission_analysis_runtime is None:
            raise RuntimeError("POD 爆款裂变出方案服务未启用")
        analysis_id = uuid.uuid4().hex
        billing_run = self._freeze_fission_analysis(actor, analysis_id)
        try:
            call_ids = tuple(call.call_id for call in billing_run.plan.calls)
            try:
                result = self.fission_analysis_runtime.generate_plans(
                    FissionPlanRequest(
                        request.source_asset_id,
                        request.target_asset_id,
                        int(request.requested_count),
                    ),
                    FissionAssetImage(request.source_asset_id, self._asset_data_url(source)),
                    FissionAssetImage(request.target_asset_id, self._asset_data_url(target)),
                    grant=billing_run.grant,
                    call_ids=call_ids,
                    on_start=lambda call_id: billing_run.start(call_id, "pod.title"),
                    on_outcome=lambda call_id, status: billing_run.record(call_id, "pod.title", status),
                )
            except Exception as exc:
                self.repository.create_fission_analysis(
                    analysis_id=analysis_id,
                    workspace_id=actor.workspace_id,
                    owner_user_id=actor.id,
                    source_asset_id=request.source_asset_id,
                    target_asset_id=request.target_asset_id,
                    status="failed",
                    raw_response=_fission_failure_diagnostics(exc),
                    plan_prompt_version=FISSION_PLAN_PROMPT_VERSION,
                    error_code=type(exc).__name__,
                )
                raise
            self.repository.create_fission_analysis(
                analysis_id=analysis_id,
                workspace_id=actor.workspace_id,
                owner_user_id=actor.id,
                source_asset_id=request.source_asset_id,
                target_asset_id=request.target_asset_id,
                status="succeeded",
                target_spec={"product_name": result.product_name},
                user_summary={
                    "source_summary": result.source_summary,
                    "target_summary": result.target_summary,
                },
                raw_response=result.raw_response,
                plans=[plan.to_payload() for plan in result.plans],
                plan_prompt_version=FISSION_PLAN_PROMPT_VERSION,
                model_version=result.model_version,
                schema_version=result.schema_version,
            )
            return analysis_id, result
        finally:
            billing_run.settle()


    def _fission_composition(self, actor: Actor) -> dict[str, Any]:
        record = self.repository.get_active_composition(actor.workspace_id, actor.id, scope=COMPOSITION_SCOPE_FISSION)
        if record is None:
            result = default_fission_composition()
            for panel, key in zip(result['panels'], COMPOSITION_PANEL_KEYS, strict=True):
                panel['shot'] = str(DEFAULT_COMPOSITION_PANELS[key]['en'])
            return result
        result = default_fission_composition()
        result['template_id'] = record['composition_id']
        result['template_version'] = str(record.get('version') or record.get('updated_at') or '1')
        for panel, key in zip(result['panels'], COMPOSITION_PANEL_KEYS, strict=True):
            panel['shot'] = str((record.get('panels', {}).get(key) or {}).get('en') or '')
            if not panel['shot']:
                raise PodRepositoryError('生效构图模板不完整，请重新保存模板', 409)
        return result

    def create_fission_batch(self, actor: Actor, request: FissionBatchCreate, *, enqueue: bool = True) -> dict[str, Any]:
        """一键开始裂变：出方案 → 冻结 → 落库 → 入队。

        出方案是零计费的独立动作：失败时不建批次、不冻结生图计费。
        幂等只对「建批次成功」生效；失败后用同一 client_request_id 重试会重新出方案。
        """
        request_hash = hashlib.sha256(json.dumps(request.model_dump(mode='json'), sort_keys=True,
                                                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        with self._regeneration_lock:
            existing = self.repository.find_fission_batch(actor.workspace_id, actor.id, request.client_request_id)
            if existing is not None:
                if existing['request_hash'] != request_hash:
                    raise PodRepositoryError('client_request_id 已用于不同的裂变请求', 409)
                return self.get_fission_batch(actor, existing['batch_id'])
            source = self.repository.get_asset(request.source_asset_id, actor.workspace_id, actor.id)
            target = self.repository.get_asset(request.target_asset_id, actor.workspace_id, actor.id)
            if source.get('kind') != 'fission_source_artwork':
                raise PodRepositoryError('样图资产角色错误，必须通过 role=source_artwork 上传', 409)
            if target.get('kind') != 'fission_target_product':
                raise PodRepositoryError('白底产品图资产角色错误，必须通过 role=target_product 上传', 409)
            analysis_id, result = self._generate_fission_plans(actor, request, source, target)
            plans = [plan.to_payload() for plan in result.plans]
            composition = self._fission_composition(actor)
            batch_id = uuid.uuid4().hex
            billing_run = None
            try:
                if enqueue or self.billing_coordinator is not None:
                    billing_run = self._freeze_fission_batch(actor, batch_id, request.requested_count)
                batch = self.repository.create_fission_batch(
                    workspace_id=actor.workspace_id, owner_user_id=actor.id, analysis_id=analysis_id,
                    client_request_id=request.client_request_id, request_hash=request_hash,
                    requested_count=request.requested_count,
                    listing_fields=request.listing_fields.model_dump(mode='json'),
                    plans=plans, composition=composition,
                    prompt_version=FISSION_PLAN_PROMPT_VERSION,
                    panel_prompt_version=FISSION_PANEL_PROMPT_VERSION,
                    title=request.title, batch_id=batch_id,
                )
            except Exception:
                if billing_run is not None:
                    billing_run.settle()
                raise
            if billing_run is not None and self.worker is not None:
                self.worker.register_billing_run(batch_id, billing_run)
            if enqueue and self.worker is not None:
                self.worker.submit(batch_id, billing_run)
            return self.get_fission_batch(actor, batch_id)

    def get_fission_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch.get('mode') != 'fission':
            raise PodRepositoryError('POD fission batch not found', 404)
        return self._batch_payload(batch)

    def list_fission_batches(self, actor: Actor, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        rows, total = self.repository.list_batches(actor.workspace_id, actor.id,
                                                  limit=max(1, min(limit, 100)), offset=max(0, offset), mode='fission')
        return {'batches': [self._batch_summary(row) for row in rows], 'total': total}

    def retry_fission_style(self, actor: Actor, batch_id: str, style_index: int, *, enqueue: bool = True) -> dict[str, Any]:
        batch = self.get_fission_batch(actor, batch_id)
        self._require_fission_panel_batch(batch)
        return self.regenerate_style(actor, batch_id, style_index, enqueue=enqueue)

    def _replica_assets(
        self, actor: Actor, request: ReplicaBatchCreate
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """校验样图与目标资产均属于当前 actor/workspace 且角色与上传标记一致（冻结前，不扣费）。"""
        source = self.repository.get_asset(request.source_asset_id, actor.workspace_id, actor.id)
        if source.get("kind") != "replica_source":
            raise ValueError("样图资产角色错误，必须通过 role=source 上传")
        targets: list[dict[str, Any]] = []
        for target in request.targets:
            asset = self.repository.get_asset(target.target_asset_id, actor.workspace_id, actor.id)
            if asset.get("kind") != "replica_target":
                raise ValueError("目标产品资产角色错误，必须通过 role=target 上传")
            targets.append(asset)
        return source, targets

    def create_replica_batch(
        self, actor: Actor, request: ReplicaBatchCreate, *, enqueue: bool = True
    ) -> dict[str, Any]:
        """创建复刻批次：先完成全部本地校验与幂等判重，再冻结计费并在一个事务里落库。

        每个目标款都必须带 ``analysis_id``（= 图案迁移方案 id）；方案里已有主图提示词，
        因此建批次阶段不再调用豆包。派生三格的视角在建批次时从复刻构图模板冻结一次。
        """
        self._replica_assets(actor, request)
        request_hash = _replica_request_hash(request)
        existing = self.repository.find_replica_batch(
            actor.workspace_id, actor.id, request.client_request_id
        )
        if existing is not None:
            if existing["request_hash"] == request_hash:
                return self.get_replica_batch(actor, existing["batch_id"])
            raise PodRepositoryError("client_request_id 已用于不同的复刻请求", 409)
        batch_id = uuid.uuid4().hex
        # 复刻固定四张单图：主图 + 三张派生，计费形状 4 面板 × 2 次尝试 + 标题。
        strategy: GenerationStrategy = self._replica_batch_strategy(request)
        plans = self._replica_plan_rows(actor, request)
        billing_run: PodBillingRun | None = None
        try:
            if enqueue or self.billing_coordinator:
                billing_run = self._freeze_replica_batch(
                    actor, batch_id, len(request.targets), strategy
                )
            batch = self.repository.create_replica_batch(
                actor.workspace_id,
                actor.id,
                request,
                request_hash=request_hash,
                batch_id=batch_id,
                prompt_snapshot=str(plans.get(1, {}).get("prompt_snapshot") or "{}"),
                plans=plans,
            )
        except ReplicaBatchIdempotentReturn as duplicate:
            # 并发下同请求已由另一提交者落库：结算本次冻结并按既有批次返回。
            if billing_run is not None:
                billing_run.settle()
            return self.get_replica_batch(actor, duplicate.batch_id)
        except Exception:
            if billing_run is not None:
                billing_run.settle()
            raise
        if billing_run is not None and self.worker is not None:
            self.worker.register_billing_run(batch_id, billing_run)
        if enqueue and self.worker is not None:
            self.worker.submit(batch_id, billing_run)
        try:
            business_logger("pod_processing").info(
                "========== POD 复刻批次开始 | batch_id=%s | workspace=%s | 用户=%s | "
                "款数=%d | 样图=%s | 批次标题=%s | 冻结计费=%s ==========",
                batch_id, actor.workspace_id, actor.id, len(request.targets),
                request.source_asset_id, (request.title or "-")[:120],
                "有" if billing_run is not None else "无")
        except Exception:  # noqa: BLE001
            pass
        return self.get_replica_batch(actor, batch["batch_id"])

    def list_replica_batches(self, actor: Actor, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        rows, total = self.repository.list_batches(
            actor.workspace_id,
            actor.id,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
            mode="replica",
        )
        return {"batches": [self._batch_summary(row) for row in rows], "total": total}

    def get_replica_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch.get("mode") != "replica":
            raise PodRepositoryError("POD replica batch not found", 404)
        return self._batch_payload(batch)

    def analyze_replica_assets(self, actor: Actor, request: ReplicaAnalyzeRequest) -> dict[str, Any]:
        """把一组 A/B 丢给豆包，产出该款第一张主图的提示词并落方案表。

        动作免费（纯 pod.title scope），但仍走完整的冻结 → 短期密钥 → 调用 → 结算底座，
        保留幂等键与审计。同一 (A, B, 提示词版本, 模型, 参考图顺序) 命中已有方案时直接复用，
        不再调豆包；失败不写方案表，重新分析会重新调用。
        """
        if self.analysis_runtime is None:
            raise RuntimeError("POD 复刻方案服务未启用")
        source = self.repository.get_asset(request.source_asset_id, actor.workspace_id, actor.id)
        if source.get("kind") != "replica_source":
            raise ValueError("样图资产角色错误，必须通过 role=source 上传")
        target = self.repository.get_asset(request.target_asset_id, actor.workspace_id, actor.id)
        if target.get("kind") != "replica_target":
            raise ValueError("目标产品资产角色错误，必须通过 role=target 上传")

        cached = self.repository.find_replica_transfer_plan(
            actor.workspace_id,
            actor.id,
            request.source_asset_id,
            request.target_asset_id,
            MAIN_PROMPT_VERSION,
            MAIN_PROMPT_MODEL,
            MAIN_PROMPT_REFERENCE_ORDER,
        )
        if cached is not None:
            try:
                business_logger("pod_processing").info(
                    "复刻方案命中缓存 | plan_id=%s | 提示词版本=%s | A=%s | B=%s",
                    cached["plan_id"], cached["prompt_version"],
                    request.source_asset_id, request.target_asset_id)
            except Exception:  # noqa: BLE001
                pass
            return self._transfer_plan_payload(cached, cached=True)

        plan_id = uuid.uuid4().hex
        billing_run = self._freeze_replica_analysis(actor, plan_id)
        try:
            return self._run_replica_analysis_authorized(actor, request, plan_id, billing_run)
        except PodBillingAuthorizationRequired as exc:
            raise RuntimeError(str(exc)) from exc
        finally:
            billing_run.settle()

    def _run_replica_analysis_authorized(
        self,
        actor: Actor,
        request: ReplicaAnalyzeRequest,
        plan_id: str,
        billing_run: PodBillingRun,
    ) -> dict[str, Any]:
        call_ids = tuple(
            call.call_id for call in billing_run.plan.calls if call.feature == "pod.title"
        )
        source = self.repository.get_asset(request.source_asset_id, actor.workspace_id, actor.id)
        target = self.repository.get_asset(request.target_asset_id, actor.workspace_id, actor.id)
        result = self.analysis_runtime.generate_main_prompt(
            ReplicaAssetImage(
                asset_id=source["asset_id"], data_url=self._asset_data_url(source)
            ),
            ReplicaAssetImage(
                asset_id=target["asset_id"], data_url=self._asset_data_url(target)
            ),
            grant=billing_run.grant,
            call_id=call_ids[0],
            call_ids=call_ids,
            on_start=lambda call_id: billing_run.start(call_id, "pod.title"),
            on_outcome=lambda call_id, status: billing_run.record(call_id, "pod.title", status),
        )
        # 失败不写方案表：只有成功的主图提示词才进入缓存，避免半成品被后续批次复用。
        self.repository.create_replica_transfer_plan(
            plan_id=plan_id,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            source_asset_id=request.source_asset_id,
            target_asset_id=request.target_asset_id,
            prompt_version=result.prompt_version,
            model=result.model,
            reference_order=MAIN_PROMPT_REFERENCE_ORDER,
            main_prompt=result.main_prompt,
        )
        # 并发提交同 A/B 时唯一键会保留最早那行的 plan_id：按唯一键读回权威记录，
        # 不能用本次新生成的 id 去查（否则会误判成不存在）。
        stored = self.repository.find_replica_transfer_plan(
            actor.workspace_id,
            actor.id,
            request.source_asset_id,
            request.target_asset_id,
            result.prompt_version,
            result.model,
            MAIN_PROMPT_REFERENCE_ORDER,
        )
        if stored is None:  # pragma: no cover - 刚写入即读不到属于存储异常
            raise RuntimeError("复刻方案写入后无法读回")
        try:
            business_logger("pod_processing").info(
                "复刻方案生成 | plan_id=%s | 尝试次数=%d | 提示词长度=%d | 模型=%s",
                plan_id, result.attempt_count, len(result.main_prompt), result.model)
        except Exception:  # noqa: BLE001
            pass
        return self._transfer_plan_payload(stored, cached=False)

    def _asset_data_url(self, asset: dict[str, Any]) -> str:
        """资产字节 → data URL（与豆包视觉入参一致的形状）。

        大图先缩到长边 1536 再编码：直接对 20MB 原图 base64 会额外产生约 27MB 字符串，
        叠加后续 JSON 序列化后峰值上百 MB，是复刻/裂变识图的内存热点。
        """
        content = self.assets.read(str(asset["relative_path"]))
        content_type = str(asset.get("content_type") or "image/png")
        shrunk = _shrink_for_vision(content)
        if shrunk is not None:
            content, content_type = shrunk
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    @staticmethod
    def _transfer_plan_payload(record: dict[str, Any], *, cached: bool) -> dict[str, Any]:
        """方案响应：只回方案 id 与版本，不展示任何事实、能力或提示词正文。"""
        return {
            "analysis_id": record["plan_id"],
            "status": "succeeded",
            "source_asset_id": record["source_asset_id"],
            "target_asset_id": record["target_asset_id"],
            "prompt_version": record["prompt_version"],
            "model": record["model"],
            "cached": cached,
        }

    def resolve_replica_composition_template(
        self, actor: Actor, composition_id: str
    ) -> CompositionTemplateOptions:
        """选定复刻批次使用的构图模板，只取派生三格（panel_2/3/4）。

        复用全定制构图模板（``pod_customization_compositions``）：模板只负责派生格的机位，
        主图视角由豆包方案决定、不参与模板。显式指定时必须是本人**生效中**的复刻模板；
        未指定时取当前生效模板，没有则回退系统内置的三格派生拍法。
        """
        requested = str(composition_id or "").strip()
        record: dict[str, Any] | None
        if requested and requested != DEFAULT_COMPOSITION_ID:
            record = self.repository.get_composition(actor.workspace_id, actor.id, requested)
            if record is None:
                raise PodRepositoryError("构图模板不存在", 404)
            if str(record.get("scope") or COMPOSITION_SCOPE_FULL) != COMPOSITION_SCOPE_REPLICA:
                raise PodRepositoryError("该构图模板属于全定制，不能用于爆款复刻", 409)
            if not record.get("is_active"):
                raise PodRepositoryError("构图模板未生效，无法用于复刻批次", 409)
        else:
            # 复刻只认复刻池的生效模板；没有则回退系统内置机位（不借全定制的模板）。
            record = self.repository.get_active_composition(
                actor.workspace_id, actor.id, scope=COMPOSITION_SCOPE_REPLICA
            )
        slots = tuple(DERIVED_PANEL_KEYS)
        if record is None:
            return CompositionTemplateOptions(
                template_id=DEFAULT_COMPOSITION_ID,
                template_version="1",
                panel_slots=slots,
                required_slots=slots,
                panel_instructions={
                    key: str(DEFAULT_COMPOSITION_PANELS[key]["en"]) or DEFAULT_DERIVED_PANEL_DIRECTIONS[key]
                    for key in slots
                },
            )
        panels = record.get("panels") or {}
        instructions = {
            key: str((panels.get(key) or {}).get("en") or "").strip() for key in slots
        }
        missing = [key for key, value in instructions.items() if not value]
        if missing:
            raise PodRepositoryError(
                f"复刻构图模板缺少派生视角：{', '.join(missing)}", 409
            )
        return CompositionTemplateOptions(
            template_id=str(record["composition_id"]),
            template_version=str(record.get("prompt_version") or "1"),
            panel_slots=slots,
            required_slots=slots,
            panel_instructions=instructions,
        )

    @staticmethod
    def _replica_batch_strategy(request: ReplicaBatchCreate) -> GenerationStrategy:
        """复刻批次固定独立面板：每款 4 次单面板调用（主图 + 三张派生）。

        不再有 grid（一次 2×2 母图 + 本地拆四格）：那条路径会把单格像素摊薄，
        且四格共享机位、互相干扰。老客户端若仍下发 ``grid`` 也一律按独立面板执行。
        """
        return "independent_panels"

    def _replica_transfer_plan_record(self, actor: Actor, plan_id: str) -> dict[str, Any]:
        """读取方案；不存在或不属于当前用户一律按「方案失效」409，避免提交半截批次。"""
        try:
            return self.repository.get_replica_transfer_plan(
                plan_id, actor.workspace_id, actor.id
            )
        except PodRepositoryError as exc:
            raise PodRepositoryError("复刻方案不存在或已失效，请重新分析", 409) from exc

    def _replica_plan_rows(
        self, actor: Actor, request: ReplicaBatchCreate
    ) -> dict[int, dict[str, Any]]:
        """为每款冻结 v4 计划：方案表里的主图提示词 + 复刻模板派生的三格视角。

        每个目标款都必须带 ``analysis_id``（= 方案表 ``plan_id``），且方案里的 A/B 必须
        与本次提交一致；方案由 ``analyze_replica_assets`` 预先产出，这里不再调用豆包。
        """
        template = self.resolve_replica_composition_template(actor, request.composition_id)
        derived = {
            key: str((template.panel_instructions or {}).get(key) or "").strip()
            or DEFAULT_DERIVED_PANEL_DIRECTIONS[key]
            for key in DERIVED_PANEL_KEYS
        }
        plans: dict[int, dict[str, Any]] = {}
        for style_index, target in enumerate(request.targets, start=1):
            analysis_id = str(target.analysis_id or "").strip()
            if not analysis_id:
                raise PodRepositoryError("复刻款式缺少图案迁移方案，请先完成分析", 409)
            record = self._replica_transfer_plan_record(actor, analysis_id)
            if record["source_asset_id"] != request.source_asset_id:
                raise PodRepositoryError("复刻方案与原素材样图不匹配", 409)
            if record["target_asset_id"] != target.target_asset_id:
                raise PodRepositoryError("复刻方案与目标产品资产不匹配", 409)
            plan = ReplicaPlan(
                main_prompt=str(record["main_prompt"]),
                prompt_version=str(record["prompt_version"]),
                image_model_version=resolve_pod_image_model(),
                reference_order=str(record["reference_order"]),
                composition_template_id=template.template_id,
                composition_template_version=template.template_version,
                derived_panels=dict(derived),
                generation_strategy="independent_panels",
            )
            plans[style_index] = self._replica_plan_row(analysis_id, plan)
        return plans

    @staticmethod
    def _replica_plan_row(analysis_id: str, plan: ReplicaPlan) -> dict[str, Any]:
        """把冻结计划展开成款表一行所需的快照字段。"""
        return {
            "analysis_id": analysis_id,
            "replica_plan_json": plan.model_dump_json(),
            "prompt_snapshot": plan.main_prompt,
            "template_version": plan.composition_template_version,
            "compiler_version": plan.prompt_version,
            "generation_strategy": plan.generation_strategy,
            "plan_fingerprint": "",
        }


    def list_semi_batches(self, actor: Actor, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        rows, total = self.repository.list_batches(
            actor.workspace_id,
            actor.id,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
            mode="semi",
        )
        return {"batches": [self._batch_summary(row) for row in rows], "total": total}

    def get_semi_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch.get("mode") != "semi":
            raise PodRepositoryError("POD semi batch not found", 404)
        return self._batch_payload(batch)

    def download_semi_zip(self, actor: Actor, batch_id: str) -> tuple[bytes, str, int]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch.get("mode") != "semi":
            raise PodRepositoryError("POD semi batch not found", 404)
        completed_items = [
            item for item in batch["items"]
            if item.get("status") == "completed" and item.get("pattern_asset_id")
        ]
        if not completed_items:
            raise PodRepositoryError("当前批次没有可下载的图案", 409)
        buffer = io.BytesIO()
        # 图案本身已是 PNG/JPEG 压缩格式，再 DEFLATE 基本不减体积、纯耗 CPU；
        # 直接 STORED 打包（ZIP 仍是合法压缩包，解压行为一致）。
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            for item in completed_items:
                index = int(item.get("item_index") or item.get("index") or 0)
                asset = self.repository.get_asset(
                    item["pattern_asset_id"], batch["workspace_id"], batch["owner_user_id"]
                )
                suffix = Path(asset["filename"]).suffix or ".png"
                archive.writestr(
                    f"style_{index:03d}{suffix}",
                    self.assets.read(asset["relative_path"]),
                )
        item_count = len(completed_items)
        filename = f"POD-SEMI-{batch_id[:8]}-{item_count}款.zip"
        self.export_records.record_success(
            batch_id=batch_id,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            file_name=filename,
            format="semi_zip",
            exported_count=item_count,
            skipped_count=0,
        )
        return buffer.getvalue(), filename, item_count

    def run_direct_listing_trial(
        self, actor: Actor, request: DirectListingTrialCreate
    ) -> dict[str, Any]:
        """Run exactly one reference-locked listing grid, outside batch workers."""
        template = self.repository.get_template(request.template_id, actor.workspace_id, actor.id)
        if template["source"] != "personal":
            raise ValueError("direct POD listing trial requires a personal template")
        template_asset = self.repository.get_asset(template["asset_id"], actor.workspace_id, actor.id)
        template_image = self.assets.read(template_asset["relative_path"])
        template_content_type = str(template_asset["content_type"] or "").strip().lower()
        if template_content_type not in SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES:
            raise ValueError("direct POD listing template must be a JPEG, PNG, or WEBP image")
        trial_id = uuid.uuid4().hex
        billing_run = self._freeze_trial(actor, trial_id, request)
        # 与批次链路一致：结构事实表与视图角色冻结进基础提示词，多图只在豆包下发。
        views = self.repository.list_template_views(request.template_id, actor.workspace_id, actor.id)
        extra_roles = tuple(
            str(row["role"]) for row in views if str(row["role"]) in {"side", "top"}
        )
        base_prompt = build_direct_listing_prompt(
            request.business_fields,
            request.creative_prompt,
            structure_facts=load_structure_facts(template.get("structure_facts_json")),
            reference_view_roles=("primary", *extra_roles),
        )
        references = (
            load_generation_references(
                repository=self.repository,
                assets=self.assets,
                template_id=request.template_id,
                workspace_id=actor.workspace_id,
                owner_user_id=actor.id,
                primary_content=template_image,
                primary_content_type=template_content_type,
            )
            if model_supports_multi_view(resolve_pod_image_model())
            else ()
        )
        trial_elements = assign_style_elements(
            request.business_fields.style_keywords, 1, trial_id
        )
        grid_asset_ids: list[str] = []
        generated_grids: list[Any] = []
        split_error = ""

        try:
            return self._run_direct_listing_trial_authorized(
                actor,
                request,
                template_image=template_image,
                template_content_type=template_content_type,
                reference_images=references,
                trial_id=trial_id,
                base_prompt=base_prompt,
                trial_elements=trial_elements,
                billing_run=billing_run,
            )
        except PodBillingAuthorizationRequired as exc:
            raise RuntimeError(str(exc)) from exc
        finally:
            billing_run.settle()

    def _run_direct_listing_trial_authorized(
        self,
        actor: Actor,
        request: DirectListingTrialCreate,
        *,
        template_image: bytes,
        template_content_type: str,
        reference_images: tuple[Any, ...] = (),
        trial_id: str,
        base_prompt: str,
        trial_elements: dict[str, object],
        billing_run: PodBillingRun,
    ) -> dict[str, Any]:
        grid_asset_ids: list[str] = []
        generated_grids: list[Any] = []
        split_error = ""
        for attempt in (1, 2):
            attempt_prompt = build_style_listing_prompt(
                base_prompt,
                style_index=1,
                attempt=attempt,
                business_fields=request.business_fields.model_dump(),
                creative_prompt=request.creative_prompt,
                style_elements=trial_elements,
            )
            provider_call_id = f"{trial_id}:image:{attempt}"
            request_kwargs: dict[str, Any] = (
                {"reference_images": tuple(reference_images)}
                if len(reference_images) > 1
                else {
                    "template_image": template_image,
                    "template_content_type": template_content_type,
                }
            )
            grid_request = DirectListingGridRequest(
                trial_id=trial_id,
                template_id=request.template_id,
                prompt=attempt_prompt,
                attempt=attempt,
                **request_kwargs,
            )
            try:
                billing_run.start(provider_call_id, "pod.image")
                grid = self.ai_runtime.generate_listing_grid(
                    grid_request,
                    grant=billing_run.grant,
                    call_id=provider_call_id,
                )
                billing_run.record(provider_call_id, "pod.image", "success")
            except PodBillingAuthorizationRequired:
                if billing_run.call_status(provider_call_id) == "started":
                    billing_run.record(provider_call_id, "pod.image", "no_return")
                raise
            except Exception as exc:
                billing_run.record(
                    provider_call_id,
                    "pod.image",
                    image_provider_outcome_for_exception(exc),
                )
                self._raise_direct_trial_generation_error(exc)
            generated_grids.append(grid)
            try:
                panels = self.ai_runtime.split_listing_grid(grid)
                if len(panels) != 4:
                    raise RuntimeError("generated four-grid image did not yield exactly four panels")
            except Exception as exc:
                split_error = safe_error_message(exc)
                if attempt == 1:
                    continue
                grid_asset_ids = self._save_direct_trial_grid_attempts(actor, trial_id, generated_grids)
                failed = self.repository.create_direct_listing_trial(
                    trial_id=trial_id,
                    workspace_id=actor.workspace_id,
                    owner_user_id=actor.id,
                    template_id=request.template_id,
                    status="failed",
                    prompt_snapshot=attempt_prompt,
                    grid_attempt_asset_ids=grid_asset_ids,
                    panel_asset_ids={},
                    public_urls={},
                    error_message=split_error,
                )
                return self._direct_listing_trial_payload(failed)

            grid_asset_ids = self._save_direct_trial_grid_attempts(actor, trial_id, generated_grids)
            roles = LISTING_IMAGE_ROLES
            panel_assets = {
                role: self._save_direct_trial_asset(
                    actor,
                    "direct_listing_panel",
                    f"direct-listing-{trial_id}-{role}{panel.suffix}",
                    panel.content,
                )
                for role, panel in zip(roles, panels, strict=True)
            }
            public_urls: dict[str, str] = {}
            title_result: dict[str, Any] | None = None
            try:
                for role, panel in zip(roles, panels, strict=True):
                    public_urls[role] = self.ai_runtime.publish_listing_image(
                        panel, namespace=actor.workspace_id, role=role
                    )
                    if role == "lifestyle":
                        title_result = self._generate_direct_trial_title(
                            trial_id,
                            panel,
                            request.business_fields,
                            request.creative_prompt,
                            billing_run,
                        )
            except PodBillingAuthorizationRequired:
                raise
            except Exception as exc:
                failed = self.repository.create_direct_listing_trial(
                    trial_id=trial_id,
                    workspace_id=actor.workspace_id,
                    owner_user_id=actor.id,
                    template_id=request.template_id,
                    status="failed",
                    prompt_snapshot=attempt_prompt,
                    grid_attempt_asset_ids=grid_asset_ids,
                    panel_asset_ids={role: asset["asset_id"] for role, asset in panel_assets.items()},
                    public_urls=public_urls,
                    error_message=f"POD 图床发布失败：{safe_error_message(exc)}",
                    title_result=title_result,
                )
                return self._direct_listing_trial_payload(failed)
            stored = self.repository.create_direct_listing_trial(
                trial_id=trial_id,
                workspace_id=actor.workspace_id,
                owner_user_id=actor.id,
                template_id=request.template_id,
                status="completed",
                prompt_snapshot=attempt_prompt,
                grid_attempt_asset_ids=grid_asset_ids,
                panel_asset_ids={role: asset["asset_id"] for role, asset in panel_assets.items()},
                public_urls=public_urls,
                title_result=title_result,
            )
            return self._direct_listing_trial_payload(stored)

        raise RuntimeError(f"direct listing trial did not produce a valid grid: {split_error}")

    def get_direct_listing_trial(self, actor: Actor, trial_id: str) -> dict[str, Any]:
        return self._direct_listing_trial_payload(
            self.repository.get_direct_listing_trial(trial_id, actor.workspace_id, actor.id)
        )

    def list_direct_listing_trials(self, actor: Actor) -> dict[str, Any]:
        rows, total = self.repository.list_direct_listing_trials(actor.workspace_id, actor.id)
        return {"trials": [self._direct_listing_trial_payload(row) for row in rows], "total": total}

    def generate_brief_fields(self, actor: Actor, request: BriefFieldRequest) -> dict[str, Any]:
        """智能前置层：一句模糊输入 → 结构化业务字段。

        动作免费（服务端对 POD 画像的纯 title scope 显式零计费），但仍走完整的
        冻结 → 发放短期密钥 → 调用 → 结算流程，保留幂等键与审计。
        """
        if self.brief_runtime is None:
            raise RuntimeError("POD 智能填写服务未启用")
        brief_id = uuid.uuid4().hex
        template_image = b""
        template_content_type = ""
        if request.template_asset_id:
            # 模板图只用于读材质/形态并顺手产出批级美术指导；读不到就跳过，不阻塞智能填写。
            try:
                asset = self.repository.get_asset(
                    request.template_asset_id, actor.workspace_id, actor.id
                )
                template_image = self.assets.read(asset["relative_path"])
                template_content_type = str(asset.get("content_type") or "")
            except Exception:  # noqa: BLE001 - 缺图/无权限一律降级为纯文本智能填写
                template_image = b""
                template_content_type = ""
        billing_run = self._freeze_brief(actor, brief_id)
        try:
            return self._run_brief_fields_authorized(
                request,
                brief_id,
                billing_run,
                template_image=template_image,
                template_content_type=template_content_type,
            )
        except PodBillingAuthorizationRequired as exc:
            raise RuntimeError(str(exc)) from exc
        finally:
            billing_run.settle()

    def _run_brief_fields_authorized(
        self,
        request: BriefFieldRequest,
        brief_id: str,
        billing_run: PodBillingRun,
        *,
        template_image: bytes = b"",
        template_content_type: str = "",
    ) -> dict[str, Any]:
        call_ids = tuple(
            call.call_id for call in billing_run.plan.calls if call.feature == "pod.title"
        )
        result = self.brief_runtime.generate_brief_fields(
            PodBriefRequest(
                brief_id=brief_id,
                brief=request.brief,
                locale=request.locale,
                fabric_craft=request.fabric_craft,
                template_image=template_image,
                template_content_type=template_content_type,
            ),
            grant=billing_run.grant,
            call_id=call_ids[0],
            call_ids=call_ids,
            on_start=lambda call_id: billing_run.start(call_id, "pod.title"),
            on_outcome=lambda call_id, status: billing_run.record(call_id, "pod.title", status),
        )
        return {
            "brief_id": brief_id,
            "prompt_version": result.prompt_version,
            "model": result.model,
            "fields": result.fields.model_dump(),
            "art_direction": (
                result.art_direction.model_dump() if result.art_direction is not None else None
            ),
        }

    def generate_composition(
        self, actor: Actor, request: CompositionRequest, *, scope: str = COMPOSITION_SCOPE_FULL
    ) -> dict[str, Any]:
        """构图定制：一段大白话 → 四格画面指令，新增一份模板并设为生效。

        动作免费（服务端对 POD 画像的纯 title scope 显式零计费），但仍走完整的
        冻结 → 发放短期密钥 → 调用 → 结算流程，保留幂等键与审计。
        ``scope`` 决定写入哪个模块的模板池（full / replica / fission），各池独立生效。
        """
        if self.composition_runtime is None:
            raise RuntimeError("POD 构图定制服务未启用")
        composition_id = uuid.uuid4().hex
        billing_run = self._freeze_composition(actor, composition_id)
        try:
            return self._run_composition_authorized(actor, request, composition_id, billing_run, scope)
        except PodBillingAuthorizationRequired as exc:
            raise RuntimeError(str(exc)) from exc
        finally:
            billing_run.settle()

    def _run_composition_authorized(
        self,
        actor: Actor,
        request: CompositionRequest,
        composition_id: str,
        billing_run: PodBillingRun,
        scope: str = COMPOSITION_SCOPE_FULL,
    ) -> dict[str, Any]:
        call_ids = tuple(
            call.call_id for call in billing_run.plan.calls if call.feature == "pod.title"
        )
        result = self.composition_runtime.generate_composition(
            PodCompositionRequest(
                composition_id=composition_id,
                brief=request.brief,
                locale=request.locale,
                scope=scope,
            ),
            grant=billing_run.grant,
            call_id=call_ids[0],
            call_ids=call_ids,
            on_start=lambda call_id: billing_run.start(call_id, "pod.title"),
            on_outcome=lambda call_id, status: billing_run.record(call_id, "pod.title", status),
        )
        panels = result.panels.model_dump()
        if scope == COMPOSITION_SCOPE_REPLICA:
            # 主图视角不可指定：无论模型返回什么，panel_1 一律落成固定占位，
            # 保证模板只表达派生三格（panel_2~4）的机位。
            panels["panel_1"] = {
                "zh": REPLICA_MAIN_PANEL_PLACEHOLDER_ZH,
                "en": REPLICA_MAIN_PANEL_PLACEHOLDER_EN,
            }
        stored = self.repository.create_composition(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            composition_id=composition_id,
            name=request.name or _composition_default_name(request.brief),
            raw_input=request.brief,
            panels=panels,
            model=result.model,
            prompt_version=result.prompt_version,
            scope=scope,
            # 全定制新模板按「四轮」语义保存；复刻/裂变仍是「四格」语义。
            layout="role_v1" if scope == COMPOSITION_SCOPE_FULL else "panel_v1",
        )
        return self._composition_payload(stored)

    def save_composition(
        self, actor: Actor, composition_id: str, request: CompositionUpdateRequest
    ) -> dict[str, Any]:
        """保存对某一份模板的手动编辑：后台按中文重新转写英文，改原记录、不新增。"""
        if self.composition_runtime is None:
            raise RuntimeError("POD 构图定制服务未启用")
        if self.repository.get_composition(actor.workspace_id, actor.id, composition_id) is None:
            raise PodRepositoryError("POD composition not found", 404)
        # 计费幂等键必须唯一：编辑沿用同一 composition_id 会与「生成」时的 run 撞键，
        # 因此每次保存都用新 token（该动作零计费，不需要跨次幂等）。
        billing_run = self._freeze_composition(actor, uuid.uuid4().hex)
        try:
            return self._run_composition_localize_authorized(actor, composition_id, request, billing_run)
        except PodBillingAuthorizationRequired as exc:
            raise RuntimeError(str(exc)) from exc
        finally:
            billing_run.settle()

    def _run_composition_localize_authorized(
        self,
        actor: Actor,
        composition_id: str,
        request: CompositionUpdateRequest,
        billing_run: PodBillingRun,
    ) -> dict[str, Any]:
        call_ids = tuple(
            call.call_id for call in billing_run.plan.calls if call.feature == "pod.title"
        )
        result = self.composition_runtime.localize_composition(
            PodCompositionLocalizeRequest(
                composition_id=composition_id, panels=request.panels.model_dump()
            ),
            grant=billing_run.grant,
            call_id=call_ids[0],
            call_ids=call_ids,
            on_start=lambda call_id: billing_run.start(call_id, "pod.title"),
            on_outcome=lambda call_id, status: billing_run.record(call_id, "pod.title", status),
        )
        stored = self.repository.update_composition_panels(
            actor.workspace_id,
            actor.id,
            composition_id,
            panels=result.panels.model_dump(),
            model=result.model,
            prompt_version=result.prompt_version,
        )
        if stored is None:
            raise PodRepositoryError("POD composition not found", 404)
        return self._composition_payload(stored)

    def list_compositions(
        self, actor: Actor, *, scope: str = COMPOSITION_SCOPE_FULL
    ) -> dict[str, Any]:
        """列出该模块的构图模板：系统内置「默认模板」永远排第一，其余为用户模板（生效的排前）。"""

        records = self.repository.list_compositions(actor.workspace_id, actor.id, scope=scope)
        templates = [self._composition_payload(record) for record in records]
        default = self._default_composition_payload(scope)
        # 没有任何用户模板生效时，即回退到系统默认模板。
        default["is_active"] = not any(template["is_active"] for template in templates)
        return {"templates": [default, *templates], "total": len(templates) + 1}

    @staticmethod
    def _default_composition_payload(scope: str = COMPOSITION_SCOPE_FULL) -> dict[str, Any]:
        # 全定制用「四轮差异化机位」默认模板；复刻/裂变仍是旧四格语义。
        column_scope = scope == COMPOSITION_SCOPE_FULL
        panels = (
            DEFAULT_COLUMN_COMPOSITION_PANELS if column_scope else DEFAULT_COMPOSITION_PANELS
        )
        return {
            "composition_id": DEFAULT_COMPOSITION_ID,
            "name": DEFAULT_COMPOSITION_NAME,
            "raw_input": (
                DEFAULT_COLUMN_COMPOSITION_RAW_INPUT
                if column_scope
                else DEFAULT_COMPOSITION_RAW_INPUT
            ),
            "panels": {
                key: {"zh": str(value["zh"]), "en": str(value["en"])}
                for key, value in panels.items()
            },
            "model": "",
            "prompt_version": "pod-composition-default",
            "is_active": False,
            "is_builtin": True,
            "updated_at": "",
            "layout": "role_v1" if column_scope else "panel_v1",
            "scope": scope if scope in (COMPOSITION_SCOPE_FULL, COMPOSITION_SCOPE_REPLICA, COMPOSITION_SCOPE_FISSION) else COMPOSITION_SCOPE_FULL,
        }

    def get_active_composition(
        self, actor: Actor, *, scope: str = COMPOSITION_SCOPE_FULL
    ) -> dict[str, Any] | None:
        stored = self.repository.get_active_composition(actor.workspace_id, actor.id, scope=scope)
        return self._composition_payload(stored) if stored else None

    def rename_composition(
        self, actor: Actor, composition_id: str, request: CompositionRenameRequest
    ) -> dict[str, Any]:
        stored = self.repository.rename_composition(
            actor.workspace_id, actor.id, composition_id, request.name
        )
        if stored is None:
            raise PodRepositoryError("POD composition not found", 404)
        return self._composition_payload(stored)

    def activate_composition(
        self, actor: Actor, composition_id: str, *, scope: str = COMPOSITION_SCOPE_FULL
    ) -> dict[str, Any]:
        if composition_id == DEFAULT_COMPOSITION_ID:
            # 选择系统默认模板 = 清空该模块用户模板的生效标记（新建批次回退默认机位）。
            self.repository.clear_active_compositions(actor.workspace_id, actor.id, scope=scope)
            payload = self._default_composition_payload(scope)
            payload["is_active"] = True
            return payload
        stored = self.repository.activate_composition(
            actor.workspace_id, actor.id, composition_id, scope=scope
        )
        if stored is None:
            # 可能是不属于该模块（scope）的模板：明确 404，避免跨池误激活。
            existing = self.repository.get_composition(actor.workspace_id, actor.id, composition_id)
            if existing is not None:
                raise PodRepositoryError("构图模板不属于当前模块，无法激活", 409)
            raise PodRepositoryError("POD composition not found", 404)
        return self._composition_payload(stored)

    def delete_composition(self, actor: Actor, composition_id: str) -> dict[str, Any]:
        if not self.repository.delete_composition(actor.workspace_id, actor.id, composition_id):
            raise PodRepositoryError("POD composition not found", 404)
        return {"deleted": True}

    @staticmethod
    def _composition_payload(stored: dict[str, Any], *, is_builtin: bool = False) -> dict[str, Any]:
        raw_panels = stored.get("panels") if isinstance(stored.get("panels"), dict) else {}
        panels: dict[str, dict[str, str]] = {}
        for key in COMPOSITION_PANEL_KEYS:
            entry = raw_panels.get(key)
            if isinstance(entry, dict):
                panels[key] = {"zh": str(entry.get("zh") or ""), "en": str(entry.get("en") or "")}
            else:
                panels[key] = {"zh": str(entry or ""), "en": ""}
        return {
            "composition_id": str(stored.get("composition_id") or ""),
            "name": str(stored.get("name") or ""),
            "raw_input": str(stored.get("raw_input") or ""),
            "panels": panels,
            "model": str(stored.get("model") or ""),
            "prompt_version": str(stored.get("prompt_version") or ""),
            "is_active": bool(stored.get("is_active")),
            "is_builtin": bool(is_builtin or stored.get("is_builtin")),
            "updated_at": str(stored.get("updated_at") or ""),
            "scope": str(stored.get("scope") or COMPOSITION_SCOPE_FULL),
            # 构图语义来源：panel_v1（旧四格）/ role_v1（四轮）。
            "layout": str(stored.get("layout") or "panel_v1"),
        }

    def list_batches(self, actor: Actor, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        rows, total = self.repository.list_batches(
            actor.workspace_id,
            actor.id,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
            # 两种模式共表：这里只列全定制批次，否则全定制页会把更晚创建的半定制
            # 批次当成「最近一批」展开，用全定制界面渲染纯图案批次。
            mode="full",
        )
        return {"batches": [self._batch_summary(row) for row in rows], "total": total}

    def get_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        if self.worker is None or not self.worker.is_batch_running(batch_id):
            self.repository.reconcile_stale_generating_titles(batch_id)
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def pause_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] in {"paused", "pausing"}:
            return self._batch_payload(batch)
        if not self.repository.request_pause(batch_id):
            raise PodRepositoryError("仅运行中的 POD 批次可以暂停", 409)
        try:
            business_logger("pod_processing").info(
                "POD 批次暂停请求 | batch_id=%s | workspace=%s", batch_id, actor.workspace_id)
        except Exception:  # noqa: BLE001
            pass
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def cancel_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] == "cancelled":
            return self._batch_payload(batch)
        worker_running = self.worker is not None and self.worker.is_batch_running(batch_id)

        def finish_cancelled() -> None:
            self.repository.fail_remaining_items(batch_id, "POD 批次已取消")
            self.repository.fail_pending_titles(batch_id, "POD 批次已取消")
            self.repository.mark_batch_cancelled(batch_id, "POD 批次已取消")

        if batch["status"] == "cancelling":
            if not worker_running:
                finish_cancelled()
                batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
            return self._batch_payload(batch)
        was_paused = batch["status"] == "paused"
        if not self.repository.request_cancel(batch_id):
            raise PodRepositoryError("仅运行中或已暂停的 POD 批次可以取消", 409)
        try:
            business_logger("pod_processing").warning(
                "POD 批次取消 | batch_id=%s | workspace=%s | 原状态=%s",
                batch_id, actor.workspace_id, str(batch["status"] or "-"))
        except Exception:  # noqa: BLE001
            pass
        if was_paused or not worker_running:
            # 已暂停或 worker 已退出的批次不会再经过检查点，需同步收尾。
            finish_cancelled()
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def delete_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        relative_paths = self.repository.delete_batch(batch_id, actor.workspace_id, actor.id)
        for relative_path in relative_paths:
            try:
                self.assets.remove(relative_path)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "POD delete_batch failed to remove local file: %s", relative_path
                )
        return {"deleted": batch_id}

    def resume_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] != "paused":
            raise PodRepositoryError("仅已暂停的 POD 批次可以继续", 409)
        # 旧版复刻计划（无 v4 主图方案）只读：续跑会用错误提示词重跑，直接拒绝。
        self._require_replica_retry_supported(batch)
        # Resuming a paused batch always creates a fresh plan for the
        # remaining styles. It never asks the user to re-authorize an old run.
        run = self._freeze_paused_batch_remainder(actor, batch)
        if not self.repository.resume_paused_batch(batch_id):
            self._settle_unclaimed_retry(run)
            raise PodRepositoryError("POD 批次无法继续", 409)
        if self.worker is None:
            raise RuntimeError("POD worker is disabled")
        self.worker.register_billing_run(batch_id, run)
        self.worker.submit(batch_id, run)
        try:
            business_logger("pod_processing").info(
                "POD 批次恢复 | batch_id=%s | workspace=%s", batch_id, actor.workspace_id)
        except Exception:  # noqa: BLE001
            pass
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def export_dianxiaomi(self, actor: Actor, batch_id: str) -> DianxiaomiExport:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        copies = self.repository.get_style_copies(batch_id, actor.workspace_id, actor.id)
        self._ensure_exportable(batch, copies)
        exported = build_pod_dianxiaomi_export(batch, copies)
        record = self.export_records.record_success(
            batch_id=batch_id,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            file_name=exported.filename,
            format="dianxiaomi_xlsx",
            exported_count=exported.exported_style_count,
            skipped_count=exported.skipped_style_count,
        )
        return DianxiaomiExport(
            content=exported.content,
            exported_style_count=exported.exported_style_count,
            skipped_style_count=exported.skipped_style_count,
            filename=exported.filename,
            export_id=record["id"],
        )

    def export_miaoshou(self, actor: Actor, batch_id: str, kind: str) -> PodWorkbookExport:
        """按妙手 Temu 导入模板导出（kind：apparel 服饰类 / general 非服饰类）。"""
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        copies = self.repository.get_style_copies(batch_id, actor.workspace_id, actor.id)
        self._ensure_exportable(batch, copies)
        exported = build_pod_miaoshou_export(batch, copies, kind)
        record = self.export_records.record_success(
            batch_id=batch_id,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            file_name=exported.filename,
            format=f"miaoshou_{kind}_xlsx",
            exported_count=exported.exported_style_count,
            skipped_count=exported.skipped_style_count,
        )
        return PodWorkbookExport(
            content=exported.content,
            exported_style_count=exported.exported_style_count,
            skipped_style_count=exported.skipped_style_count,
            filename=exported.filename,
            export_id=record["id"],
        )

    def _ensure_exportable(self, batch: dict[str, Any], copies: dict[int, Any]) -> None:
        """导出前置校验：把「不能导出」的原因映射为可读的 409（店小秘/妙手共用）。"""
        analysis = analyze_dianxiaomi_export(batch, copies)
        if analysis.block_reason is None:
            return
        messages = {
            "active_batch": "pod 制作尚未完成，请等待全部完成重试",
            "listing_fields_missing": "POD 批次缺少上架信息快照，无法导出",
            "style_copy_missing": "POD 款式文案缺失，无法导出",
            "no_exportable_styles": "POD 批次没有可导出的款式",
            "all_exportable_styles_unselected": (
                "POD 批次没有可导出的款式：所有就绪款式均已被取消勾选"
            ),
            "billing_recovery_required": "POD 批次仍有未完成的图片/标题/文案工作",
        }
        raise PodRepositoryError(messages[analysis.block_reason], 409)

    def set_style_export_selection(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        selected: bool,
    ) -> dict[str, Any]:
        selected = self.repository.upsert_style_export_selection(
            batch_id,
            actor.workspace_id,
            actor.id,
            style_index,
            selected=selected,
        )
        return {"style_index": style_index, "export_selected": selected}

    def list_exports(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        rows = self.export_records.list_for_batch(
            batch_id=batch_id,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
        )
        return {"exports": rows, "total": len(rows)}

    # --- 第 4 张图「规格卡」：同源预览 + 终态重印（方案 §7/§8） ---

    SPEC_CARD_TERMINAL_STATUSES = frozenset({"completed", "partial_failure", "failed"})
    SPEC_CARD_PREVIEW_BASE_SIDE = 800

    def template_spec_card_base(self, actor: Actor, template_id: str) -> bytes | None:
        """预览底图：模板资产字节；模板不存在/资产不可读都返回 None（用空白底图，不报错）。"""

        try:
            template = self.repository.get_template(template_id, actor.workspace_id, actor.id)
            asset = self.repository.get_asset(template["asset_id"], actor.workspace_id, actor.id)
            return self.assets.read(asset["relative_path"])
        except Exception:  # noqa: BLE001 - 预览底图缺失不影响示意
            return None

    def preview_spec_card(self, config_mapping: Any, base_content: bytes | None = None) -> bytes:
        """规格卡同源渲染预览：返回 JPEG 字节；**不落库、不计费**（方案 §7）。

        配置非法（空表/超行超列/风格或位置不识别）抛 ValueError，由路由转 400。
        """

        config = self._validated_spec_card_config(config_mapping)
        if not config.enabled:
            # 「不印到图上」：预览与生成结果一致 —— 直接给干净底图（无底图时给空白示意底图）。
            return base_content or blank_spec_card_base_jpeg(self.SPEC_CARD_PREVIEW_BASE_SIDE)
        request = spec_card.SpecCardRequest(
            cells=display_spec_card_cells(config.cells, config.display_unit),
            style=config.style,
            corner=config.corner,
        )
        if base_content:
            try:
                return spec_card.render_spec_card(base_content, request).jpeg_bytes
            except spec_card.SpecCardRenderError:
                # 底图不可读/非正方形/过小：退回空白示意底图，不把预览变成报错。
                pass
        blank = blank_spec_card_base_jpeg(self.SPEC_CARD_PREVIEW_BASE_SIDE)
        return spec_card.render_spec_card(blank, request).jpeg_bytes

    def reprint_batch_spec_card(
        self,
        actor: Actor,
        batch_id: str,
        config_mapping: Any,
        style_index: int | None = None,
    ) -> dict[str, Any]:
        """终态批次「保存并全批重印」：0 provider 调用，逐款独立（方案 §8）。

        只替换 ``publications.public_url``；某款失败保持现状并计入 errors，其余款继续。
        """

        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in self.SPEC_CARD_TERMINAL_STATUSES:
            raise BatchNotTerminalForSpecCard()
        if batch.get("mode") == "replica" and style_index is None:
            # 复刻每款规格卡独立，必须按款重印，禁止无 style_index 的全批更新。
            raise PodRepositoryError("复刻批次重印必须指定 style_index", 422)
        config = self._validated_spec_card_config(config_mapping)
        if batch.get("mode") == "replica":
            # 复刻每款配置独立：只更新被指定款的快照，不改整批镜像配置、不动其他款。
            if not self.repository.update_replica_target_spec_card(
                batch_id, int(style_index), config.model_dump()
            ):
                raise PodRepositoryError("POD 复刻目标不存在", 404)
        else:
            self.repository.update_batch_spec_card(batch_id, config.model_dump())
        targets = self.repository.list_spec_card_hero_targets(batch_id)
        if style_index is not None:
            wanted = int(style_index)
            targets = [target for target in targets if int(target["style_index"]) == wanted]
        reprinted = 0
        errors: list[dict[str, Any]] = []
        for target in targets:
            index = int(target["style_index"])
            self._log_spec_card_audit(actor, batch_id, index, "重印开始")
            try:
                public_url = self._reprint_style_spec_card(batch, target, config)
            except Exception as exc:  # noqa: BLE001 - 逐款独立，失败款保持现状
                message = safe_error_message(exc) or exc.__class__.__name__
                errors.append({"style_index": index, "message": message})
                self._log_spec_card_audit(
                    actor, batch_id, index, f"重印失败，保持现状：{message}", level="warning"
                )
                continue
            reprinted += 1
            self._log_spec_card_audit(actor, batch_id, index, f"重印完成 | public_url={public_url}")
        return {
            "saved": True,
            "reprinted": reprinted,
            "failed": len(errors),
            "errors": errors,
            "needs_re_export": True,
        }

    def _reprint_style_spec_card(self, batch: dict[str, Any], target: dict[str, Any], config: Any) -> str:
        """单款重印：读干净母版 → 渲染 → 派生资产 → 发布 → 只改发布指针。"""

        style_index = int(target["style_index"])
        pattern_asset_id = str(target.get("pattern_asset_id") or "")
        if not pattern_asset_id:
            raise PodRepositoryError("POD 母版资产已不可用，无法重新合成标注", 404)
        asset = self.repository.get_asset(
            pattern_asset_id, batch["workspace_id"], batch["owner_user_id"]
        )
        base_content = self.assets.read(asset["relative_path"])
        if config.enabled:
            result = spec_card.render_spec_card(
                base_content,
                spec_card.SpecCardRequest(
                    cells=display_spec_card_cells(config.cells, config.display_unit),
                    style=config.style,
                    corner=config.corner,
                ),
            )
            rendered = result.jpeg_bytes
        else:
            # 「不印到图上」：重印即去掉已印的卡片，素材图回到干净母版。
            rendered = base_content
        self._save_batch_asset(
            batch, SPEC_CARD_ASSET_KIND, f"style-{style_index}-hero-card.jpg", rendered
        )
        public_url = self.ai_runtime.publish_listing_image(
            build_spec_card_media(rendered),
            namespace=batch["workspace_id"],
            role="hero",
        )
        if not public_url:
            raise RuntimeError("重印后的规格卡未取得可公开访问的地址")
        if not self.repository.set_style_grid_publication(target["result_id"], "hero", public_url):
            raise PodRepositoryError("POD style result not found", 404)
        return str(public_url)

    def _save_batch_asset(
        self, batch: dict[str, Any], kind: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        stored = self.assets.save_image(batch["workspace_id"], batch["owner_user_id"], content)
        return self.repository.create_asset(
            workspace_id=batch["workspace_id"],
            owner_user_id=batch["owner_user_id"],
            kind=kind,
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )

    @staticmethod
    def _log_spec_card_audit(
        actor: Actor, batch_id: str, style_index: int, detail: str, *, level: str = "info"
    ) -> None:
        try:
            getattr(business_logger("pod_processing"), level)(
                "POD 规格卡重印 | batch_id=%s | style=%d | 操作人=%s | workspace=%s | %s",
                batch_id, style_index, getattr(actor, "username", "") or actor.id,
                actor.workspace_id, detail)
        except Exception:  # noqa: BLE001 - 审计日志绝不阻断业务
            pass

    @staticmethod
    def _validated_spec_card_config(config_mapping: Any) -> Any:
        """整卡校验（空表/行列表格上限/风格与位置）；错误一律是带中文文案的 ValueError。"""

        try:
            return validate_spec_card(config_mapping)
        except ValidationError as exc:
            raise ValueError(_spec_card_validation_message(exc)) from exc

    def optimize_scene(
        self,
        actor: Actor,
        batch_id: str,
        item_id: str,
        *,
        instruction: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        item = self.repository.claim_scene_optimization(
            batch_id, item_id, actor.workspace_id, actor.id
        )
        billing_run = self._freeze_retry(
            actor,
            f"{batch_id}:item:{item_id}:scene:{uuid.uuid4().hex}",
            "pod.image",
            action_type="scene_optimization",
            target_id=item_id,
            batch_id=batch_id,
            action_payload={"instruction": instruction},
        )
        self._sync_retry_epoch(billing_run)
        if self.worker is not None:
            self.worker.register_action_billing_run(f"scene:{batch_id}:{item_id}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_scene_optimization(batch_id, item_id, instruction, billing_run)
        return self._item_payload(item)

    def regenerate_item(
        self,
        actor: Actor,
        batch_id: str,
        item_id: str,
        *,
        creative_prompt: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        item = self.repository.claim_item_regeneration(
            batch_id, item_id, actor.workspace_id, actor.id
        )
        billing_run = self._freeze_retry(
            actor,
            f"{batch_id}:item:{item_id}:retry:{uuid.uuid4().hex}",
            "pod.image",
            action_type="item_retry",
            target_id=item_id,
            batch_id=batch_id,
            action_payload={"creative_prompt": creative_prompt},
        )
        self._sync_retry_epoch(billing_run)
        if self.worker is not None:
            self.worker.register_action_billing_run(f"item:{batch_id}:{item_id}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_item_regeneration(batch_id, item_id, creative_prompt, billing_run)
        return self._item_payload(item)

    def regenerate_style(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        creative_prompt: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        with self._regeneration_lock:
            return self._regenerate_style(
                actor, batch_id, style_index, creative_prompt=creative_prompt, enqueue=enqueue
            )

    def _sync_retry_epoch(self, billing_run: PodBillingRun) -> None:
        """把 claim 之后的批次 epoch 写回 billing run。

        claim 会把批次推进到新一轮（execution_epoch + 1），而重试用的 billing run
        是在 claim 之前冻结的，epoch 仍停在旧值（0）。不刷新的话，这条路径的写入
        与结算都不带 ``AND execution_epoch = ?`` 谓词，可能覆盖 reaper 已经判定的
        失败终态（表现为「已判失败又变完成」）。
        """
        current = self.repository.get_batch_execution_epoch(billing_run.action_key)
        if current is not None:
            billing_run.execution_epoch = current

    def _regenerate_style(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        creative_prompt: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        self._preflight_style_retry(actor, batch_id, style_index)
        action_id = f"{batch_id}:style:{style_index}:retry:{uuid.uuid4().hex}"
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        billing_run = self._freeze_style_retry(
            actor, action_id, batch_id, style_index, creative_prompt,
            semi=batch.get("mode") == "semi",
            billing_profile=self._pod_billing_profile(batch),
            replica_generation_strategy=self._batch_panel_generation_strategy(batch_id),
        )
        try:
            results = self.repository.claim_style_regeneration(
                batch_id, style_index, actor.workspace_id, actor.id
            )
        except Exception:
            self._settle_unclaimed_retry(billing_run)
            raise
        self._sync_retry_epoch(billing_run)
        if self.worker is not None:
            self.worker.register_action_billing_run(f"style:{batch_id}:{style_index}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_style_regeneration(batch_id, style_index, creative_prompt, billing_run)
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        title = next((row for row in batch.get("style_titles", []) if row["style_index"] == style_index), None)
        return {
            "style_index": style_index,
            "results": [self._item_payload(item) for item in results],
            "title": self._title_payload(title) if title is not None else None,
        }

    def regenerate_title(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        enqueue: bool = True,
    ) -> dict[str, Any]:
        with self._regeneration_lock:
            return self._regenerate_title(actor, batch_id, style_index, enqueue=enqueue)

    def _regenerate_title(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        enqueue: bool = True,
    ) -> dict[str, Any]:
        self._require_title_runtime_configured(require_present=True)
        self._preflight_title_retry(actor, batch_id, style_index)
        action_id = f"{batch_id}:style:{style_index}:title-retry:{uuid.uuid4().hex}"
        billing_run = self._freeze_retry(
            actor,
            action_id,
            "pod.title",
            action_type="title_retry",
            target_id=str(style_index),
            batch_id=batch_id,
        )
        try:
            title = self.repository.claim_title_regeneration(
                batch_id, style_index, actor.workspace_id, actor.id
            )
        except Exception:
            self._settle_unclaimed_retry(billing_run)
            raise
        self._sync_retry_epoch(billing_run)
        if self.worker is not None:
            self.worker.register_action_billing_run(f"title:{batch_id}:{style_index}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_title_regeneration(batch_id, style_index, billing_run)
        return self._title_payload(title)

    def set_manual_title(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        title: str,
    ) -> dict[str, Any]:
        clean = str(title or "").strip()
        if not clean:
            raise ValueError("manual title is required")
        self._preflight_manual_title(actor, batch_id, style_index)
        self.repository.complete_manual_title(
            batch_id, style_index, clean, actor.workspace_id, actor.id
        )
        self.repository.settle_batch_by_listing_readiness(batch_id)
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        saved = next(
            (row for row in batch["style_titles"] if int(row["style_index"]) == int(style_index)),
            None,
        )
        if saved is None:
            raise PodRepositoryError("POD style title not found", 404)
        return self._title_payload(saved)

    def retry_failed(
        self,
        actor: Actor,
        batch_id: str,
        *,
        image_style_indices: list[int],
        title_style_indices: list[int],
        enqueue: bool = True,
    ) -> dict[str, Any]:
        request = BatchRetryFailedCreate(
            image_style_indices=image_style_indices,
            title_style_indices=title_style_indices,
        )
        image_indices = tuple(sorted(request.image_style_indices))
        title_indices = tuple(sorted(request.title_style_indices))
        if title_indices:
            self._require_title_runtime_configured(require_present=True)
        self._preflight_batch_retry(actor, batch_id, image_indices, title_indices)
        try:
            business_logger("pod_processing").info(
                "POD 失败重试 | batch_id=%s | workspace=%s | 图片款式=%s | 标题款式=%s",
                batch_id, actor.workspace_id,
                ",".join(str(i) for i in image_indices) or "-",
                ",".join(str(i) for i in title_indices) or "-")
        except Exception:  # noqa: BLE001
            pass
        action_id = f"{batch_id}:batch-retry:{uuid.uuid4().hex}"
        billing_run = self._freeze_batch_retry(
            actor, action_id, batch_id, image_indices, title_indices
        )
        try:
            self.repository.claim_batch_retry(
                batch_id,
                actor.workspace_id,
                actor.id,
                image_style_indices=image_indices,
                title_style_indices=title_indices,
            )
        except Exception:
            self._settle_unclaimed_retry(billing_run)
            raise
        self._sync_retry_epoch(billing_run)
        if self.worker is not None:
            self.worker.register_action_billing_run(f"batch-retry:{batch_id}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_batch_retry(batch_id, image_indices, title_indices, billing_run)
        return {
            "image_style_indices": list(image_indices),
            "title_style_indices": list(title_indices),
            "submitted_image_style_count": len(image_indices),
            "submitted_title_style_count": len(title_indices),
        }

    @staticmethod
    def _reject_column_rerun(batch: dict[str, Any]) -> None:
        """列式批次 v1 不提供用户侧重跑：已有入口要求 4 行一起重置，会冲掉已完成图。"""
        if str(batch.get("generation_layout") or "") == GENERATION_LAYOUT_COLUMN:
            raise PodRepositoryError(
                "列式生成批次暂不支持单款重生成或批量重试，请重新建批", 409
            )

    @staticmethod
    def _require_replica_retry_supported(batch: dict[str, Any]) -> None:
        """复刻旧批次只读：任意一款的计划版本不是 v4 就拒绝重试 / 续跑。

        旧计划里没有 v4 的主图提示词与派生格，重跑只会用旧提示词产出错误结果；
        历史批次仍可查看与导出。
        """
        if str(batch.get("mode") or "") != "replica":
            return
        for target in batch.get("replica_targets") or []:
            raw = str(target.get("replica_plan_json") or "").strip()
            version = ""
            if raw and raw != "{}":
                try:
                    version = str((json.loads(raw) or {}).get("plan_version") or "")
                except (TypeError, ValueError):
                    version = ""
            if version != REPLICA_PLAN_VERSION:
                raise PodRepositoryError("历史复刻批次不支持重试", 409)

    def _preflight_style_retry(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        self._reject_column_rerun(batch)
        self._require_replica_retry_supported(batch)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
            raise PodRepositoryError("POD batch must settle before regenerating one style", 409)
        results = [
            item for item in batch.get("items", [])
            if int(item.get("style_index") or 0) == int(style_index)
        ]
        statuses = {str(item.get("status") or "") for item in results}
        if len(results) != 4 or statuses not in ({"failed"}, {"completed"}):
            raise PodRepositoryError("only a settled POD style can be regenerated", 409)

    def _preflight_title_retry(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
            raise PodRepositoryError("POD batch must settle before regenerating its title", 409)
        title = next(
            (row for row in batch.get("style_titles", []) if int(row["style_index"]) == int(style_index)),
            None,
        )
        results = [
            item for item in batch.get("items", [])
            if int(item.get("style_index") or 0) == int(style_index)
        ]
        if title is None or title.get("status") not in {"failed", "completed"}:
            raise PodRepositoryError("only a settled POD title can be regenerated", 409)
        if len(results) != 4 or any(
            item.get("status") != "completed" or not item.get("public_url") for item in results
        ):
            raise PodRepositoryError("all four public POD images are required before regenerating a title", 409)

    def _preflight_manual_title(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled"}:
            if batch["status"] == "settlement_pending":
                raise PodRepositoryError("POD billing settlement is pending", 409)
            raise PodRepositoryError("POD batch must settle before saving a manual title", 409)

    def _preflight_batch_retry(
        self,
        actor: Actor,
        batch_id: str,
        image_style_indices: tuple[int, ...],
        title_style_indices: tuple[int, ...],
    ) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        self._reject_column_rerun(batch)
        self._require_replica_retry_supported(batch)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
            raise PodRepositoryError("POD batch must settle before retrying failed styles", 409)
        if any(index > int(batch["requested_count"]) for index in (*image_style_indices, *title_style_indices)):
            raise PodRepositoryError("POD style index is outside the batch range", 422)
        for style_index in image_style_indices:
            results = [item for item in batch["items"] if int(item.get("style_index") or 0) == style_index]
            if len(results) != 4 or all(item.get("status") == "completed" for item in results):
                raise PodRepositoryError("only styles with unfinished images can be retried", 409)
        for style_index in title_style_indices:
            title = next(
                (row for row in batch["style_titles"] if int(row["style_index"]) == style_index),
                None,
            )
            results = [item for item in batch["items"] if int(item.get("style_index") or 0) == style_index]
            if (
                title is None
                or title.get("status") != "failed"
                or not title.get("style_task_id")
                or len(results) != 4
                or any(item.get("status") != "completed" or not item.get("public_url") for item in results)
            ):
                raise PodRepositoryError(
                    "only a failed POD title with four public images can be retried", 409
                )

    @staticmethod
    def _settle_unclaimed_retry(billing_run: PodBillingRun) -> None:
        try:
            billing_run.settle()
        except Exception:
            # The durable billing run remains settlement_pending for settlement
            # bookkeeping; it must not lock the failed generation retry path.
            pass

    def close(self) -> None:
        if self._recon_thread is not None:
            self._recon_stop.set()
            self._recon_thread.join(timeout=5.0)
            self._recon_thread = None
        if self._reaper_thread is not None:
            self._reaper_stop.set()
            self._reaper_thread.join(timeout=5.0)
            self._reaper_thread = None
        if self._cache_sweep_thread is not None:
            self._cache_sweep_stop.set()
            self._cache_sweep_thread.join(timeout=5.0)
            self._cache_sweep_thread = None
        # Revoke active epochs before cancelling the local executors.  Provider
        # calls already inside requests cannot be force-killed safely, but any
        # result they deliver after this point is rejected by the repository.
        self.repository.pause_billing_runs_for_shutdown()
        if self.worker is not None:
            self.worker.close()

    def _start_reaper(self) -> None:
        """Start the background reaper daemon that revokes stale batch epochs."""
        self._reaper_stop.clear()
        self._reaper_thread = threading.Thread(
            target=self._run_stuck_batch_reaper,
            name="pod-batch-reaper",
            daemon=True,
        )
        self._reaper_thread.start()

    def _run_stuck_batch_reaper(self) -> None:
        """Loop: reap stale batches every 60 seconds until stopped."""
        import logging
        logger = logging.getLogger(__name__)
        while not self._reaper_stop.wait(timeout=60.0):
            try:
                self.reap_stuck_batches_once()
                self.settle_stuck_billing_runs()
            except Exception as exc:
                logger.warning("POD reaper encountered an error: %s", exc)

    def _start_cache_sweeper(self) -> None:
        """Start the background sweeper that clears stale local image cache."""
        self._cache_sweep_stop.clear()
        self._cache_sweep_thread = threading.Thread(
            target=self._run_cache_sweeper,
            name="pod-stale-cache-sweeper",
            daemon=True,
        )
        self._cache_sweep_thread.start()

    def _run_cache_sweeper(self) -> None:
        """Loop: clear stale local cache once, then every 48 hours until stopped."""
        import logging
        logger = logging.getLogger(__name__)
        while True:
            try:
                self.reap_stale_local_cache_once()
            except Exception as exc:
                logger.warning("POD stale-cache sweeper encountered an error: %s", exc)
            if self._cache_sweep_stop.wait(timeout=48 * 3600):
                break

    # ------------------------------------------------------------------ #
    # 启动对账与日报对平（设计第 7 节）
    # ------------------------------------------------------------------ #

    def _billing_reconciler(self) -> PodBillingReconciler:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        if self._billing_reconciler_instance is None:
            self._billing_reconciler_instance = PodBillingReconciler(
                self.repository,
                self.billing_coordinator,
                settle_run=self._settle_stored_billing_run,
                plan_builder=self._billing_plan,
                device_id=self._device_id,
            )
        return self._billing_reconciler_instance

    def _start_billing_reconciler(self) -> None:
        """每 10 分钟对一次未关单的账；登录成功时另有一次性触发。"""
        self._recon_stop.clear()
        self._recon_thread = threading.Thread(
            target=self._run_billing_reconciler,
            name="pod-billing-reconciler",
            daemon=True,
        )
        self._recon_thread.start()

    def _run_billing_reconciler(self) -> None:
        import logging

        while not self._recon_stop.wait(timeout=600.0):
            try:
                self.reconcile_billing_runs_once()
            except Exception as exc:  # noqa: BLE001 - 后台线程不能因单轮失败退出
                logging.getLogger(__name__).warning(
                    "POD billing recon encountered an error: %s", exc
                )

    def reconcile_billing_runs(
        self, actor: Actor, *, scope: str = "startup"
    ) -> dict[str, Any]:
        """逐笔收敛该用户的非终态账（启动、定时、登录后共用）。"""
        return self._billing_reconciler().reconcile_actor(actor, scope=scope)

    def reconcile_billing_runs_once(self) -> dict[str, Any]:
        """对本地所有有未关单账的用户各跑一轮（取不到远端凭证的用户自动跳过）。"""
        if self.billing_coordinator is None:
            return {"owners": 0, "reconciled": 0}
        owners = self.repository.list_billing_run_owners()
        reconciled = 0
        for workspace_id, owner_id in owners:
            actor = Actor(id=owner_id, username="", role="", workspace_id=workspace_id)
            try:
                self.reconcile_billing_runs(actor, scope="scheduled")
                reconciled += 1
            except CustomerBillingPermissionError:
                # 该用户没有活跃远端会话：留到下次（登录后）再跑。
                continue
            except Exception:
                continue
        return {"owners": len(owners), "reconciled": reconciled}

    def reconcile_billing_daily(
        self, actor: Actor, *, date: str = ""
    ) -> dict[str, Any]:
        """本地出账 + 与服务端日报逐项比对；差额下钻后写入对账流水。"""
        return self._billing_reconciler().reconcile_daily(actor, day=date)

    def list_billing_recon_runs(
        self, actor: Actor, limit: int = 20
    ) -> dict[str, Any]:
        rows = self.repository.list_recon_runs(
            actor.workspace_id, actor.id, limit=limit
        )
        return {"runs": rows, "total": len(rows)}

    def _remove_asset_files(self, relative_paths: list[str]) -> tuple[int, int]:
        """Best-effort removal of stored asset files; returns (removed_count, freed_bytes).

        Removal always goes through :meth:`PodAssetStore.remove` (path guard +
        empty-parent cleanup). A failed removal must never abort the sweep —
        whatever is left gets re-collected on the next pass.
        """

        import logging

        logger = logging.getLogger(__name__)
        removed = 0
        freed = 0
        for relative_path in relative_paths:
            size = 0
            try:
                size = self.assets.path(relative_path).stat().st_size
            except (FileNotFoundError, OSError, ValueError):
                size = 0
            try:
                self.assets.remove(relative_path)
            except Exception:
                logger.warning(
                    "POD stale-cache sweeper failed to remove %s", relative_path
                )
                continue
            removed += 1
            freed += size
        return removed, freed

    def reap_stale_local_cache_once(self, *, older_than_hours: int = 48) -> list[str]:
        """Clear local image cache older than the given window and return removed paths."""
        relative_paths = self.repository.reap_stale_local_cache(older_than_hours=older_than_hours)
        self._remove_asset_files(relative_paths)
        return relative_paths

    def preview_local_cache(self, *, older_than_hours: int = 48) -> dict[str, Any]:
        """扫描本机图片缓存（只读）：按「可勾选分类」返回可释放的文件数与字节数。

        行级分类在回滚事务里试算（与执行共用同一段逻辑，见 repository 的
        ``preview_selected_categories``）；磁盘孤儿只扫描、不删除。
        """
        path_map = self.repository.preview_selected_categories(older_than_hours=older_than_hours)
        path_map[self.repository.SWEEP_CATEGORY_DISK_ORPHANS] = self._scan_disk_orphans(
            older_than_hours=older_than_hours
        )
        categories: list[dict[str, Any]] = []
        total_files = 0
        total_bytes = 0
        for key in self.repository.SWEEP_CATEGORY_KEYS:
            files, freed = self._measure_asset_files(path_map.get(key, []))
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
        """手动缓存清扫（支持按分类勾选；见 docs/POD-本地图片缓存与清理说明.md §8）。

        ``categories=None`` 表示全选（旧「一键清理」语义，兼容旧调用）；显式空列表
        表示什么都不清。全程只碰本地缓存与资产行，不碰模板、业务数据与图床对象：

        1. 行级分类（``published_release`` / ``terminal_intermediates`` /
           ``unreferenced_residue``）：已发布图床的旧结果释放本地指针、终态批次的
           中间图解引用、超窗且无任何表引用的资产行删除——选中的分类在单事务内按序执行；
        2. 磁盘对账（``disk_orphans``）：磁盘上「不在 ``pod_customization_assets``
           ``.relative_path`` 集合里且 mtime 超窗」的文件（跳过写入中的临时文件）。
           这类文件没有任何资产行引用，行级清扫永远回收不到，必须反向扫描；
        3. 释放量按删除前实际文件大小累加（文件缺失计 0，不中断）。
        """

        selected: tuple[str, ...] = (
            self.repository.SWEEP_CATEGORY_KEYS
            if categories is None
            else tuple(dict.fromkeys(categories))
        )
        unknown = [key for key in selected if key not in self.repository.SWEEP_CATEGORY_KEYS]
        if unknown:
            raise PodRepositoryError(f"未知的缓存清扫分类: {', '.join(unknown)}", 400)

        per_category: dict[str, dict[str, int]] = {
            key: {"files": 0, "bytes": 0} for key in self.repository.SWEEP_CATEGORY_KEYS
        }

        row_selected = [key for key in self.repository.SWEEP_ROW_CATEGORIES if key in set(selected)]
        if row_selected:
            freed_paths = self.repository.sweep_selected_categories(
                categories=row_selected, older_than_hours=older_than_hours
            )
            for key in row_selected:
                files, freed = self._remove_asset_files(freed_paths.get(key, []))
                per_category[key] = {"files": files, "bytes": freed}

        disk_key = self.repository.SWEEP_CATEGORY_DISK_ORPHANS
        if disk_key in set(selected):
            orphan_paths = self._scan_disk_orphans(older_than_hours=older_than_hours)
            files, freed = self._remove_asset_files(orphan_paths)
            per_category[disk_key] = {"files": files, "bytes": freed}

        total_files = sum(item["files"] for item in per_category.values())
        total_bytes = sum(item["bytes"] for item in per_category.values())
        orphan_files = per_category[disk_key]["files"]

        result = {
            "older_than_hours": int(older_than_hours),
            "categories": per_category,
            "files_removed": total_files,
            "bytes_freed": total_bytes,
            "reaped_assets": total_files - orphan_files,
            "orphan_files_removed": orphan_files,
        }
        import logging

        logging.getLogger(__name__).info(
            "POD manual cache sweep: files=%d bytes=%d reaped_assets=%d orphans=%d window_hours=%d categories=%s",
            result["files_removed"],
            result["bytes_freed"],
            result["reaped_assets"],
            result["orphan_files_removed"],
            result["older_than_hours"],
            ",".join(selected),
        )
        return result

    def _scan_disk_orphans(self, *, older_than_hours: int) -> list[str]:
        """磁盘上没有任何资产行引用、且超过保留期的文件（跳过写入中的临时文件）。"""

        window_seconds = max(1, int(older_than_hours)) * 3600
        known_paths = self.repository.list_asset_relative_paths()
        now = time.time()
        return [
            relative_path
            for relative_path, _size, mtime in self.assets.iter_stored_files()
            if relative_path not in known_paths and now - mtime >= window_seconds
        ]

    def _measure_asset_files(self, relative_paths: list[str]) -> tuple[int, int]:
        """按删除口径测量（文件缺失计 0，与 ``_remove_asset_files`` 的记账一致）。"""

        files = 0
        total = 0
        for relative_path in relative_paths:
            files += 1
            try:
                total += self.assets.path(relative_path).stat().st_size
            except (FileNotFoundError, OSError, ValueError):
                pass
        return files, total

    def reap_stuck_batches_once(self) -> list[dict]:
        """Reap batches that have not progressed within the inactivity window.

        Exposed as a public method for deterministic tests and operator diagnostics.
        Returns the list of reaped batch records (batch_id, old_epoch, new_status).
        """
        import logging
        logger = logging.getLogger(__name__)
        reaped = self.repository.reap_stuck_batches(
            stale_after_seconds=POD_PROGRESS_TIMEOUT_SECONDS,
        )
        for record in reaped:
            logger.info(
                "POD reaper: batch %s reaped (old_epoch=%s, new_status=%s, reason=inactivity_timeout)",
                record["batch_id"],
                record["old_epoch"],
                record["new_status"],
            )
        return reaped

    def recover_interrupted_work(self) -> int:
        recovered = self.repository.recover_interrupted_batches()
        self.repository.recover_billing_runs()
        return recovered

    def list_pending_billing_runs(self, actor: Actor) -> dict[str, Any]:
        rows = self.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
        return {"runs": [self._billing_run_payload(row) for row in rows], "total": len(rows)}

    def resume_billing_run(
        self, actor: Actor, run_id: str, *, enqueue: bool = False
    ) -> dict[str, Any]:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        stored = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        if stored["status"] == "settled":
            return self._billing_run_payload(stored)
        if not self.repository.claim_billing_resume(run_id, actor.workspace_id, actor.id):
            current = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            if current["status"] in {"settled", "resume_claimed", "authorized", "settling"}:
                return self._billing_run_payload(current)
            raise PodRepositoryError("POD billing run is already active", 409)
        stored = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        plan = self._billing_plan(stored["plan"])
        has_planned_calls = any(
            outcome["status"] == "planned" for outcome in stored["outcomes"]
        )
        has_uncertain_calls = any(
            outcome["status"] == "started" for outcome in stored["outcomes"]
        )
        if has_uncertain_calls:
            message = (
                "POD provider call outcome is uncertain after interruption; "
                "automatic resume and settlement are blocked"
            )
            self.repository.mark_billing_pending(stored["action_key"], message)
            raise PodRepositoryError(message, 409)
        settlement_grant = getattr(self.billing_coordinator, "settlement_grant", None)
        try:
            if not has_planned_calls and callable(settlement_grant):
                grant = settlement_grant(
                    actor,
                    stored["freeze_id"],
                    rule_version=stored["rule_version"],
                    expires_at=stored["grant_expires_at"],
                )
            else:
                grant = self.billing_coordinator.regrant(actor, stored["freeze_id"])
        except CustomerBillingPermissionError:
            self.repository.mark_billing_pending(
                stored["action_key"], "POD billing service authentication failed"
            )
            raise
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if grant.freeze_id != stored["freeze_id"]:
            raise RuntimeError("POD billing service returned a mismatched freeze")
        self.repository.mark_billing_authorized(
            stored["action_key"], rule_version=grant.rule_version, expires_at=grant.expires_at
        )
        run = PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=stored["action_key"],
            resumed=True,
        )
        if stored["action_type"] == "batch_initial" and has_planned_calls:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            if enqueue:
                self.worker.submit(stored["batch_id"], run)
            else:
                self.worker.process_batch(stored["batch_id"], run)
            return self._billing_run_payload(
                self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            )
        if has_planned_calls and stored["action_type"] == "direct_trial":
            finalized = [
                outcome for outcome in stored["outcomes"] if outcome["status"] != "planned"
            ]
            if finalized:
                message = "POD direct trial has partial provider outcomes; automatic replay is blocked"
                self.repository.mark_billing_pending(stored["action_key"], message)
                raise PodRepositoryError(message, 409)
            def continue_direct_trial() -> None:
                request = DirectListingTrialCreate.model_validate(stored["action_payload"])
                template = self.repository.get_template(
                    request.template_id, actor.workspace_id, actor.id
                )
                template_asset = self.repository.get_asset(
                    template["asset_id"], actor.workspace_id, actor.id
                )
                try:
                    self._run_direct_listing_trial_authorized(
                        actor,
                        request,
                        template_image=self.assets.read(template_asset["relative_path"]),
                        template_content_type=template_asset["content_type"],
                        trial_id=stored["target_id"],
                        base_prompt=build_direct_listing_prompt(
                            request.business_fields, request.creative_prompt
                        ),
                        trial_elements=assign_style_elements(
                            request.business_fields.style_keywords, 1, stored["target_id"]
                        ),
                        billing_run=run,
                    )
                except PodBillingAuthorizationRequired as exc:
                    self.repository.mark_billing_pending(run.action_key, str(exc))
                    return
                try:
                    run.settle()
                except Exception as exc:
                    self.repository.mark_billing_pending(run.action_key, str(exc))

            if enqueue:
                if self.worker is None:
                    raise RuntimeError("POD worker is disabled")
                self.worker.submit_billing_action(run_id, continue_direct_trial)
            else:
                continue_direct_trial()
            return self._billing_run_payload(
                self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            )
        if has_planned_calls and stored["action_type"] in {
            "scene_optimization",
            "item_retry",
            "style_retry",
            "title_retry",
        }:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            payload = stored["action_payload"]
            if stored["action_type"] == "style_retry" and payload.get("retry_mode") == "batch":
                image_style_indices = tuple(int(index) for index in payload.get("image_style_indices", []))
                title_style_indices = tuple(int(index) for index in payload.get("title_style_indices", []))
                function = (
                    self.worker.submit_batch_retry
                    if enqueue
                    else self.worker.process_batch_retry
                )
                function(stored["batch_id"], image_style_indices, title_style_indices, run)
            elif stored["action_type"] == "scene_optimization":
                function = (
                    self.worker.submit_scene_optimization
                    if enqueue
                    else self.worker.optimize_scene
                )
                function(stored["batch_id"], stored["target_id"], str(payload.get("instruction") or ""), run)
            elif stored["action_type"] == "item_retry":
                function = (
                    self.worker.submit_item_regeneration
                    if enqueue
                    else self.worker.regenerate_item
                )
                function(stored["batch_id"], stored["target_id"], str(payload.get("creative_prompt") or ""), run)
            elif stored["action_type"] == "style_retry":
                function = (
                    self.worker.submit_style_regeneration
                    if enqueue
                    else self.worker.regenerate_style
                )
                function(stored["batch_id"], int(stored["target_id"]), str(payload.get("creative_prompt") or ""), run)
            else:
                function = (
                    self.worker.submit_title_regeneration
                    if enqueue
                    else self.worker.regenerate_title
                )
                function(stored["batch_id"], int(stored["target_id"]), run)
            return self._billing_run_payload(
                self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            )
        try:
            run.settle()
        except PodBillingAuthorizationRequired:
            raise
        except Exception as exc:
            raise
        if stored["batch_id"]:
            refreshed = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            result_status = refreshed["result_status"]
            if result_status and result_status not in {"settlement_pending"}:
                self.repository.set_batch_status(stored["batch_id"], result_status)
        return self._billing_run_payload(
            self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        )

    def cancel_billing_run(self, actor: Actor, run_id: str) -> dict[str, Any]:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        stored = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        if stored["status"] != "settlement_pending":
            raise PodRepositoryError("POD billing run is not cancellable", 409)
        plan = self._billing_plan(stored["plan"])
        has_planned_calls = any(
            outcome["status"] == "planned" for outcome in stored["outcomes"]
        )
        settlement_grant = getattr(self.billing_coordinator, "settlement_grant", None)
        try:
            if not has_planned_calls and callable(settlement_grant):
                grant = settlement_grant(
                    actor,
                    stored["freeze_id"],
                    rule_version=stored["rule_version"],
                    expires_at=stored["grant_expires_at"],
                )
            else:
                grant = self.billing_coordinator.regrant(actor, stored["freeze_id"])
        except CustomerBillingPermissionError:
            self.repository.mark_billing_pending(
                stored["action_key"], "POD billing service authentication failed"
            )
            raise
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if grant.freeze_id != stored["freeze_id"]:
            raise RuntimeError("POD billing service returned a mismatched freeze")
        self.repository.mark_billing_authorized(
            stored["action_key"], rule_version=grant.rule_version, expires_at=grant.expires_at
        )
        run = PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=stored["action_key"],
            resumed=True,
        )
        try:
            run.settle()
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if stored["action_type"] == "direct_trial":
            self.repository.fail_direct_listing_trial(
                stored["target_id"],
                actor.workspace_id,
                actor.id,
                "POD 试用已取消，冻结积分将按结算结果释放",
            )
        return self._billing_run_payload(
            self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        )


    def _settle_stored_billing_run(self, stored: dict[str, Any]) -> None:
        """Rebuild a persisted run and settle it, releasing unearned frozen points.

        Only runs whose outcomes are all terminal (no ``started``) can be settled
        safely.  An interrupted provider call with an unknown outcome is left
        ``settlement_pending`` for manual review instead of being guessed.
        """
        if any(outcome["status"] == "started" for outcome in stored["outcomes"]):
            raise PodRepositoryError(
                "POD provider call outcome is uncertain after interruption; automatic settle blocked", 409
            )
        actor = Actor(
            id=str(stored["owner_user_id"]),
            username="",
            role="",
            workspace_id=str(stored["workspace_id"]),
        )
        plan = self._billing_plan(stored["plan"])
        grant = self._settlement_grant(actor, stored)
        run = PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=stored["action_key"],
            resumed=True,
        )
        run.settle()

    def _settlement_grant(self, actor: Actor, stored: dict[str, Any]) -> PodExecutionGrant:
        """取回可结算的 grant（v2 探活 / v1 重冻结），并把本地状态推进到 authorized。"""
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        settlement_grant = getattr(self.billing_coordinator, "settlement_grant", None)
        try:
            if callable(settlement_grant):
                grant = settlement_grant(
                    actor,
                    stored["freeze_id"],
                    rule_version=stored["rule_version"],
                    expires_at=stored["grant_expires_at"],
                )
            else:
                grant = self.billing_coordinator.regrant(actor, stored["freeze_id"])
        except CustomerBillingPermissionError:
            self.repository.mark_billing_pending(
                stored["action_key"], "POD billing service authentication failed"
            )
            raise
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if grant.freeze_id != stored["freeze_id"]:
            raise RuntimeError("POD billing service returned a mismatched freeze")
        self.repository.mark_billing_authorized(
            stored["action_key"], rule_version=grant.rule_version, expires_at=grant.expires_at
        )
        return grant

    def close_billing_run(self, actor: Actor, run_id: str) -> dict[str, Any]:
        """用户主动放弃：v2 走 ``POST /close``；v1 降级为「全 no_return 结算」。

        已成功的调用照常计费，未完成（planned / started）的按 ``no_return`` 上报；
        服务端回执里的金额原样落库，关单原因记 ``no_return_close``。
        """
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        stored = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        if stored["status"] in self.repository.BILLING_TERMINAL_STATUSES:
            return self._billing_run_payload(stored)
        plan = self._billing_plan(stored["plan"])
        outcomes = self.repository.prepare_billing_close(stored["action_key"])
        grant = self._settlement_grant(actor, stored)
        closer = getattr(self.billing_coordinator, "close", None)
        try:
            if callable(closer):
                receipt = closer(actor, grant, plan, outcomes)
            else:
                # v1 兼容：没有 close 入口时按既有语义全额退还（全 no_return 结算）。
                no_return = tuple(
                    PodCallOutcome(call.call_id, call.feature, "no_return")
                    for call in plan.calls
                )
                receipt = self.billing_coordinator.settle(actor, grant, plan, no_return)
        except Exception as exc:
            from .remote_billing import classify_billing_exception

            code, retryable, status_code = classify_billing_exception(exc)
            if retryable:
                self.repository.mark_billing_pending(stored["action_key"], str(exc))
            elif code == "freeze_not_active":
                self.repository.terminate_billing_run(
                    stored["action_key"], "expired_released", str(exc)
                )
            elif status_code == 404:
                self.repository.terminate_billing_run(
                    stored["action_key"], "orphaned", str(exc)
                )
            else:
                self.repository.terminate_billing_run(
                    stored["action_key"], "anomaly", str(exc)
                )
            raise
        if isinstance(receipt, dict):
            self.repository.insert_settlement(
                freeze_id=grant.freeze_id,
                source="close",
                charged_points=receipt.get("charged_points") or 0,
                refunded_points=receipt.get("refunded_points") or 0,
                release_reason="no_return_close",
                rule_version=str(receipt.get("rule_version") or ""),
                raw_response=receipt.get("raw") or receipt,
                settlement_id=str(receipt.get("settlement_id") or ""),
                items=[
                    {
                        "link_idx": int(link.get("link_idx") or 0),
                        "unit_id": str(link.get("unit_id") or ""),
                        "feature_status": link.get("feature_status") or {},
                        "charged_points": link.get("charged_points") or 0,
                        "refunded_points": link.get("refunded_points") or 0,
                    }
                    for link in receipt.get("links") or ()
                    if isinstance(link, dict)
                ],
            )
        self.repository.mark_billing_settled(
            stored["action_key"], release_reason="no_return_close"
        )
        return self._billing_run_payload(
            self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        )

    @staticmethod
    def _deadline_passed(value: str) -> bool:
        return deadline_passed(value)

    def _billing_deadline_passed(self, stored: dict[str, Any]) -> bool:
        """「还能不能结算」只认 settle_deadline；v1 没有该字段时回退 expires_at，不加宽限。"""
        deadline = str(stored.get("settle_deadline") or "")
        if deadline:
            return self._deadline_passed(deadline)
        return self._deadline_passed(str(stored.get("grant_expires_at") or ""))

    def _freeze_is_still_frozen(self, stored: dict[str, Any]) -> bool:
        """到期后先探活：只有服务端确实还是 frozen 才收口 ``started``。"""
        if self.billing_coordinator is None or not stored.get("freeze_id"):
            return False
        prober = getattr(self.billing_coordinator, "freeze_status", None)
        if not callable(prober):
            return False
        actor = Actor(
            id=str(stored["owner_user_id"]),
            username="",
            role="",
            workspace_id=str(stored["workspace_id"]),
        )
        try:
            probe = prober(actor, str(stored["freeze_id"]))
        except Exception:
            return False
        return str(probe.get("status") or "") == "frozen"

    def settle_stuck_billing_runs(self) -> int:
        """按 ``settle_deadline`` 收口 abandoned ``settlement_pending`` runs。

        未到期不动；到期后若仍有 ``started``（结果未知）的调用，先探活，只有服务端
        确实还是 ``frozen`` 才把它们改写为 ``no_return`` 并结算。
        """
        if self.billing_coordinator is None:
            return 0
        settled = 0
        for run_id, workspace_id, owner_user_id in self.repository.list_settlement_pending_runs():
            try:
                stored = self.repository.get_billing_run(run_id, workspace_id, owner_user_id)
                if not self._billing_deadline_passed(stored):
                    continue
                if any(outcome["status"] == "started" for outcome in stored["outcomes"]):
                    if not self._freeze_is_still_frozen(stored):
                        continue
                    self.repository.release_started_outcomes(stored["action_key"])
                    stored = self.repository.get_billing_run(run_id, workspace_id, owner_user_id)
                self._settle_stored_billing_run(stored)
                settled += 1
            except Exception:
                # 可重试错误或探活失败：留给下一轮 sweep / 启动对账。
                continue
        return settled

    def asset_info(self, actor: Actor, asset_id: str) -> dict[str, Any]:
        return self.repository.get_asset(asset_id, actor.workspace_id, actor.id)

    def asset_path(self, actor: Actor, asset_id: str) -> Path:
        return self.assets.path(self.asset_info(actor, asset_id)["relative_path"])

    def _save_direct_trial_asset(
        self, actor: Actor, kind: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        stored = self.assets.save_image(actor.workspace_id, actor.id, content)
        return self.repository.create_asset(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            kind=kind,
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )

    def _save_direct_trial_grid_attempts(
        self, actor: Actor, trial_id: str, grids: list[Any]
    ) -> list[str]:
        return [
            self._save_direct_trial_asset(
                actor,
                "direct_listing_grid",
                f"direct-listing-{trial_id}-attempt-{attempt}.png",
                grid.content,
            )["asset_id"]
            for attempt, grid in enumerate(grids, start=1)
        ]

    def _require_title_runtime_configured(self, *, require_present: bool = False) -> None:
        if require_present and self.title_runtime is None:
            raise RuntimeError("POD 标题服务未启用")

    def _freeze_batch(
        self,
        actor: Actor,
        batch_id: str,
        style_count: int,
        *,
        billing_profile: str = POD_BILLING_PROFILE_RANDOM,
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_batch(
            batch_id, style_count=style_count, billing_profile=billing_profile
        )
        return self._freeze_action(
            actor, plan, action_type="batch_initial", target_id=batch_id, batch_id=batch_id
        )

    def _freeze_fission_batch(self, actor: Actor, batch_id: str, style_count: int) -> PodBillingRun:
        """裂变批次计费：每款 4 面板 × 2 次图片调用 + 标题，按款计 link。"""
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_fission_batch(batch_id, style_count=style_count)
        return self._freeze_action(
            actor, plan, action_type="batch_initial", target_id=batch_id, batch_id=batch_id
        )

    def _freeze_column_batch(self, actor: Actor, batch_id: str, style_count: int) -> PodBillingRun:
        """列式生成初始批次：按「二级批次（4 款）」冻结，1 组 = 1 link。"""
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_column_batch(batch_id, style_count=style_count)
        return self._freeze_action(
            actor, plan, action_type="batch_initial", target_id=batch_id, batch_id=batch_id
        )

    def _freeze_replica_batch(
        self, actor: Actor, batch_id: str, style_count: int, generation_strategy: str
    ) -> PodBillingRun:
        """复刻批次计费：按生成策略冻结每款图片调用形状（grid 2 次 / 独立面板 8 次）。"""
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_replica_batch(
            batch_id, style_count=style_count, generation_strategy=generation_strategy
        )
        return self._freeze_action(
            actor, plan, action_type="batch_initial", target_id=batch_id, batch_id=batch_id
        )

    @staticmethod
    def _pod_billing_profile(batch: dict[str, Any]) -> str:
        """按批次模式取计费画像（普通形态）：复刻统一上报 pod_replica_v1（供远端分类），
        全定制沿用 pod_random_v1。半定制走独立的 ``for_semi_*`` 计划、复刻四图直出
        走独立的 ``for_replica_panel*`` 计划，都不使用本函数。
        """
        if str(batch.get("mode") or "") == "fission":
            return POD_BILLING_PROFILE_FISSION
        if str(batch.get("mode") or "") == "replica":
            return POD_BILLING_PROFILE_REPLICA
        return POD_BILLING_PROFILE_RANDOM

    def _batch_panel_generation_strategy(self, batch_id: str) -> str:
        """从批次快照读取生图策略：裂变/复刻返回独立面板，其余 grid。"""
        try:
            batch = self.repository.get_batch_internal(batch_id)
        except Exception:  # noqa: BLE001
            return "grid"
        mode = str(batch.get("mode") or "")
        if mode == "fission":
            return str(batch.get("fission_generation_strategy") or "grid")
        if mode != "replica":
            return "grid"
        for target in batch.get("replica_targets") or []:
            if str(target.get("generation_strategy") or "") == "independent_panels":
                return "independent_panels"
        return "grid"

    @staticmethod
    def _require_fission_panel_batch(batch: dict[str, Any]) -> None:
        if str(batch.get("generation_strategy") or "grid") != "independent_panels":
            raise PodRepositoryError("历史裂变批次只读：不支持重试或重新生成", 409)

    def _freeze_semi_batch(self, actor: Actor, batch_id: str, count: int) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_semi_batch(batch_id, count=count)
        return self._freeze_action(
            actor, plan, action_type="batch_initial", target_id=batch_id, batch_id=batch_id
        )

    def _freeze_paused_batch_remainder(
        self, actor: Actor, batch: dict[str, Any]
    ) -> PodBillingRun:
        if str(batch.get("generation_layout") or "") == GENERATION_LAYOUT_COLUMN:
            return self._freeze_column_batch_remainder(actor, batch)
        batch_id = str(batch["batch_id"])
        completed_images: dict[int, int] = {}
        for item in batch.get("items", []):
            if item.get("status") == "completed":
                style_index = int(item.get("style_index") or 0)
                completed_images[style_index] = completed_images.get(style_index, 0) + 1
        title_statuses = {
            int(title["style_index"]): str(title.get("status") or "")
            for title in batch.get("style_titles", [])
        }
        image_indices = tuple(
            style_index
            for style_index in range(1, int(batch["requested_count"]) + 1)
            if completed_images.get(style_index, 0) < 4
        )
        title_indices = tuple(
            style_index
            for style_index in range(1, int(batch["requested_count"]) + 1)
            if completed_images.get(style_index, 0) == 4
            and self.title_runtime is not None
            and batch.get("mode") != "semi"
            and title_statuses.get(style_index) != "completed"
        )
        if not image_indices and not title_indices:
            raise PodRepositoryError("POD 批次没有待继续的款式", 409)
        if batch.get("mode") == "semi":
            # 半定制按「款」计费：本次续跑冻结 link_count = 剩余组数 × 4。
            plan = PodCallPlan.for_semi_batch_resume(
                batch_id,
                uuid.uuid4().hex,
                image_style_indices=image_indices,
            )
        else:
            mode = str(batch.get("mode") or "")
            panel_strategy = self._batch_panel_generation_strategy(batch_id) == "independent_panels"
            if mode == "fission":
                if panel_strategy:
                    plan = PodCallPlan.for_fission_batch_resume(
                        batch_id,
                        uuid.uuid4().hex,
                        image_style_indices=image_indices,
                        title_style_indices=title_indices,
                        include_title=self.title_runtime is not None,
                    )
                else:
                    raise PodRepositoryError("历史裂变批次只读：不支持续跑", 409)
            elif panel_strategy:
                plan = PodCallPlan.for_replica_batch_resume(
                    batch_id,
                    uuid.uuid4().hex,
                    image_style_indices=image_indices,
                    title_style_indices=title_indices,
                    include_title=self.title_runtime is not None,
                )
            else:
                plan = PodCallPlan.for_batch_resume(
                    batch_id,
                    uuid.uuid4().hex,
                    image_style_indices=image_indices,
                    title_style_indices=title_indices,
                    include_title=self.title_runtime is not None,
                    billing_profile=self._pod_billing_profile(batch),
                )
        return self._freeze_action(
            actor,
            plan,
            action_type="batch_initial",
            target_id=batch_id,
            batch_id=batch_id,
            action_payload={
                "resume_image_style_indices": list(image_indices),
                "resume_title_style_indices": list(title_indices),
            },
        )

    def _freeze_column_batch_remainder(
        self, actor: Actor, batch: dict[str, Any]
    ) -> PodBillingRun:
        """列式批次的暂停续跑：按「组 × 缺失角色」冻结，已完成组不再产生调用。

        主图未完成的组必须把 3 个派生轮一并冻结：主图跑完后派生轮会在同一次续跑里
        立即发起（见 ``group_roles_to_freeze``）。
        """
        batch_id = str(batch["batch_id"])
        sheets_by_group: dict[str, dict[str, str]] = {}
        for sheet in batch.get("group_sheets") or []:
            sheets_by_group.setdefault(str(sheet["group_id"]), {})[
                str(sheet["role"])
            ] = str(sheet["status"])
        group_roles: dict[int, list[str]] = {}
        for group in batch.get("style_groups") or []:
            roles = group_roles_to_freeze(
                sheets_by_group.get(str(group["group_id"]), {})
            )
            if roles:
                group_roles[int(group["group_index"])] = list(roles)
        primary_done = {
            int(item["style_index"])
            for item in batch.get("items") or []
            if int(item.get("variant_index") or 0) == 1
            and str(item.get("status") or "") == "completed"
        }
        title_status = {
            int(title["style_index"]): str(title.get("status") or "")
            for title in batch.get("style_titles") or []
        }
        title_indices = tuple(
            style_index
            for style_index in range(1, int(batch["requested_count"]) + 1)
            if style_index in primary_done
            and self.title_runtime is not None
            and batch.get("mode") != "semi"
            # 只补「排队/中断」的标题；已失败的标题不自动重跑（v1 不做用户重跑）。
            and title_status.get(style_index, "queued") in {"queued", "generating", ""}
        )
        if not group_roles and not title_indices:
            raise PodRepositoryError("POD 批次没有待继续的款式", 409)
        plan = PodCallPlan.for_column_batch_resume(
            batch_id,
            uuid.uuid4().hex,
            group_roles=group_roles,
            title_style_indices=title_indices,
        )
        return self._freeze_action(
            actor,
            plan,
            action_type="batch_initial",
            target_id=batch_id,
            batch_id=batch_id,
            action_payload={
                "resume_column_group_roles": {
                    str(group): list(roles) for group, roles in group_roles.items()
                },
                "resume_title_style_indices": list(title_indices),
            },
        )

    def _freeze_trial(
        self, actor: Actor, trial_id: str, request: DirectListingTrialCreate
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_trial(trial_id, include_title=self.title_runtime is not None)
        return self._freeze_action(
            actor,
            plan,
            action_type="direct_trial",
            target_id=trial_id,
            batch_id="",
            action_payload=request.model_dump(mode="json"),
        )

    def _freeze_brief(self, actor: Actor, brief_id: str) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_brief(brief_id)
        # 不落库用户的模糊输入：方案 D4 明确生成内容不做后端持久化，这里只留模式标记。
        return self._freeze_action(
            actor,
            plan,
            action_type="title_retry",
            target_id=brief_id,
            batch_id="",
            action_payload={"mode": "brief"},
        )

    def _freeze_composition(self, actor: Actor, composition_id: str) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_composition(composition_id)
        # 与智能填写同口径：纯 pod.title scope 零计费，只留模式标记。
        return self._freeze_action(
            actor,
            plan,
            action_type="title_retry",
            target_id=composition_id,
            batch_id="",
            action_payload={"mode": "composition"},
        )

    def _freeze_replica_analysis(self, actor: Actor, analysis_id: str) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_replica_analysis(analysis_id)
        # 与 brief/composition 同口径：纯 pod.title scope 零计费，只留模式标记。
        return self._freeze_action(
            actor,
            plan,
            action_type="title_retry",
            target_id=analysis_id,
            batch_id="",
            action_payload={"mode": "replica_analysis"},
        )

    def _freeze_structure_analysis(self, actor: Actor, analysis_id: str) -> PodBillingRun:
        """模板结构识图：与复刻识图同口径（纯 pod.title 零计费）。"""
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_structure_analysis(analysis_id)
        return self._freeze_action(
            actor,
            plan,
            action_type="title_retry",
            target_id=analysis_id,
            batch_id="",
            action_payload={"mode": "structure_analysis"},
        )

    def _freeze_fission_analysis(self, actor: Actor, analysis_id: str) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_fission_analysis(analysis_id)
        return self._freeze_action(
            actor,
            plan,
            action_type="title_retry",
            target_id=analysis_id,
            batch_id="",
            action_payload={"mode": "fission_analysis"},
        )

    def _freeze_style_retry(
        self,
        actor: Actor,
        action_id: str,
        batch_id: str,
        style_index: int,
        creative_prompt: str,
        *,
        semi: bool = False,
        billing_profile: str = POD_BILLING_PROFILE_RANDOM,
        replica_generation_strategy: str = "grid",
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        if semi:
            plan = PodCallPlan.for_semi_style_retry(action_id)
        elif replica_generation_strategy == "independent_panels":
            if billing_profile == POD_BILLING_PROFILE_FISSION:
                plan = PodCallPlan.for_fission_style_retry(
                    action_id, include_title=self.title_runtime is not None
                )
            else:
                plan = PodCallPlan.for_replica_style_retry(
                    action_id, include_title=self.title_runtime is not None
                )
        else:
            plan = PodCallPlan.for_style_retry(
                action_id,
                include_title=self.title_runtime is not None,
                billing_profile=billing_profile,
            )
        return self._freeze_action(
            actor,
            plan,
            action_type="style_retry",
            target_id=str(style_index),
            batch_id=batch_id,
            action_payload={"creative_prompt": creative_prompt},
        )

    def _freeze_batch_retry(
        self,
        actor: Actor,
        action_id: str,
        batch_id: str,
        image_style_indices: tuple[int, ...],
        title_style_indices: tuple[int, ...],
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        mode = str(batch.get("mode") or "")
        panel_strategy = self._batch_panel_generation_strategy(batch_id) == "independent_panels"
        if mode == "fission":
            if not panel_strategy:
                raise PodRepositoryError("历史裂变批次只读：不支持重试", 409)
            plan = PodCallPlan.for_fission_batch_retry(
                action_id,
                image_style_indices=image_style_indices,
                title_style_indices=title_style_indices,
                include_title=self.title_runtime is not None,
            )
        elif panel_strategy:
            plan = PodCallPlan.for_replica_batch_retry(
                action_id,
                image_style_indices=image_style_indices,
                title_style_indices=title_style_indices,
                include_title=self.title_runtime is not None,
            )
        else:
            plan = PodCallPlan.for_batch_retry(
                action_id,
                image_style_indices=image_style_indices,
                title_style_indices=title_style_indices,
                include_title=self.title_runtime is not None,
                billing_profile=self._pod_billing_profile(batch),
            )
        # The existing durable schema restricts action_type values. The payload
        # distinguishes this single-run batch retry without a table migration.
        return self._freeze_action(
            actor,
            plan,
            action_type="style_retry",
            target_id="batch_retry",
            batch_id=batch_id,
            action_payload={
                "retry_mode": "batch",
                "image_style_indices": list(image_style_indices),
                "title_style_indices": list(title_style_indices),
            },
        )

    def _freeze_retry(
        self,
        actor: Actor,
        action_id: str,
        feature: str,
        *,
        action_type: str,
        target_id: str,
        batch_id: str,
        action_payload: dict[str, Any] | None = None,
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_retry(
            action_id,
            feature=feature,  # type: ignore[arg-type]
            max_attempts=TITLE_ATTEMPTS if feature == "pod.title" else 1,
        )
        return self._freeze_action(
            actor,
            plan,
            action_type=action_type,
            target_id=target_id,
            batch_id=batch_id,
            action_payload=action_payload,
        )

    def _freeze_action(
        self,
        actor: Actor,
        plan: PodCallPlan,
        *,
        action_type: str,
        target_id: str,
        batch_id: str,
        action_payload: dict[str, Any] | None = None,
    ) -> PodBillingRun:
        """先写意图账，再冻结；冻结或回填失败时意图行保留给重放接管。

        旧实现是「先冻结、后记账」：回包丢失或落库失败时，服务端已有冻结、本地
        却没有单号，只能等过期。现在顺序倒过来，``action_key`` 在调服务端之前
        就已经落库，崩溃后用同一个键重放即可接管同一笔冻结。
        """
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        stored = self.repository.find_billing_run_by_action_key(plan.idempotency_key)
        if stored is None:
            try:
                stored = self.repository.create_billing_intent(
                    action_key=plan.idempotency_key,
                    action_type=action_type,
                    target_id=target_id,
                    batch_id=batch_id,
                    actor_id=actor.id,
                    workspace_id=actor.workspace_id,
                    plan=plan,
                    action_payload=action_payload,
                    module=plan.module,
                    unit_kind=plan.unit_kind,
                    link_count=len(plan.unit_ids),
                    item_count=self._plan_item_count(plan),
                    device_id=self._device_id(),
                )
            except sqlite3.IntegrityError:
                # 并发重放：另一条路径已经写了同一个 action_key，复用那一行。
                stored = self.repository.find_billing_run_by_action_key(plan.idempotency_key)
                if stored is None:
                    raise
        if stored["status"] != "intent":
            raise PodRepositoryError("POD billing action is already frozen", 409)
        try:
            grant = self.billing_coordinator.freeze(actor, plan)
        except Exception as exc:
            # 4xx / 5xx / 网络 / 契约破坏都保留 intent：用户重发或启动对账会用同一键重放。
            self.repository.mark_billing_intent_failed(
                plan.idempotency_key, safe_error_message(exc)
            )
            raise
        self._attach_freeze(plan, grant)
        return PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=plan.idempotency_key,
        )

    def _attach_freeze(self, plan: PodCallPlan, grant: PodExecutionGrant) -> None:
        """把冻结回执一次性回填到意图行（CAS intent → authorized）。"""
        try:
            attached = attach_freeze_snapshot(
                self.repository,
                plan.idempotency_key,
                plan,
                grant,
                device_id=self._device_id(),
            )
        except Exception as exc:
            # 回填失败：保留 intent，由对账重放接管；不再执行「全部 no_return 结算」
            # 补偿（那条快路径会在服务端已冻结但本地无快照时把钱直接退掉）。
            self.repository.mark_billing_intent_failed(
                plan.idempotency_key, safe_error_message(exc)
            )
            raise
        if not attached:
            # 已被并发路径或对账回填：不覆盖既有快照。
            return

    @staticmethod
    def _plan_item_count(plan: PodCallPlan) -> int:
        return int(plan.semi_item_count or plan.column_style_count or 0)

    def _device_id(self) -> str:
        """本机标识：随数据目录持久化，重装后可复用（多设备对账用）。

        device_id 的来源仍属产品待定口径（见设计 §9.4）；这里先用数据目录内的
        稳定文件，避免每次启动换一个新设备号。
        """
        path = self.repository.database_path.parent / "pod-device-id"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value
        value = uuid.uuid4().hex
        try:
            path.write_text(value, encoding="utf-8")
        except OSError:
            pass
        return value

    @staticmethod
    def _billing_plan(payload: dict[str, Any]) -> PodCallPlan:
        return PodCallPlan(
            idempotency_key=str(payload["idempotency_key"]),
            calls=tuple(
                PodPlannedCall(str(call["call_id"]), str(call["feature"]))  # type: ignore[arg-type]
                for call in payload["calls"]
            ),
            semi_item_count=int(payload.get("semi_item_count") or 0),
            column_style_count=int(payload.get("column_style_count") or 0),
            billing_profile=str(payload.get("billing_profile") or POD_BILLING_PROFILE_RANDOM),
            module=str(payload.get("module") or ""),
            unit_kind=str(payload.get("unit_kind") or ""),
            unit_ids=tuple(str(value) for value in payload.get("unit_ids") or ()),
        )

    @staticmethod
    def _billing_run_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["run_id"],
            "action_type": row["action_type"],
            "target_id": row["target_id"],
            "batch_id": row["batch_id"],
            "freeze_id": row["freeze_id"],
            "rule_version": row["rule_version"],
            "expires_at": row["grant_expires_at"],
            "status": row["status"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _generate_direct_trial_title(
        self,
        trial_id: str,
        hero: Any,
        business_fields: Any,
        creative_prompt: str,
        billing_run: PodBillingRun,
    ) -> dict[str, Any] | None:
        if self.title_runtime is None:
            return None
        request = PodTitleRequest(
            style_task_id=trial_id,
            style_index=1,
            hero_image=hero.content,
            hero_content_type=hero.content_type,
            business_fields=business_fields,
            creative_prompt=creative_prompt,
            accepted_titles=(),
        )
        call_ids = tuple(call.call_id for call in billing_run.plan.calls if call.feature == "pod.title")
        try:
            title_kwargs = {
                "grant": billing_run.grant,
                "call_id": call_ids[0],
                "call_ids": call_ids,
                "on_outcome": lambda call_id, status: billing_run.record(
                    call_id, "pod.title", status
                ),
            }
            parameters = inspect.signature(self.title_runtime.generate_title).parameters
            if "on_start" in parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            ):
                title_kwargs["on_start"] = lambda call_id: billing_run.start(
                    call_id, "pod.title"
                )
            generated = self.title_runtime.generate_title(request, **title_kwargs)
            if not any(billing_run.has_outcome(call_id) for call_id in call_ids):
                billing_run.record(call_ids[0], "pod.title", "success")
            result = vars(generated)
            result["visual_signature"] = visual_signature(generated)
            return {"style_task_id": trial_id, "status": "completed", "error_message": "", **result}
        except PodBillingAuthorizationRequired:
            raise
        except Exception as exc:
            if not any(billing_run.has_outcome(call_id) for call_id in call_ids):
                billing_run.record(call_ids[0], "pod.title", "no_return")
            return {
                "style_task_id": trial_id,
                "status": "failed",
                "title": "",
                "normalized_title": None,
                "visual_theme": "",
                "motif_keywords": (),
                "color_keywords": (),
                "model": "",
                "prompt_version": "",
                "attempt_count": int(getattr(exc, "attempt_count", 0) or 0),
                "error_message": str(exc),
            }

    @staticmethod
    def _raise_direct_trial_generation_error(exc: Exception) -> None:
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403}:
            raise RuntimeError(
                f"图片服务鉴权失败（HTTP {status_code}）：请检查 POD 图片服务的 API Key 与权限配置；未保存试跑结果。"
            ) from exc
        raise RuntimeError(
            f"POD 图片生成失败：{safe_error_message(exc, fallback=exc.__class__.__name__)}"
        ) from exc

    def _template_payload(self, template: dict[str, Any]) -> dict[str, Any]:
        template_id = template["template_id"]
        views = self.repository.list_template_views(
            template_id, template["workspace_id"], template["owner_user_id"]
        )
        facts = load_structure_facts(template.get("structure_facts_json"))
        return {
            "id": template_id,
            "name": template["name"],
            "source": template["source"],
            # 主图资产 id：智能填写要用它附带模板图生成批级美术指导。
            "asset_id": str(template["asset_id"]),
            "preview_url": f"/api/pod-customization/assets/{template['asset_id']}",
            "original_url": f"/api/pod-customization/assets/{template['asset_id']}",
            "width": template["width"],
            "height": template["height"],
            "error_message": template["error_message"],
            "version": template["version"],
            # 结构视图集与事实表：普通模板为空 / none，前端据此决定是否提示补视图。
            "structure_status": str(template.get("structure_status") or "none"),
            "structure_facts": facts or {},
            "structure_analysis_version": str(template.get("structure_analysis_version") or ""),
            "views": [
                {
                    "role": str(row["role"]),
                    "url": f"/api/pod-customization/assets/{row['asset_id']}",
                    "width": int(row["width"] or 0),
                    "height": int(row["height"] or 0),
                }
                for row in views
            ],
            "created_at": template["created_at"],
            "updated_at": template["updated_at"],
        }

    def _batch_payload(self, batch: dict[str, Any]) -> dict[str, Any]:
        mode = batch.get("mode") or "full"
        semi = mode == "semi"
        replica = mode == "replica"
        snapshot = batch.get("template")
        template_payload = None
        if snapshot:
            template_payload = {
                "id": snapshot["template_id"],
                "name": snapshot["name"],
                "source": snapshot["source"],
                "preview_url": f"/api/pod-customization/assets/{snapshot['asset_id']}",
                "original_url": f"/api/pod-customization/assets/{snapshot['asset_id']}",
                "width": snapshot["width"],
                "height": snapshot["height"],
                "created_at": snapshot["created_at"],
                "updated_at": snapshot["created_at"],
            }
        items = [self._item_payload(item) for item in batch["items"]]
        # 款（张）级计数：completed_count/failed_count 是按「组」统计的（1 组 4 张全部
        # 结算才算 1），半定制对外交付单位是「款」，所以另给一份按款的计数。
        completed_item_count = sum(1 for row in batch["items"] if row.get("status") == "completed")
        failed_item_count = sum(1 for row in batch["items"] if row.get("status") == "failed")
        if semi:
            export_payload: dict[str, Any] = {
                "ready": False,
                "exportable_style_count": 0,
                "selected_exportable_style_count": 0,
                "user_excluded_style_count": 0,
                "skipped_style_count": 0,
                "block_reason": "semi_mode",
            }
        else:
            copies = self.repository.get_style_copies(
                batch["batch_id"], batch["workspace_id"], batch["owner_user_id"]
            )
            export_analysis = analyze_dianxiaomi_export(batch, copies)
            export_payload = {
                "ready": export_analysis.ready,
                "exportable_style_count": len(export_analysis.exportable_styles),
                "selected_exportable_style_count": export_analysis.selected_exportable_style_count,
                "user_excluded_style_count": export_analysis.user_excluded_style_count,
                "skipped_style_count": export_analysis.skipped_style_count,
                "block_reason": export_analysis.block_reason,
            }
        style_count = int(batch["requested_count"])
        # 列式批次的分组摘要：先按 group_id 归并 sheet，避免 groups × sheets 的二次遍历。
        sheets_by_group: dict[str, dict[str, str]] = {}
        for sheet in batch.get("group_sheets") or []:
            sheets_by_group.setdefault(str(sheet.get("group_id")), {})[
                str(sheet.get("role") or "")
            ] = str(sheet.get("status") or "")
        payload: dict[str, Any] = {
            "id": batch["batch_id"],
            "batch_id": batch["batch_id"],
            "mode": mode,
            "title": batch["title"],
            "status": batch["status"],
            "template_id": batch["template_id"],
            "template_snapshot_id": batch["template_snapshot_id"],
            "template_name": batch["template_name"],
            "count": style_count,
            "style_count": style_count,
            "item_count": style_count * 4,
            "processed_count": batch["processed_count"],
            "completed_count": batch["completed_count"],
            "failed_count": batch["failed_count"],
            "completed_item_count": completed_item_count,
            "failed_item_count": failed_item_count,
            "title_completed_count": batch.get("title_completed_count", 0),
            "title_failed_count": batch.get("title_failed_count", 0),
            "listing_ready_count": batch.get("listing_ready_count", 0),
            "style_grid": bool(batch.get("style_grid")),
            "initial_call_count": batch["initial_call_count"],
            "refill_call_count": batch["refill_call_count"],
            "prompt_version": batch["prompt_version"],
            "prompt_snapshot": batch["prompt_snapshot"],
            "business_fields": batch["business_fields"],
            "generation_layout": batch.get("generation_layout") or "legacy",
            "group_count": int(batch.get("group_count") or 0),
            "art_direction": batch.get("art_direction") or None,
            "groups": [
                {
                    "group_index": int(group["group_index"]),
                    "style_indices": list(group.get("style_indices") or []),
                    "status": str(group.get("status") or ""),
                    "current_round": str(group.get("current_round") or ""),
                    "sheets": sheets_by_group.get(str(group.get("group_id")), {}),
                }
                for group in batch.get("style_groups") or []
            ],
            "listing_fields": None if semi else batch["listing_fields"],
            "dianxiaomi_export": export_payload,
            "creative_prompt": batch["creative_prompt"],
            "composition": self._batch_composition_payload(batch),
            "error_message": batch["error_message"],
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
            "finished_at": batch.get("finished_at", ""),
            "template": template_payload,
            "items": items,
            "style_titles": [self._title_payload(title) for title in batch.get("style_titles", [])],
        }
        if replica:
            payload["source"] = self._replica_source_payload(batch)
            payload["targets"] = [
                self._replica_target_payload(target)
                for target in batch.get("replica_targets", [])
            ]
        if mode == "fission":
            payload.pop("prompt_snapshot", None)
            payload.pop("creative_prompt", None)
            for key in ("template_id", "template_snapshot_id", "template_name"):
                payload.pop(key, None)
            payload["template"] = None
            payload["generation_strategy"] = str(batch.get("fission_generation_strategy") or "grid")
            payload["source"] = self._replica_source_payload({"replica_source": batch.get("fission_source")})
            payload["target"] = self._replica_source_payload({"replica_source": batch.get("fission_target")})
            payload["variants"] = [
                {key: variant.get(key) for key in (
                    "style_index", "image_status", "title_status", "spec_card_status", "error_message"
                )}
                for variant in batch.get("fission_variants", [])
            ]
        return payload

    @staticmethod
    def _batch_composition_payload(batch: dict[str, Any]) -> dict[str, Any] | None:
        raw = batch.get("composition_json")
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        if batch.get("mode") == "fission":
            template_id = str(record.get("template_id") or "")
            record = {
                "composition_id": template_id,
                "name": "默认构图" if template_id == "fission_default_v1" else "已冻结构图",
                "scope": COMPOSITION_SCOPE_FISSION,
                "is_builtin": template_id == "fission_default_v1",
                "panels": {
                    key: {"zh": "", "en": str(panel.get("shot") or "")}
                    for key, panel in zip(COMPOSITION_PANEL_KEYS, record.get("panels", []))
                },
            }
        return PodCustomizationService._composition_payload(record)

    @staticmethod
    def _replica_source_payload(batch: dict[str, Any]) -> dict[str, Any]:
        source = batch.get("replica_source") or {}
        asset_id = source.get("asset_id", "")
        return {
            "asset_id": asset_id,
            "preview_url": f"/api/pod-customization/assets/{asset_id}" if asset_id else None,
            "download_url": f"/api/pod-customization/assets/{asset_id}?download=1" if asset_id else None,
            "filename": source.get("filename", ""),
            "content_type": source.get("content_type", ""),
            "width": source.get("width"),
            "height": source.get("height"),
        }

    @staticmethod
    def _replica_target_payload(target: dict[str, Any]) -> dict[str, Any]:
        asset = target.get("asset") or {}
        asset_id = target.get("asset_id", "")
        return {
            "style_index": target.get("style_index"),
            "asset_id": asset_id,
            "preview_url": f"/api/pod-customization/assets/{asset_id}" if asset_id else None,
            "download_url": f"/api/pod-customization/assets/{asset_id}?download=1" if asset_id else None,
            "filename": asset.get("filename", ""),
            "width": asset.get("width"),
            "height": asset.get("height"),
            "product_name": (target.get("business_fields") or {}).get("product_name", ""),
            "business_fields": target.get("business_fields"),
            "listing_fields": target.get("listing_fields"),
        }

    @staticmethod
    def _direct_listing_trial_payload(trial: dict[str, Any]) -> dict[str, Any]:
        attempts = trial["grid_attempt_asset_ids"]
        roles = LISTING_IMAGE_ROLES

        def asset_urls(asset_id: str) -> dict[str, str]:
            return {
                "preview_url": f"/api/pod-customization/assets/{asset_id}",
                "download_url": f"/api/pod-customization/assets/{asset_id}?download=1",
            }

        return {
            "id": trial["trial_id"],
            "status": trial["status"],
            "template_id": trial["template_id"],
            "prompt_snapshot": trial["prompt_snapshot"],
            "grid": asset_urls(attempts[-1]) if attempts else None,
            "grid_attempts": [
                {"attempt": index, **asset_urls(asset_id)}
                for index, asset_id in enumerate(attempts, start=1)
            ],
            "images": [
                {
                    "role": role,
                    **asset_urls(trial["panel_asset_ids"][role]),
                    "public_url": trial["public_urls"].get(role),
                }
                for role in roles
                if role in trial["panel_asset_ids"]
            ],
            "title": (
                PodCustomizationService._title_payload(trial["title_result"])
                if trial.get("title_result") is not None
                else None
            ),
            "error_message": trial["error_message"],
            "created_at": trial["created_at"],
            "updated_at": trial["updated_at"],
        }

    @staticmethod
    def _batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
        rows = batch.get("items") or []
        return {
            "id": batch["batch_id"],
            "mode": batch.get("mode", "full"),
            "title": batch["title"],
            "status": batch["status"],
            "template_id": batch["template_id"],
            "template_name": batch["template_name"],
            "count": batch["requested_count"],
            "item_count": int(batch["requested_count"]) * 4,
            "processed_count": batch["processed_count"],
            "completed_count": batch["completed_count"],
            "failed_count": batch["failed_count"],
            # 按「款/张」的计数（completed_count 是按「组」的），半定制列表按款展示。
            "completed_item_count": sum(1 for row in rows if row.get("status") == "completed"),
            "failed_item_count": sum(1 for row in rows if row.get("status") == "failed"),
            "title_completed_count": batch.get("title_completed_count", 0),
            "title_failed_count": batch.get("title_failed_count", 0),
            "listing_ready_count": batch.get("listing_ready_count", 0),
            "style_titles": [
                PodCustomizationService._title_payload(title)
                for title in batch.get("style_titles", [])
            ],
            "style_grid": bool(batch.get("style_grid")),
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
            "finished_at": batch.get("finished_at", ""),
        }

    @staticmethod
    def _title_payload(title: dict[str, Any]) -> dict[str, Any]:
        return {
            "style_index": title.get("style_index", 1),
            "style_task_id": title.get("style_task_id", ""),
            "status": title["status"],
            "title": title.get("title") or None,
            "source": title.get("source", "ai"),
            "listing_ready": bool(title.get("listing_ready", False)),
            "export_selected": bool(title.get("export_selected", True)),
            "error_message": title.get("error_message", ""),
            "updated_at": title.get("updated_at", ""),
        }

    @staticmethod
    def _item_payload(item: dict[str, Any]) -> dict[str, Any]:
        pattern_id = item["pattern_asset_id"]
        composite_id = item["composite_asset_id"]
        public_url = item.get("public_url") or None
        composite_preview_url = (
            f"/api/pod-customization/assets/{composite_id}" if composite_id else None
        )
        return {
            "id": item["item_id"],
            "index": item["item_index"],
            "style_index": item.get("style_index", item["item_index"]),
            "export_selected": bool(item.get("export_selected", True)),
            "variant_index": item.get("variant_index", 1),
            "status": item["status"],
            "pattern_preview_url": f"/api/pod-customization/assets/{pattern_id}" if pattern_id else None,
            "pattern_download_url": f"/api/pod-customization/assets/{pattern_id}?download=1" if pattern_id else None,
            "composite_preview_url": composite_preview_url,
            "composite_download_url": f"/api/pod-customization/assets/{composite_id}?download=1" if composite_id else None,
            "role": item.get("role") or None,
            "public_url": public_url,
            "scene_optimized": bool(item["scene_optimized"]),
            "error_message": item["error_message"],
            "updated_at": item["updated_at"],
        }
