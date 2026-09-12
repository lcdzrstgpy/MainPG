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

# 只有这两种状态是「明确判定过」，其余一律视为未判定。
DECIDED_STATUSES = frozenset({STATUS_CLEAN, STATUS_UNAVAILABLE})


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


def is_usable_source(
    stored: Mapping[str, Any] | None,
    current_fingerprint: str,
) -> bool:
    """落库结论对当前图集是否仍成立，且判定为「可用规格原图」。"""
    if not stored:
        return False
    if bool(stored.get("scope_relaxed")):
        # 判定范围被兜底放宽，无法保证覆盖导出实际使用的图集。
        return False
    if str(stored.get("status") or "") != STATUS_CLEAN:
        return False
    if not current_fingerprint:
        return False
    return current_fingerprint == str(stored.get("fingerprint") or "")


def resolve_draft_usable(
    stored: Mapping[str, Any] | None,
    *,
    current_fingerprint: str | None,
    fingerprint_error: bool = False,
) -> dict[str, Any]:
    """把落库结论 + 当前图集指纹解析成导出侧可直接消费的判定结果。

    ``fingerprint_error=True`` 表示当前图集读不出来（媒体不可用），保守按未判定处理。
    """
    if not stored:
        return {"judged": False, "clean": False, "usable_source": False, "reason": "never_judged"}
    if bool(stored.get("scope_relaxed")):
        return {"judged": False, "clean": False, "usable_source": False, "reason": "scope_relaxed"}
    status = str(stored.get("status") or "")
    if status not in DECIDED_STATUSES:
        reason = "not_decided" if not status else status
        return {"judged": False, "clean": False, "usable_source": False, "reason": reason}
    if fingerprint_error:
        return {"judged": False, "clean": False, "usable_source": False, "reason": "media_unavailable"}
    if not current_fingerprint or current_fingerprint != str(stored.get("fingerprint") or ""):
        return {"judged": False, "clean": False, "usable_source": False, "reason": "fingerprint_stale"}
    clean = status == STATUS_CLEAN
    return {"judged": True, "clean": clean, "usable_source": clean, "reason": status}


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
        return list(sku_views), False
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
