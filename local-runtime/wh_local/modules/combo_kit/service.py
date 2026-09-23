"""combo_kit 业务编排：套装生命周期、主体解析、文本/生图 + 隔离扣费、预检。"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..product_processing.infrastructure.media import (
    GeneratedMedia,
    MediaConfigurationError,
    MediaProcessingError,
    ProductImageProcessor,
    _plausible_public_http_url,
)
from .assets import ComboKitAssets
from .billing import ComboKitBillingCoordinator
from .contracts import (
    DEFAULT_GENERATION_MODE,
    EDITABLE_PROMPT_ROLES,
    FUSION_MAIN_ROLE,
    GENERATED_API_ROLES,
    IMAGE_POINTS,
    IMAGE_ROLES,
    MAX_IMAGES,
    MIN_IMAGES,
    TEXT_POINTS,
    ComboKitConflict,
    ComboKitError,
    ComboKitNotFound,
    ComboKitValidationError,
    normalize_generation_mode,
)
from .prompts import (
    BASE_PROMPT_A,
    DETAIL_SHOT_TEMPLATE,
    MULTIVIEW_DETAIL_SHOT_TEMPLATE,
    base_prompt_for_role,
    default_base_for_index,
    default_image_prompts_for_mode,
    build_image_prompt,
    build_text_prompt,
    pick_role_view_indices,
)
from .autosegment import segment_subject_polygon
from .export import build_combo_dianxiaomi_export
from .repository import ComboKitRepository
from .ai_runtime import ComboKitAiRuntime
from .generation import _make_media_processor, _static_config, crop_subject_references
from .watermark import apply_watermark, normalize_watermark_config
from .worker import ProgressReporter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _report(progress: ProgressReporter | None, current: int, total: int, label: str = "") -> None:
    """向异步任务上报进度；同步调用（progress 为 None）时静默跳过。

    进度上报只是可观测性，任何异常都不能影响主流程与计费结算。
    """
    if progress is None:
        return
    try:
        progress({"current": int(current or 0), "total": int(total or 0), "label": str(label or "")})
    except Exception:
        pass


class ComboKitService:
    def __init__(
        self,
        repository: ComboKitRepository,
        assets: ComboKitAssets,
        ai_runtime: ComboKitAiRuntime,
        billing: ComboKitBillingCoordinator,
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.ai_runtime = ai_runtime
        self.billing = billing

    # ---- 生命周期 ----

    def create_set(
        self, payload: dict[str, Any], *, workspace_id: str, owner_user_id: str
    ) -> dict[str, Any]:
        set_id = _uuid()
        now = _now()
        name = str(payload.get("name") or "").strip()
        sku = str(payload.get("sku") or "").strip()
        sku_display = str(payload.get("sku_display") or "").strip()
        if sku and not sku_display:
            sku_display = _default_sku_display(name, payload.get("specs") or [])
        self.repository.create_set(
            {
                "set_id": set_id,
                "workspace_id": workspace_id,
                "owner_user_id": owner_user_id,
                "name": name,
                "sku": sku,
                "sku_display": sku_display,
                "description": str(payload.get("description") or ""),
                "bullets_json": json.dumps(payload.get("bullets") or [], ensure_ascii=False),
                "category_path": str(payload.get("category_path") or ""),
                "category_id": str(payload.get("category_id") or ""),
                "attributes_json": json.dumps(payload.get("attributes") or {}, ensure_ascii=False),
                "sku_specs_json": json.dumps(payload.get("specs") or [], ensure_ascii=False),
                "generation_mode": normalize_generation_mode(payload.get("generation_mode")),
                "status": "draft",
                "stage": "set_info",
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_set(set_id)

    def get_set(self, set_id: str) -> dict[str, Any]:
        try:
            base = self.repository.get_set(set_id)
        except KeyError:
            raise ComboKitNotFound("组合套装不存在") from None
        items = self.repository.list_items(set_id)
        try:
            prompt = self.repository.get_prompt(set_id)
        except KeyError:
            prompt = {}
        return {
            **base,
            "items": items,
            "prompt": prompt,
            "billing": self.repository.list_billing(set_id),
            "preview": self._preview_or_none(set_id),
        }

    def remove_set(self, set_id: str) -> dict[str, Any]:
        if not self.repository.remove_set(set_id):
            raise ComboKitNotFound("组合套装不存在")
        return {"set_id": set_id, "status": "removed"}

    def _preview_or_none(self, set_id: str) -> dict[str, Any] | None:
        try:
            return self.repository.get_preview(set_id)
        except KeyError:
            return None

    def list_sets(
        self, workspace_id: str, *, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        sets = self.repository.list_sets(workspace_id, limit=limit, offset=offset)
        return {"sets": sets, "count": len(sets)}

    def update_set(
        self,
        set_id: str,
        payload: dict[str, Any],
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        base = self._require_set(set_id)
        update: dict[str, Any] = {}
        mapping = {
            "name": "name",
            "description": "description",
            "category_path": "category_path",
            "category_id": "category_id",
            "declared_price": "declared_price",
            "category_name": "category_name",
            "id_type": "id_type",
            "id_code": "id_code",
        }
        for src, dst in mapping.items():
            if src in payload:
                update[dst] = str(payload.get(src) or "")
        # attributes 是「键值映射」对象（与 create_set 一致），bullets/specs 是数组。
        for key in ("bullets", "specs"):
            if key in payload:
                update[f"{key}_json"] = json.dumps(payload.get(key) or [], ensure_ascii=False)
        if "attributes" in payload:
            update["attributes_json"] = json.dumps(payload.get("attributes") or {}, ensure_ascii=False)
        if "sku" in payload:
            update["sku"] = str(payload.get("sku") or "")
        if "sku_display" in payload:
            update["sku_display"] = str(payload.get("sku_display") or "")
        if "fusion_prompt" in payload:
            update["fusion_prompt"] = str(payload.get("fusion_prompt") or "")
        # 生成选型：未识别值回退默认（bundle），保证旧前端/异常入参不会写入脏数据。
        if "generation_mode" in payload:
            update["generation_mode"] = normalize_generation_mode(payload.get("generation_mode"))
        # 水印配置：整套存成一个 JSON 对象，非法值由归一化收敛到安全默认。
        if "watermark" in payload:
            update["watermark_json"] = json.dumps(
                normalize_watermark_config(payload.get("watermark")), ensure_ascii=False
            )
        # 店小秘必填数值字段：长宽高/重量/库存/建议售价。
        for key in ("length_cm", "width_cm", "height_cm", "weight_g", "suggested_price_usd"):
            if key in payload:
                update[key] = _to_float(payload.get(key))
        if "stock" in payload:
            update["stock"] = _to_int(payload.get("stock"))
        if update:
            self.repository.update_set(set_id, update)
        return self.get_set(set_id)

    # ---- 子商品素材（上传/排序/删除/主体词/蒙版） ----

    def add_item(
        self,
        set_id: str,
        payload: dict[str, Any],
        *,
        image_content: bytes | None,
        image_filename: str,
        image_content_type: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        self._require_set(set_id)
        existing = self.repository.list_items(set_id)
        count = len(existing)
        if count + 1 > MAX_IMAGES:
            raise ComboKitValidationError(f"单个套装最多 {MAX_IMAGES} 张原图")
        if image_content is None:
            raise ComboKitValidationError("上传图片来源图不能为空")
        saved = self.assets.save_original(
            image_content, image_filename, image_content_type, workspace_id=workspace_id
        )
        item = self.repository.add_item(
            {
                "set_id": set_id,
                "workspace_id": workspace_id,
                "owner_user_id": owner_user_id,
                "item_index": count + 1,
                "original_asset_id": saved["sha256"],
                "original_path": saved["path"],
                "original_url": f"/api/combo-kit/originals/{set_id}/{saved['sha256']}{saved['suffix']}",
                "subject_keywords": str(payload.get("subject_keywords") or ""),
                "mask_json": json.dumps(payload.get("mask") or {}, ensure_ascii=False),
                "mask_inverted": bool(payload.get("mask_inverted")),
                "spec_text": str(payload.get("spec_text") or ""),
                "created_at": _now(),
                "updated_at": _now(),
            }
        )
        return item

    def update_item(
        self, set_id: str, item_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_set(set_id)
        update: dict[str, Any] = {}
        for key in ("subject_keywords", "mask_inverted", "spec_text"):
            if key in payload:
                update[key] = payload.get(key) or (0 if key == "mask_inverted" else "")
        if "item_index" in payload and payload.get("item_index") is not None:
            update["item_index"] = int(payload["item_index"])
        if "mask" in payload:
            update["mask_json"] = json.dumps(payload.get("mask") or {}, ensure_ascii=False)
        if payload.get("mask_edit"):
            cur = self.repository.get_item(item_id)
            update["mask_regex_serial"] = int(cur.get("mask_regex_serial") or 0) + 1
        if update:
            self.repository.update_item(item_id, update)
        return self.repository.get_item(item_id)

    def auto_mask_item(self, set_id: str, item_id: str) -> dict[str, Any]:
        """算法预框选：分割出主体轮廓，作为蒙版初始多边形直接落库。

        用户在此基础上拖动控制点微调即可。全程本地 onnxruntime 推理，不调外部
        API、不计费。分割失败或结果不可信时不写库，返回空点，由前端回落到默认
        六边形——绝不能把坏蒙版写进库。
        """
        self._require_set(set_id)
        try:
            item = self.repository.get_item(item_id)
        except KeyError:
            raise ComboKitNotFound("来源图不存在") from None
        if str(item.get("set_id") or "") != set_id:
            raise ComboKitNotFound("来源图不存在") from None
        try:
            path = self.assets.require_original(
                str(item.get("original_path") or ""),
                workspace_id=str(item.get("workspace_id") or "local"),
            )
        except (ValueError, FileNotFoundError):
            raise ComboKitNotFound("来源图文件不存在") from None
        points = segment_subject_polygon(str(path))
        if not points:
            return {"item_id": item_id, "points": [], "status": "unavailable"}
        self.repository.update_item(
            item_id, {"mask_json": json.dumps({"points": points}, ensure_ascii=False)}
        )
        return {"item_id": item_id, "points": points, "status": "applied"}

    def remove_item(self, set_id: str, item_id: str) -> dict[str, Any]:
        self._require_set(set_id)
        removed = self.repository.remove_item(set_id, item_id)
        if not removed:
            raise ComboKitNotFound("来源图不存在") from None
        return {"item_id": item_id, "status": "removed"}

    def set_primary_item(self, set_id: str, item_id: str) -> dict[str, Any]:
        """把某成员设为套装的主要商品（其余成员自动取消主要标记）。"""
        self._require_set(set_id)
        try:
            self.repository.set_primary_item(set_id, item_id)
        except KeyError:
            raise ComboKitNotFound("来源图不存在") from None
        return self.repository.get_item(item_id)

    def clear_primary_item(self, set_id: str) -> dict[str, Any]:
        """取消套装的主要商品标记（清空 is_primary）。"""
        self._require_set(set_id)
        self.repository.clear_primary_item(set_id)
        return {"items": self.repository.list_items(set_id)}

    def list_items(self, set_id: str) -> dict[str, Any]:
        self._require_set(set_id)
        return {"items": self.repository.list_items(set_id)}

    def set_item_order(self, set_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_set(set_id)
        order = payload.get("order") or []
        # 只允许本套装内的 item 参与排序：跨套装 item_id 一律忽略，
        # 防止借排序接口篡改其它套装的 item_index。
        valid_ids = {str(item.get("item_id")) for item in self.repository.list_items(set_id)}
        for index, item_id in enumerate(order, start=1):
            if str(item_id) not in valid_ids:
                continue
            try:
                self.repository.update_item(str(item_id), {"item_index": index})
            except KeyError:
                continue
        return {"items": self.repository.list_items(set_id)}

    # ---- 主体解析（串行） ----

    def analyze_subject(
        self,
        set_id: str,
        payload: dict[str, Any],
        *,
        actor: Any,
        progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        base = self._require_set(set_id)
        item_ids = payload.get("item_ids") or None
        items = self.repository.list_items(set_id)
        if item_ids:
            wanted = {str(item_id) for item_id in item_ids}
            items = [item for item in items if str(item.get("item_id")) in wanted]
        if not items:
            raise ComboKitValidationError("没有可解析主体的来源图")
        # bundle 选型每张图是一个成员商品，必须填写主体词才能融合；
        # multiview 选型的图是同一商品的视角，允许不填主体词（视为未标注视角）。
        mode = str(base.get("generation_mode") or DEFAULT_GENERATION_MODE)
        if mode != "multiview":
            if not all(str(item.get("subject_keywords") or "").strip() for item in items):
                raise ComboKitValidationError("请先为每个子商品填写主体词")
        # 单品多视角：先识别每张来源图的拍摄机位并落库。机位是后续「按角色分配参考图」
        # 与「指定输出视角」的唯一依据，缺了它模型只会把多张机位当成同角度重复参考做平均。
        # 识别失败（返回空列表）时退化为不写 view_label，绝不阻断主体解析主流程。
        view_labels: list[str] = []
        # 主体识别本身不扣费：复用「文本」批次的直连 ark 密钥（冻结→领key→调用→退额）。
        freeze = self.billing.freeze(
            actor,
            billing_type="text",
            set_id=set_id,
            idempotency_key=f"combo-kit:subject:{set_id}",
            scope=["title"],
        )
        results = []
        total = len(items)
        _report(progress, 0, total, "解析主体")
        try:
            with text_context(freeze):
                # 机位识别必须放在 text_context 内：DoubaoArkClient 依赖上下文里的直连
                # 密钥，在上下文外构造会退回托管分支并因 usage 未预留而直接失败。
                if mode == "multiview":
                    view_labels = list(
                        self.ai_runtime.classify_view_labels(
                            image_paths=[
                                str(item.get("original_path") or "") or str(item.get("original_url") or "")
                                for item in items
                            ]
                        )
                    )
                for index, item in enumerate(items, start=1):
                    item_id = str(item["item_id"])
                    parsed = self.ai_runtime.analyze_subject(
                        # 传入本地落盘路径：ai_runtime 读取后转 base64 data URL 内嵌，
                        # 与 POD 主体/主图识别一致，方舟上游无需访问本机/鉴权 URL。
                        image_path=str(item.get("original_path") or "") or str(item.get("original_url") or ""),
                        subject_keywords=str(item.get("subject_keywords") or ""),
                        mask=_read_json(item.get("mask_json") or {}, {}),
                        original_fallback_title=str(item.get("subject_keywords") or "商品主体"),
                    )
                    # 机位标签与 items 一一对应（classify_view_labels 保序且等长）；缺失则不写。
                    if len(view_labels) == total:
                        parsed["view_label"] = view_labels[index - 1]
                    self.repository.update_item(item_id, {"subject_parsed_json": json.dumps(parsed, ensure_ascii=False)})
                    results.append({"item_id": item_id, **parsed})
                    _report(progress, index, total, "解析主体")
        finally:
            self.billing.settle(actor, freeze, success=False)
        # 主体解析完成后，立即生成融合套装主图（预览），作为后续第 1 张成品图复用。
        # 该次生图计入整套生图调用计数，扣费在整套生成阶段统一打包 100 分结算。
        _report(progress, 0, 0, "生成融合主图")
        custom_prompt = str(base.get("fusion_prompt") or "")
        main_image = self._generate_fusion_main(set_id, base, items, actor=actor, custom_prompt=custom_prompt)
        self.repository.update_set(set_id, {"stage": "subject"})
        return {"results": results, "items": self.repository.list_items(set_id), "main_image": main_image}

    # ---- Prompt 配置 ----

    def save_prompt(self, set_id: str, payload: dict[str, Any], *, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        self._require_set(set_id)
        self.repository.upsert_prompt(
            {
                "set_id": set_id,
                "workspace_id": workspace_id,
                "owner_user_id": owner_user_id,
                "base_prompt_a": str(payload.get("base_prompt_a") or ""),
                "base_prompt_b": str(payload.get("base_prompt_b") or ""),
                "image_prompts_json": json.dumps(payload.get("image_prompts") or {}, ensure_ascii=False),
            }
        )
        return self.repository.get_prompt(set_id)

    def get_prompt(self, set_id: str) -> dict[str, Any]:
        base = self._require_set(set_id)
        try:
            return self.repository.get_prompt(set_id)
        except KeyError:
            mode = str(base.get("generation_mode") or DEFAULT_GENERATION_MODE)
            return {"defaults": True, "base_prompt_a": BASE_PROMPT_A,
                    "image_prompts": default_image_prompts_for_mode(mode)}

    # ---- 文本生成（20 积分，隔离扣费） ----

    def generate_text(self, set_id: str, *, actor: Any, progress: ProgressReporter | None = None) -> dict[str, Any]:
        base = self._require_set(set_id)
        items = self.repository.list_items(set_id)
        subject_summaries = []
        for item in items:
            parsed = _read_json(item.get("subject_parsed_json") or {}, {})
            summary = str(parsed.get("sellable_subject") or item.get("subject_keywords") or "").strip()
            if summary:
                subject_summaries.append(summary)
        specs = _read_json(base.get("sku_specs_json") or [], [])
        category = str(base.get("category_path") or "")
        set_name = str(base.get("name") or "")
        primary_subject = _primary_subject(items)
        prompt_text = build_text_prompt(
            set_name=set_name, category=category, specs=specs,
            subject_summaries=subject_summaries, primary_subject=primary_subject,
            mode=str(base.get("generation_mode") or DEFAULT_GENERATION_MODE),
        )
        freeze = self.billing.freeze(
            actor,
            billing_type="text",
            set_id=set_id,
            idempotency_key=f"combo-kit:text:{set_id}:{uuid.uuid4().hex}",
            scope=["title"],
        )
        billing = self.repository.add_billing(
            {
                "workspace_id": base.get("workspace_id"),
                "owner_user_id": str(getattr(actor, "id", "") or ""),
                "set_id": set_id,
                "billing_type": "text",
                "freeze_id": freeze.get("freeze_id") or "",
                "rule_version": freeze.get("rule_version") or 0,
                "points": TEXT_POINTS,
                "status": "frozen",
                "created_at": _now(),
                "updated_at": _now(),
            }
        )
        _report(progress, 0, 1, "生成标题与卖点")
        try:
            with text_context(freeze):
                result = self.ai_runtime.generate_text(prompt=prompt_text)
        except ComboKitError:
            self._settle_billing(billing["billing_id"], freeze, success=False, actor=actor)
            raise
        self.repository.update_set(
            set_id, {"text_result_json": json.dumps(result, ensure_ascii=False), "status": "text_ready", "stage": "text"}
        )
        self._settle_billing(billing["billing_id"], freeze, success=True, actor=actor)
        _report(progress, 1, 1, "生成标题与卖点")
        return result

    # ---- 生图（100 积分，隔离扣费） ----

    def generate_images(
        self, set_id: str, *, actor: Any, roles: list[str] | None = None, progress: ProgressReporter | None = None
    ) -> dict[str, Any]:
        base = self._require_set(set_id)
        watermark = normalize_watermark_config(base.get("watermark_json"))
        mode = str(base.get("generation_mode") or DEFAULT_GENERATION_MODE)
        # 防御：只允许传入 API 生图角色（main/detail_page 为融合/拼接生成，不在其列）。
        # 否则非法角色会被 generation 层过滤成零产出，却仍按整套 100 分结算成功。
        if roles:
            invalid = [str(r) for r in roles if str(r) not in GENERATED_API_ROLES]
            if invalid:
                raise ComboKitValidationError(f"不支持的生成角色：{', '.join(invalid)}")
        items = self.repository.list_items(set_id)
        if not items:
            raise ComboKitValidationError("没有可用的来源图")
        _report(progress, 0, 0, "准备主体参考图")
        # 关键：生成融合主图前，把每张原图按用户蒙版抠出主体作为参考图，
        # 确保生成结果以「框选主体」为核心，而不是整张原图。
        # 单品多视角额外做尺度归一化：同一商品的各张原图构图不一，
        # 不统一主体占比会让模型每张图各画一个比例，跨图尺寸与弧度漂移。
        reference_values, reference_views = self._prepare_references(items, mode=mode)
        # 单品多视角：每个角色只送与该角色构图匹配的机位（最多 2 张），并在提示词里
        # 写明「第几张是什么视角、必须按哪个视角输出」。把 4 个机位无差别送给每个角色，
        # 模型会在机位之间平均出一个真机不存在的造型（怪形状的直接来源）。
        role_indices: dict[str, list[int]] = {}
        if reference_views:
            for role in GENERATED_API_ROLES:
                indices = pick_role_view_indices(reference_views, role)
                if indices:
                    role_indices[role] = indices
        # server-managed-wuyin 托管网关只能抓取公网 http(s) URL；本地参考图需先
        # 发布到 COS 生成公网直链，否则网关 urls=[] 导致图生图任务失败。
        reference_values = self._publish_references(
            reference_values, workspace_id=str(base.get("workspace_id") or "local")
        )
        # 发布同样保序（逐个上传），按原下标取直链即可，保证「参考图 ↔ 机位」不错位。
        references_by_role = {
            role: [reference_values[index] for index in indices]
            for role, indices in role_indices.items()
        }
        view_plan = {
            role: ([reference_views[index] for index in indices], reference_views[indices[0]])
            for role, indices in role_indices.items()
        }
        prompt_cfg = self._prompt_or_default(
            set_id, mode=str(base.get("generation_mode") or DEFAULT_GENERATION_MODE)
        )
        image_prompts = _read_json(prompt_cfg.get("image_prompts") or {}, {})
        per_image = self._build_image_prompts(
            set_id, base, prompt_cfg, image_prompts, view_plan=view_plan
        )
        # 第 1 张套装主图复用主体解析阶段的融合主图，不再重复调用生图 API。
        main_entry = self._main_image_entry(set_id)
        fusion_content, fusion_suffix = self._read_image_source(main_entry)
        freeze = self.billing.freeze(
            actor,
            billing_type="image",
            set_id=set_id,
            idempotency_key=f"combo-kit:image:{set_id}:{uuid.uuid4().hex}",
            scope=["four_grid"],
        )
        billing = self.repository.add_billing(
            {
                "workspace_id": base.get("workspace_id"),
                "owner_user_id": str(getattr(actor, "id", "") or ""),
                "set_id": set_id,
                "billing_type": "image",
                "freeze_id": freeze.get("freeze_id") or "",
                "rule_version": freeze.get("rule_version") or 0,
                "points": IMAGE_POINTS,
                "status": "frozen",
                "created_at": _now(),
                "updated_at": _now(),
            }
        )
        set_id_val = set_id
        workspace_id = str(base.get("workspace_id") or "local")
        # 并发生图子线程读不到 server_ai_context 的 ContextVar，需注入固化直连密钥
        # 的处理器，避免退化到托管分支（usage not reserved）。
        self.ai_runtime._media = self._direct_media_processor(freeze)
        _report(progress, 0, len(per_image) or 1, "并发生成成品图")
        try:
            with image_context(freeze):
                outputs = self.ai_runtime.generate_images(
                    reference_values=reference_values,
                    references_by_role=references_by_role or None,
                    prompts=per_image,
                    fusion_content=fusion_content,
                    fusion_suffix=fusion_suffix,
                    set_id=set_id_val,
                    workspace_id=workspace_id,
                    title=str(base.get("name") or ""),
                    category=str(base.get("category_path") or ""),
                    roles=roles,
                )
        except (ComboKitError, MediaConfigurationError, MediaProcessingError):
            self._settle_billing(billing["billing_id"], freeze, success=False, actor=actor)
            raise
        # 全部成功后落盘并结算。main 命中则保留，其余张按角色并入，不覆盖本次未生成的角色。
        # 落盘/COS 发布也纳入失败结算保护：任一步抛异常必须按失败结算并解锁冻结，
        # 否则 freeze 永久泄漏、billing 停留在 frozen。
        saved = []
        _report(progress, 0, len(outputs) or 1, "保存并发布成品图")
        try:
            for out in outputs:
                stage = str(out.get("role") or "")
                suffix = str(out.get("suffix") or ".jpg")
                raw = bytes(out.get("content") or b"")
                # 始终留一份未烧水印的干净副本：水印配置随时可能改，
                # 只有保留原始像素才能在「立即应用到已生成图」时重新合成而不叠层。
                clean_path = self._save_clean_copy(
                    raw, stage=stage, set_id=set_id_val, suffix=suffix, workspace_id=workspace_id
                )
                # 生成后立即烧水印：落盘与 COS 直链用同一份带水印字节，
                # 保证页面预览 / 下载 / 导出店小秘 / 预检四处完全一致。
                content = apply_watermark(raw, watermark, suffix=suffix)
                path = self.assets.save_generated(
                    content,
                    stage=stage,
                    set_id=set_id_val,
                    suffix=suffix,
                    workspace_id=workspace_id,
                )
                saved.append({
                    "role": out.get("role"),
                    "label": out.get("label"),
                    "path": path,
                    "clean_path": clean_path,
                    "url": f"/api/combo-kit/generated/{set_id_val}/{out.get('role')}.jpg",
                    "public_url": self._publish_to_cos(
                        content,
                        stage=stage,
                        suffix=suffix,
                        workspace_id=workspace_id,
                    ),
                    "provider": out.get("provider"),
                    "model": out.get("model"),
                    "attempt_count": out.get("attempt_count"),
                })
        except Exception:
            self._settle_billing(billing["billing_id"], freeze, success=False, actor=actor)
            raise
        # 保留本次未重新生成的角色（含 main），按 IMAGE_ROLES 稳定排序，替换时其它图不被覆盖。
        # 套装主图在主体解析阶段就已生成，而水印可能在成品图阶段才新设/改动，
        # 故先按当前水印配置重烧一次主图，避免第 1 张成品图成为唯一没有水印的图。
        self._sync_main_watermark(set_id, watermark, workspace_id)
        regenerated_roles = {str(item.get("role") or "") for item in saved}
        existing = _read_json(self._require_set(set_id).get("image_results_json") or [], [])
        kept = [
            entry for entry in existing
            if str(entry.get("role") or "") not in regenerated_roles
        ]
        final = _order_image_entries([*kept, *saved])
        self.repository.update_set(set_id, {"image_results_json": json.dumps(final, ensure_ascii=False), "status": "images_ready", "stage": "images"})
        self._settle_billing(billing["billing_id"], freeze, success=True, actor=actor)
        return {"images": final}

    def delete_generated_image(self, set_id: str, role: str) -> dict[str, Any]:
        """删除某一张成品图（角色），其余图保留；删除后从列表移除并释放落盘文件。"""
        base = self._require_set(set_id)
        existing = _read_json(base.get("image_results_json") or [], [])
        target = str(role or "").strip()
        kept = [entry for entry in existing if str(entry.get("role") or "") != target]
        if len(kept) == len(existing):
            raise ComboKitNotFound(f"成品图角色不存在：{target}")
        # 删除落盘文件（尽力而为）：本地路径只清理受管目录。
        for entry in existing:
            if str(entry.get("role") or "") == target:
                for key in ("path", "clean_path"):
                    path = str(entry.get(key) or "")
                    try:
                        if path and "://" not in path:
                            Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
        self.repository.update_set(set_id, {"image_results_json": json.dumps(_order_image_entries(kept), ensure_ascii=False)})
        kept_set = {str(e.get("role") or "") for e in kept}
        return {"images": _order_image_entries(kept), "status": "removed", "removed_role": target, "remaining_roles": sorted(kept_set)}

    def apply_watermark_to_images(self, set_id: str) -> dict[str, Any]:
        """把当前水印配置立即重新烧到已生成的成品图上（不重新生图、不计费）。

        以「未烧水印源图」重新合成，因此反复保存水印不会叠层；关闭水印后执行
        即把成品图还原成干净图。某张源图不可读时只跳过该张，不影响其余成品图。
        """
        base = self._require_set(set_id)
        watermark = normalize_watermark_config(base.get("watermark_json"))
        workspace_id = str(base.get("workspace_id") or "local")
        existing = _read_json(base.get("image_results_json") or [], [])
        updated: list[dict[str, Any]] = []
        applied = 0
        for entry in existing:
            role = str(entry.get("role") or "")
            source, suffix = self._read_image_source(entry)
            if not role or not source:
                updated.append(entry)
                continue
            try:
                content = apply_watermark(source, watermark, suffix=suffix)
                path = self.assets.save_generated(
                    content, stage=role, set_id=set_id, suffix=suffix, workspace_id=workspace_id
                )
            except Exception:
                updated.append(entry)
                continue
            # 老数据没有干净副本：把本次的源图回填为干净副本，后续保存才不会叠层。
            clean_path = str(entry.get("clean_path") or "") or self._save_clean_copy(
                source, stage=role, set_id=set_id, suffix=suffix, workspace_id=workspace_id
            )
            updated.append({
                **entry,
                "path": path,
                "clean_path": clean_path,
                "public_url": self._publish_to_cos(
                    content, stage=role, suffix=suffix, workspace_id=workspace_id
                ),
            })
            applied += 1
        if updated:
            self.repository.update_set(
                set_id, {"image_results_json": json.dumps(_order_image_entries(updated), ensure_ascii=False)}
            )
        return {
            "images": _order_image_entries(updated),
            "applied": applied,
            "enabled": bool(watermark.get("enabled")),
        }

    def _build_image_prompts(
        self,
        set_id: str,
        base: dict[str, Any],
        prompt_cfg: dict[str, Any],
        image_prompts: dict[str, Any],
        *,
        view_plan: dict[str, tuple[list[str], str]] | None = None,
    ) -> dict[str, str]:
        mode = str(base.get("generation_mode") or DEFAULT_GENERATION_MODE)
        plan = view_plan or {}
        subjects = _member_subjects(self.repository.list_items(set_id))
        # 单品多视角是「同一件商品的多张视角」，各 item 的主体词指向同一件商品，
        # 只取第一条，避免把视角差异写成多个成员商品。
        if mode == "multiview":
            subjects = subjects[:1]
        specs = _read_json(base.get("sku_specs_json") or [], [])
        set_name = str(base.get("name") or "")
        base_a = str(prompt_cfg.get("base_prompt_a") or "") or BASE_PROMPT_A
        # 角色方向留空时的兜底默认词必须与生成选型一致：否则 multiview 会回落到
        # bundle 的「完整套装/每个成员」措辞，把多视角图又变成拼贴/多件商品。
        mode_defaults = default_image_prompts_for_mode(mode)
        built: dict[str, str] = {}
        # 场景图与白底图开放用户自定义辅助提示词（方向词留空时回退内置默认方向）。
        for role in EDITABLE_PROMPT_ROLES:
            if role == "detail_shot":
                continue
            direction = str(image_prompts.get(role) or "").strip() or str(mode_defaults.get(role) or "")
            reference_views, output_view = plan.get(role, ([], ""))
            built[role] = build_image_prompt(
                role=role,
                # 场景图强制改用场景基础模板：基础模板 A 的 no human / 中性背景会压掉
                # 真实生活场景与手部，正是场景图出不来场景感的原因。
                # 单品多视角选型额外切换为「单件商品」模板，避免出现套装措辞。
                base_prompt=base_prompt_for_role(role, base_a, mode),
                role_direction=direction,
                subjects=subjects,
                set_specs=specs,
                set_name=set_name,
                mode=mode,
                reference_views=reference_views,
                output_view=output_view,
            )
        # 细节图：固定模板为底座，用户补充只做追加，不允许覆盖模板（避免细节图退化成纯特写裁切）。
        # 模板按选型切换：bundle 版要「整套 + 一处 inset」，单品多视角只有一件商品，
        # 套用 bundle 版会被模型理解成一排小格子（俯视/滚轮/侧键/指示灯各一小块）。
        detail_supplement = str(image_prompts.get("detail_shot") or "").strip()
        detail_template = MULTIVIEW_DETAIL_SHOT_TEMPLATE if mode == "multiview" else DETAIL_SHOT_TEMPLATE
        detail_direction = detail_template
        if detail_supplement:
            detail_direction = f"{detail_template}\nAdditional user requirements: {detail_supplement}"
        detail_views, detail_output_view = plan.get("detail_shot", ([], ""))
        built["detail_shot"] = build_image_prompt(
            role="detail_shot",
            base_prompt=base_prompt_for_role("detail_shot", base_a, mode),
            role_direction=detail_direction,
            subjects=subjects,
            set_specs=specs,
            set_name=set_name,
            mode=mode,
            reference_views=detail_views,
            output_view=detail_output_view,
        )
        return built

    def _prompt_or_default(self, set_id: str, *, mode: str = DEFAULT_GENERATION_MODE) -> dict[str, Any]:
        try:
            return self.repository.get_prompt(set_id)
        except KeyError:
            return {
                "base_prompt_a": BASE_PROMPT_A,
                "image_prompts": default_image_prompts_for_mode(mode),
            }

    # ---- 套装主图（主体解析后生成，作为第 1 张成品图复用） ----
    # bundle 选型：把各成员商品融合成一张套装主图；
    # multiview 选型：参考图是同一商品的多张视角（含内部/展开图），不融合，直接出商品主图。

    def _generate_fusion_main(
        self,
        set_id: str,
        base: dict[str, Any],
        items: list[dict[str, Any]],
        *,
        actor: Any,
        custom_prompt: str = "",
    ) -> dict[str, Any] | None:
        mode = str(base.get("generation_mode") or DEFAULT_GENERATION_MODE)
        # 关键：生成主图前，把每张原图按用户蒙版抠出主体作为参考图。
        # multiview 也走同一策略：原图多为实拍（木桌/手部/窗户/绿植等背景），
        # 整张送入会让模型一边剥离杂物一边挑视角，导致主图造型漂移、轮廓失真。
        reference_values, reference_views = self._prepare_references(items, mode=mode)
        # multiview：主图只送与「商品主图」构图匹配的机位（3/4 视角优先，最多 2 张）。
        # 把全部机位无差别送过去，模型会在机位之间平均出一个真机不存在的造型，
        # 且不指定输出视角时成品图会各自乱挑角度。
        main_view = ""
        if reference_views:
            indices = pick_role_view_indices(reference_views, "main")
            if indices:
                reference_values = [reference_values[index] for index in indices]
                reference_views = [reference_views[index] for index in indices]
            main_view = reference_views[0] if reference_views else ""
        # 主图同样走托管网关：本地参考图需先发布为公网直链。
        reference_values = self._publish_references(
            reference_values, workspace_id=str(base.get("workspace_id") or "local")
        )
        subject_summaries = []
        for item in items:
            parsed = _read_json(item.get("subject_parsed_json") or {}, {})
            summary = str(parsed.get("sellable_subject") or item.get("subject_keywords") or "").strip()
            if summary:
                subject_summaries.append(summary)
        set_name = str(base.get("name") or "")
        primary_subject = _primary_subject(items)
        # 预览主图：生图上下文临时冻结 → 生成 → 退额（零净扣费）。
        # 真正扣费在整套生成阶段打包 100 分结算（第 1 次生图调用计数）。
        freeze: dict[str, Any] | None = None
        out: dict[str, Any] | None = None
        try:
            freeze = self.billing.freeze(
                actor,
                billing_type="image",
                set_id=set_id,
                idempotency_key=f"combo-kit:fusion:{set_id}",
                scope=["four_grid"],
            )
            with image_context(freeze):
                self.ai_runtime._media = self._direct_media_processor(freeze)
                out = self.ai_runtime.generate_fusion_main(
                    reference_values=reference_values,
                    set_name=set_name,
                    subject_summaries=subject_summaries,
                    view_labels=reference_views,
                    output_view=main_view,
                    primary_subject=primary_subject,
                    custom_prompt=custom_prompt,
                    mode=mode,
                )
        except Exception as exc:  # 不阻断主体解析结果返回。
            self.repository.update_set(set_id, {"error_message": f"套装主图生成失败：{str(exc)[:200]}"})
        finally:
            if freeze:
                try:
                    self.billing.settle(actor, freeze, success=False)
                except Exception:
                    pass
        if not out or not out.get("content"):
            return None
        workspace_id = str(base.get("workspace_id") or "local")
        content = bytes(out["content"] or b"")
        suffix = str(out.get("suffix") or ".jpg")
        watermark = normalize_watermark_config(base.get("watermark_json"))
        # 详情图是本地拼接合成，其内部素材必须是「干净主图」，否则详情图会出现双层水印；
        # 水印配置又可能在成品图阶段才修改，因此始终留一份未烧水印的主图副本。
        clean_path = self._save_clean_copy(
            content,
            stage=FUSION_MAIN_ROLE,
            set_id=set_id,
            suffix=suffix,
            workspace_id=workspace_id,
        )
        try:
            watermarked = apply_watermark(content, watermark, suffix=suffix)
            path = self.assets.save_generated(
                watermarked,
                stage=FUSION_MAIN_ROLE,
                set_id=set_id,
                suffix=suffix,
                workspace_id=workspace_id,
            )
        except Exception as exc:
            self.repository.update_set(set_id, {"error_message": f"融合主图落盘失败：{str(exc)[:200]}"})
            return None
        main_entry = {
            "role": FUSION_MAIN_ROLE,
            "label": "套装主图",
            "path": path,
            "clean_path": clean_path,
            "url": f"/api/combo-kit/generated/{set_id}/main.jpg",
            "public_url": self._publish_to_cos(
                watermarked,
                stage=FUSION_MAIN_ROLE,
                suffix=suffix,
                workspace_id=workspace_id,
            ),
            "provider": out.get("provider"),
            "model": out.get("model"),
            "attempt_count": out.get("attempt_count"),
        }
        self._upsert_main_image(set_id, main_entry)
        return main_entry

    def _sync_main_watermark(
        self, set_id: str, watermark: dict[str, Any], workspace_id: str
    ) -> None:
        """按当前水印配置重烧套装主图，使其与本次生成的其它成品图保持一致。

        始终以未烧水印的干净副本重新合成，因此重复执行不会叠加多层水印；
        任何失败都只跳过同步（保留原主图），不影响已成功生成结算的其它成品图。
        """
        main_entry = self._main_image_entry(set_id)
        if not main_entry:
            return
        content, suffix = self._read_image_source(main_entry)
        if not content:
            return
        try:
            watermarked = apply_watermark(content, watermark, suffix=suffix)
            path = self.assets.save_generated(
                watermarked,
                stage=FUSION_MAIN_ROLE,
                set_id=set_id,
                suffix=suffix,
                workspace_id=workspace_id,
            )
        except Exception:
            return
        self._upsert_main_image(set_id, {
            **main_entry,
            "path": path,
            "url": f"/api/combo-kit/generated/{set_id}/main.jpg",
            "public_url": self._publish_to_cos(
                watermarked, stage=FUSION_MAIN_ROLE, suffix=suffix, workspace_id=workspace_id
            ),
        })

    def _upsert_main_image(self, set_id: str, main_entry: dict[str, Any]) -> None:
        base = self._require_set(set_id)
        existing = _read_json(base.get("image_results_json") or [], [])
        existing = [entry for entry in existing if str(entry.get("role") or "") != FUSION_MAIN_ROLE]
        updated = [main_entry, *existing]
        self.repository.update_set(set_id, {"image_results_json": json.dumps(updated, ensure_ascii=False)})

    def _main_image_entry(self, set_id: str) -> dict[str, Any] | None:
        base = self._require_set(set_id)
        for entry in _read_json(base.get("image_results_json") or [], []):
            if str(entry.get("role") or "") == FUSION_MAIN_ROLE:
                return entry
        return None

    def _read_image_source(self, entry: dict[str, Any] | None) -> tuple[bytes | None, str]:
        """读取成品图的「未烧水印源图」：优先干净副本，缺失时回退当前成品图。

        详情图拼接与「立即应用水印」都以它为素材，保证重复合成不会叠层。
        """
        if not entry:
            return None, ".jpg"
        path = str(entry.get("clean_path") or "") or str(entry.get("path") or "")
        if not path:
            return None, ".jpg"
        try:
            content = Path(path).read_bytes()
        except OSError:
            return None, ".jpg"
        # 按源文件真实后缀编码，避免 webp/png 素材被误当 jpg 重编码。
        return content, (Path(path).suffix or ".jpg")

    def _save_clean_copy(
        self, content: bytes, *, stage: str, set_id: str, suffix: str, workspace_id: str
    ) -> str:
        """落一份未烧水印的干净副本（stage 加 `_clean` 后缀，不暴露在生成图路由上）。

        水印配置随时可能改动，只有保留原始像素才能在「立即应用水印」时重新合成
        而不叠层；落盘失败只导致该图无法重烧，不影响带水印成品图本身。
        """
        try:
            return self.assets.save_generated(
                content,
                stage=f"{stage}_clean",
                set_id=set_id,
                suffix=suffix,
                workspace_id=workspace_id,
            )
        except Exception:
            return ""

    def _publish_to_cos(
        self, content: bytes, *, stage: str, suffix: str, workspace_id: str
    ) -> str | None:
        """把一张成品图发布到 COS，返回可公网抓取的直链；COS 未配置/失败时返回 None。

        店小秘导入要求图片为公网可匿名抓取的 https 直链，本模块本地受管路由
        （带 token 查询参数）无法被抓取，因此必须在导出前发布到 COS。该函数不阻塞
        生图主流程：COS 不可用仅使后续导出缺图，不影响本模块出图。
        """
        if not content:
            return None
        try:
            processor = _make_media_processor()
            safe_suffix = suffix if suffix in {".png", ".jpeg", ".jpg", ".webp"} else ".jpg"
            digest = hashlib.sha256(content).hexdigest()
            media = GeneratedMedia(
                stage=stage,
                content=content,
                content_type=_content_type_for_suffix(safe_suffix),
                suffix=safe_suffix,
                provider="combo-cos",
                model="",
                reference_count=0,
                attempt_count=0,
            )
            return processor.upload_content_addressed_to_cos(
                media,
                namespace=str(workspace_id or "local"),
                content_hash=digest,
                collection="combo-kit",
            )
        except (MediaConfigurationError, MediaProcessingError, ValueError, TypeError):
            return None
        except Exception:
            return None

    def _prepare_references(
        self, items: list[dict[str, Any]], *, mode: str
    ) -> tuple[list[str], list[str]]:
        """按用户蒙版抠出参考图，返回 (参考图路径, 与之一一对应的机位标签)。

        抠图以「框选主体」为核心，而不是整张原图；单品多视角额外做尺度归一化，
        避免同一商品的各张原图构图不一导致跨图尺寸与弧度漂移。
        机位标签（单品多视角才有）是后续「按角色分配参考图」「指定输出视角」的唯一依据。
        抠图会跳过无路径的来源图，标签必须同步剔除，否则「第几张是哪个视角」会整体
        错位；标签数与参考图数对不上时返回空标签，调用方退化为不做视角筛选。
        """
        labels = _item_view_labels(items) if mode == "multiview" else []
        sources = [
            {
                "path": str(item.get("original_path") or "") or str(item.get("original_url") or ""),
                "points": _read_json(item.get("mask_json") or {}, {}).get("points"),
                "inverted": bool(item.get("mask_inverted")),
            }
            for item in items
        ]
        paired = [
            (source, labels[index] if index < len(labels) else "")
            for index, source in enumerate(sources)
            if str(source.get("path") or "").strip()
        ]
        values = crop_subject_references(
            [source for source, _ in paired], normalize=(mode == "multiview")
        )
        if not values:
            values = [str(item.get("original_path") or "") for item in items if str(item.get("original_path") or "").strip()]
        if not values:
            values = [str(item.get("original_url") or "") for item in items if str(item.get("original_url") or "").strip()]
        views = [label for _, label in paired] if len(paired) == len(values) else []
        return values, views

    def _publish_references(
        self, reference_values: list[str], *, workspace_id: str
    ) -> list[str]:
        """把参考图依次升级为托管网关可下载的公网 URL。

        组合套装的参考图来自本地抠图产物（tempfile）或本地上传原图（本地绝对
        路径），而 server-managed-wuyin 网关只能抓取公网 http(s) URL；直接提交
        本地路径会让请求 urls=[]，网关图生图任务因缺参考图而失败（等待后无图）。
        → 已配置 COS 时把每个本地参考图发布为公网直链；已是 http(s) URL 的保留。
        发布失败时保留原值（托管模式仍会失败，但至少不人为丢弃本地参考）。
        """
        published: list[str] = []
        for raw in reference_values:
            value = str(raw or "").strip()
            if not value:
                continue
            if _plausible_public_http_url(value):
                published.append(value)
                continue
            url = self._upload_reference_to_cos(value, workspace_id=workspace_id)
            published.append(url or value)
        return published

    def _upload_reference_to_cos(self, path: str, *, workspace_id: str) -> str | None:
        try:
            content = Path(path).read_bytes()
        except OSError:
            return None
        if not content:
            return None
        suffix = Path(path).suffix.lower() or ".jpg"
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        return self._publish_to_cos(
            content, stage="reference", suffix=suffix, workspace_id=workspace_id
        )

    def _direct_media_processor(self, freeze: dict[str, Any]) -> ProductImageProcessor:
        """构造一个固化 wuyin 直连密钥的图片处理器。

        组合套装生图在 ThreadPoolExecutor 子线程里调用
        ``processor.generate()``，而 ``server_ai_context`` 的 ContextVar 只在
        主线程可见，子线程读不到 granted_keys → ``resolve_ai_provider()``
        会退回 server-managed-wuyin 托管分支，进而因缺少 ``usage_id(image_grid)``
        报 "server-managed image usage is not reserved"。

        这里把冻结下发的中转 wuyin 密钥直接写入 config 的 image 段，使
        provider 在任何线程都解析为直连地址（https://api.wuyinkeji.com），
        不依赖线程上下文。
        """
        base_config = dict(_static_config())
        wuyin_key = str((freeze.get("keys") or {}).get("wuyin") or "").strip()
        if not wuyin_key:
            # 无直连密钥：退回默认（可能仍走托管，由下层给出可读报错）。
            return _make_media_processor()
        imports = self._import_provider_config_direct()
        base_url = imports["IMAGE_AI_BASE_URL"]
        image_section = dict(base_config.get("image") or {})
        image_section["base_url"] = base_url
        image_section["api_key"] = wuyin_key
        image_section["model"] = str(image_section.get("model") or "image_gpt")
        image_section["reference_model"] = str(image_section.get("reference_model") or image_section["model"])
        image_section["image_models"] = [str(image_section.get("model") or "image_gpt")]
        base_config["image"] = image_section
        base_config["backup_image"] = {}
        # 直连时不再有任何托管 provider，避免 server_managed 分支干扰。
        return ProductImageProcessor(config_provider=lambda: dict(base_config))

    @staticmethod
    def _import_provider_config_direct() -> dict[str, str]:
        from ..product_processing.provider_config import IMAGE_AI_BASE_URL

        return {"IMAGE_AI_BASE_URL": IMAGE_AI_BASE_URL}

    def _settle_billing(self, billing_id: str, freeze: dict[str, Any], *, success: bool, actor: Any) -> None:
        try:
            self.billing.settle(actor, freeze, success=success)
            self.repository.update_billing(billing_id, {
                "status": "settled" if success else "released",
                "result_status": "success" if success else "no_return",
                "settled_at": _now(),
            })
        except Exception:
            self.repository.update_billing(billing_id, {
                "result_status": "settle_pending",
                "error_message": "settle deferred",
            })

    # ---- 预检 ----

    def create_preview(self, set_id: str, *, workspace_id: str, owner_user_id: str) -> dict[str, Any]:
        payload = self._preview_payload(set_id)
        self.repository.upsert_preview({
            "set_id": set_id,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "status": "pending",
            "payload_json": json.dumps(payload, ensure_ascii=False),
        })
        self.repository.update_set(set_id, {"status": "preview_pending", "stage": "preview"})
        return payload

    def review_preview(self, set_id: str, payload: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
        self._require_set(set_id)
        decision = str(payload.get("decision") or "reject")
        reason = str(payload.get("reason") or "")
        status = "passed" if decision == "pass" else "rejected"
        self.repository.upsert_preview({
            "set_id": set_id,
            "workspace_id": workspace_id,
            "status": status,
            "reject_reason": reason if status == "rejected" else "",
        })
        if status == "passed":
            # 注：combo_kit_sets 表无 finished_at 列（该字段仅存在于预览任务表），
            # 此处不写，避免落入 update_set 的 allowed 白名单被静默丢弃。
            self.repository.update_set(set_id, {"status": "completed", "stage": "completed"})
        else:
            self.repository.update_set(set_id, {"status": "draft", "stage": "set_info", "error_message": reason})
        return self.get_set(set_id)

    def _preview_payload(self, set_id: str) -> dict[str, Any]:
        base = self._require_set(set_id)
        return {
            "set": base,
            "items": self.repository.list_items(set_id),
            "prompt": self._prompt_or_none(set_id),
            "billing": self.repository.list_billing(set_id),
        }

    def _prompt_or_none(self, set_id: str) -> dict[str, Any]:
        try:
            return self.repository.get_prompt(set_id)
        except KeyError:
            return {}

    def _require_set(self, set_id: str) -> dict[str, Any]:
        try:
            return self.repository.get_set(set_id)
        except KeyError:
            raise ComboKitNotFound("组合套装不存在") from None

    # ---- 店小秘导出 ----

    def export_dianxiaomi(self, set_id: str) -> Any:
        """把一套已完成组合套装导出为店小秘导入 xlsx。

        导出前先「过图床」：把尚未发布 COS 公网直链的成品图补发一次（幂等），
        并回写到 image_results_json，保证表格里的图片是公网可抓取直链。
        缺必填字段（申报价/长宽高/重量/分类等）或成品图无法发布 COS 时，
        抛 ComboDianxiaomiExportError（由路由映射为 422）。
        """
        base = self._require_set(set_id)
        base = {**base, "image_results_json": self._ensure_images_published(set_id, base)}
        return build_combo_dianxiaomi_export(base)

    def _ensure_images_published(self, set_id: str, base: dict[str, Any]) -> list[dict[str, Any]]:
        """遍历所有成品图，缺 COS 公网直链的补发一次并回写，返回更新后的列表。

        幂等：已发布（public_url 非空）的保留原样；发布失败时保留原条目，交由
        导出校验报「需已发布到 COS」，避免静默出坏图。
        """
        entries = _read_json(base.get("image_results_json") or [], [])
        if not isinstance(entries, list):
            return entries
        workspace_id = str(base.get("workspace_id") or "local")
        result: list[dict[str, Any]] = []
        changed = False
        for entry in entries:
            if not isinstance(entry, dict):
                result.append(entry)
                continue
            if str(entry.get("public_url") or "").strip():
                result.append(entry)
                continue
            role = str(entry.get("role") or "")
            path = str(entry.get("path") or "")
            content = b""
            try:
                if path and "://" not in path:
                    content = Path(path).read_bytes()
            except OSError:
                content = b""
            suffix = Path(path).suffix.lower() if path else ".jpg"
            if not content:
                result.append(entry)
                continue
            url = self._publish_to_cos(
                content, stage=role or "generated", suffix=suffix or ".jpg", workspace_id=workspace_id
            )
            if not url:
                result.append(entry)
                continue
            updated = dict(entry)
            updated["public_url"] = url
            result.append(updated)
            changed = True
        if changed:
            self.repository.update_set(set_id, {"image_results_json": json.dumps(result, ensure_ascii=False)})
        return result


def _uuid() -> str:
    import uuid

    return uuid.uuid4().hex


def _default_sku_display(name: str, specs: list[Any]) -> str:
    members = [str(item) for item in specs if str(item).strip()]
    if not members:
        return name or ""
    return f"{name}({'/'.join(members)})"


def _read_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value)) if str(value).strip() else default
    except (ValueError, TypeError):
        return default


def _order_image_entries(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 IMAGE_ROLES 固定顺序对成品图条目排序（未知角色排在最后）。"""
    order = {str(spec["role"]): index for index, spec in enumerate(IMAGE_ROLES)}
    return sorted(entries, key=lambda entry: order.get(str(entry.get("role") or ""), len(order)))


