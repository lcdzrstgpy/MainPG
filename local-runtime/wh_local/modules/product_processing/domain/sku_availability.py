"""草稿池「SKU 规格图可用性判断」结论的存放结构、指纹与有效性校验。

判断在草稿池由用户手动触发，结论落库到 ``product_processing_drafts.sku_availability_json``。
结论的有效期不靠时间戳，而靠 ``fingerprint``：把参与检测图片的 ``content_hash``
按稳定顺序拼起来再 hash。这样：

- 图片重新物化但字节相同 → 指纹不变 → 结论仍然有效（省一次重检）；
- 图片被替换 / 增删 → 指纹变化 → 结论自动失效，回退到「未判定」。

导出侧的 ``auto`` 策略只认「明确判定过、判定干净且指纹有效」这一种情况为可用；
其余（不可用 / 未判定 / 超阈值跳过 / 口径放宽 / 指纹失效）一律维持保守行为，
确保自动化只可能让结果变好，不会变差。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

# 结论状态取值。
STATUS_CLEAN = "clean"
STATUS_UNAVAILABLE = "unavailable"
STATUS_SKIPPED = "skipped"
STATUS_MISSING = "missing"
STATUS_PENDING = "pending"
# 规格图已注册但还没物化完（预检页显示「等待同步」）。这不是结论：此刻检不出规格图只是
# 暂时现象，据部分图得出的结论也会在剩余图就绪后因指纹变化失效。落成暂缓状态后由
# 素材物化收尾自动补判，避免把链接提前判死、导出直接用商品主图替代规格原图。
STATUS_PENDING_SYNC = "pending_sync"

# 只有这两种状态是「明确判定过」，其余一律视为未判定。
DECIDED_STATUSES = frozenset({STATUS_CLEAN, STATUS_UNAVAILABLE})

# 店小秘要求「变种预览图必须为 1:1 尺寸」，而该列正是我们导出的 SKU 规格原图列。
# 判定宽高是否 1:1 的容差：允许 1% 的相对误差（最小 1px），避免把 1024x1023 这类
# 仅差一两像素的方图误判成非方图；来源图本质上不会有这种舍入误差。
SQUARE_TOLERANCE_RATIO = 0.01


def is_square_view(view: Mapping[str, Any]) -> bool:
    """该规格图宽高是否为 1:1。

    宽高缺失（0 / 非法值）时返回 True：无从判断不误报，避免把「尺寸读不出来」的图
    整批打成非方图、连带把这些链接全部回退主图。
    """
    try:
        width = int(view.get("width") or 0)
        height = int(view.get("height") or 0)
    except (TypeError, ValueError):
        return True
    if width <= 0 or height <= 0:
        return True
    return abs(width - height) <= max(1, round(max(width, height) * SQUARE_TOLERANCE_RATIO))


def fingerprint_of(sku_views: Sequence[Mapping[str, Any]]) -> str:
    """参与检测的规格图内容指纹（空图集返回空串）。

    取每张图的 ``content_hash``，按 (sku_id, slot_id, sort_order, asset_id) 稳定排序
    后拼接再 hash。拿不到内容哈希时退回 ``asset:<id>``，保证仍可比较——宁可保守失效，
    也不把「无从比较」误判为「有效」。
    """
    parts: list[str] = []
    for view in sorted(
        sku_views,
        key=lambda item: (
            str(item.get("sku_id") or ""),
            str(item.get("slot_id") or ""),
            int(item.get("sort_order") or 0),
            str(item.get("asset_id") or ""),
        ),
    ):
        content_hash = str(view.get("content_hash") or "").strip()
        if not content_hash:
            content_hash = f"asset:{view.get('asset_id') or ''}"
        parts.append(f"{view.get('sku_id') or ''}:{content_hash}")
    if not parts:
        return ""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def dump(payload: Mapping[str, Any] | None) -> str:
    """序列化结论；``None`` / 空字典表示清空（回到「从未判定」）。"""
    if not payload:
        return ""
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)


def load(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def resolve_draft_usable(
    stored: Mapping[str, Any] | None,
    *,
    current_fingerprint: str | None,
    fingerprint_error: bool = False,
) -> dict[str, Any]:
    """把落库结论 + 当前图集指纹解析成导出侧可直接消费的判定结果。

    ``fingerprint_error=True`` 表示当前图集读不出来（媒体不可用），保守按未判定处理。
    """
    judged, clean, reason = _resolve(stored, current_fingerprint=current_fingerprint, fingerprint_error=fingerprint_error)
    return {
        "judged": judged,
        "clean": clean,
        "usable_source": clean,
        "reason": reason,
        # 结论有效时一并给出「检出中文的 SKU」，供导出侧兜底剔除。
        "chinese_variant_keys": chinese_variant_keys(stored) if judged else [],
        # 结论有效时给出「规格原图不是 1:1」的 SKU，供预检页给出可照做的提示。
        "not_square_variant_keys": not_square_variant_keys(stored) if judged else [],
    }


def resolve_chinese_variant_keys(
    stored: Mapping[str, Any] | None,
    *,
    current_fingerprint: str | None,
    fingerprint_error: bool = False,
) -> list[str]:
    """导出侧兜底剔除：判定有效且检出中文的 SKU 变种导出键。

    与 ``resolve_draft_usable`` 共用有效性口径（未判定 / 口径放宽 / 指纹失效一律不生效），
    避免拿过期结论误删导出行。
    """
    judged, _clean, _reason = _resolve(
        stored, current_fingerprint=current_fingerprint, fingerprint_error=fingerprint_error,
    )
    return chinese_variant_keys(stored) if judged else []


def chinese_variant_keys(stored: Mapping[str, Any] | None) -> list[str]:
    """落库结论里「检出中文」的变种导出键（去重排序）。

    ``all_sku_chinese`` 表示该链接所有有规格图的 SKU 都含中文：此时不做 SKU 级剔除
    （否则整条商品会从导出表格里消失），按现状回退商品主图，因此返回空。
    """
    if not stored or bool(stored.get("all_sku_chinese")):
        return []
    raw = stored.get("chinese_variant_keys") or []
    if not isinstance(raw, (list, tuple)):
        return []
    return sorted({str(value).strip() for value in raw if str(value or "").strip()})


def not_square_variant_keys(stored: Mapping[str, Any] | None) -> list[str]:
    """落库结论里「规格原图不是 1:1」的变种导出键（去重排序）。"""
    if not stored:
        return []
    raw = stored.get("not_square_variant_keys") or []
    if not isinstance(raw, (list, tuple)):
        return []
    return sorted({str(value).strip() for value in raw if str(value or "").strip()})


def merge_excluded_variant_keys(
    overrides: dict[str, Any], keys: Sequence[str],
) -> dict[str, Any]:
    """把「检出中文的 SKU」并入 ``excluded_variant_keys``（与手工排除取并集）。

    就地改写并返回 ``overrides``；没有可并入的键时原样返回，避免把「未设置」写成显式空。
    """
    normalized = [str(key).strip() for key in keys if str(key or "").strip()]
    if not normalized:
        return overrides
    merged = [
        str(key).strip()
        for key in (overrides.get("excluded_variant_keys") or [])
        if str(key or "").strip()
    ]
    known = set(merged)
    for key in normalized:
        if key not in known:
            merged.append(key)
            known.add(key)
    overrides["excluded_variant_keys"] = merged
    return overrides


def _resolve(
    stored: Mapping[str, Any] | None,
    *,
    current_fingerprint: str | None,
    fingerprint_error: bool,
) -> tuple[bool, bool, str]:
    """结论有效性判定，返回 ``(是否明确判定且有效, 是否判定干净, reason)``。"""
    if not stored:
        return False, False, "never_judged"
    if bool(stored.get("scope_relaxed")):
        return False, False, "scope_relaxed"
    status = str(stored.get("status") or "")
    if status not in DECIDED_STATUSES:
        return False, False, "not_decided" if not status else status
    if fingerprint_error:
        return False, False, "media_unavailable"
    if not current_fingerprint or current_fingerprint != str(stored.get("fingerprint") or ""):
        return False, False, "fingerprint_stale"
    if status == STATUS_UNAVAILABLE:
        # 保留落库时的细化原因（not_square_image / chinese_detected / too_many_sku_images /
        # no_sku_image / text_check_failed），前端据此展示「非 1:1 需裁剪」等可执行提示；
        # 折叠成粗粒度 unavailable 会让这些提示与红标永不生效。
        detail = str(stored.get("reason") or "").strip()
        if detail:
            return True, False, detail
    return True, status == STATUS_CLEAN, status


def keep_active_sku_views(
    sku_views: Sequence[Mapping[str, Any]], raw: Mapping[str, Any] | None,
) -> tuple[list[Mapping[str, Any]], bool]:
    """只保留「当前仍保留在草稿里」的 SKU 对应的规格图绑定。

    草稿池删除 SKU 规格只改写 ``raw_payload.source_variant_records``，不会同步失效
    媒体绑定，直接用绑定计数会把已删除（甚至更早的历史残留）的规格图也算进去。这里按
    现存变种的 ``sku_id`` / ``spec_text`` 反查绑定；若现存变种完全没有可用标识，则退回
    不过滤，避免误伤正常草稿。

    返回 ``(过滤后的视图, 是否放宽口径)``。放宽口径意味着筛选形同虚设，结论不能保证
    覆盖导出实际使用的图集，调用方不应据此判定为「可用」。
    """
    records = (raw or {}).get("source_variant_records")
    if not isinstance(records, list):
        # 老批次没有该字段：无法按现存变种过滤，属放宽口径——不过滤但标记 relaxed，
        # 结论不能保证覆盖导出实际使用的图集，调用方不应据此判定为「可用」。
        return list(sku_views), True
    sku_ids: set[str] = set()
    labels: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        sku_id = str(record.get("sku_id") or record.get("source_sku_id") or "").strip()
        if sku_id:
            sku_ids.add(sku_id)
        label = str(record.get("spec_text") or "").strip()
        if not label:
            attributes = record.get("attributes")
            if isinstance(attributes, Mapping):
                label = " ".join(
                    str(value) for value in attributes.values()
                    if value is not None and str(value).strip()
                ).strip()
        if label:
            labels.add(label)
    if not sku_ids and not labels:
        return list(sku_views), True
    return [
        view for view in sku_views
        if str(view.get("sku_id") or "") in sku_ids
        or str(view.get("variant_label") or "") in labels
    ], False