def _item_view_labels(items: list[dict[str, Any]]) -> list[str]:
    """按上传顺序取每个 item 的拍摄机位标签（主体识别阶段写入 subject_parsed_json）。

    未识别出机位的 item 返回空串，保证返回值与 items 等长，供调用方按下标对齐参考图。
    """
    labels: list[str] = []
    for item in items:
        parsed = _read_json(item.get("subject_parsed_json") or {}, {})
        labels.append(str(parsed.get("view_label") or "") if isinstance(parsed, dict) else "")
    return labels


def _member_subjects(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按上传顺序返回解析出主体词的成员主体（套装每件成员各一条）。"""
    subjects: list[dict[str, Any]] = []
    for item in items:
        parsed = _read_json(item.get("subject_parsed_json") or {}, {})
        if isinstance(parsed, dict) and parsed.get("sellable_subject"):
            subjects.append(parsed)
    return subjects


def _primary_subject(items: list[dict[str, Any]]) -> str:
    """取用户标记的「主要商品」主体的英文名；未标记则返回空串。"""
    for item in items:
        if item.get("is_primary"):
            parsed = _read_json(item.get("subject_parsed_json") or {}, {})
            return str(parsed.get("sellable_subject") or item.get("subject_keywords") or "").strip()
    return ""


def text_context(freeze: dict[str, Any]):
    from ..product_processing.server_ai_proxy import server_ai_context

    keys = _granted_keys(freeze)
    token = str(freeze.get("token") or "")
    return server_ai_context(token, {}, granted_keys=keys, freeze_id=str(freeze.get("freeze_id") or ""))


def image_context(freeze: dict[str, Any]):
    from ..product_processing.server_ai_proxy import server_ai_context

    keys = _granted_keys(freeze)
    token = str(freeze.get("token") or "")
    return server_ai_context(token, {}, granted_keys=keys, freeze_id=str(freeze.get("freeze_id") or ""))


def _granted_keys(freeze: dict[str, Any]) -> dict[str, str]:
    keys = freeze.get("keys") or {}
    return {str(k): str(v) for k, v in keys.items() if str(v)}


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _content_type_for_suffix(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
    }.get((suffix or "").lower(), "image/jpeg")
