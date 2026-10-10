from __future__ import annotations

from typing import Any


class ReplicaStyleSnapshotMissingError(RuntimeError):
    """复刻款快照缺失：明确失败，绝不回退首款数据。"""


def style_product_context(batch: dict[str, Any], style_index: int) -> dict[str, Any]:
    """返回 ``template``、``business_fields``、``listing_fields``。

    - replica：取该款冻结目标快照，``template`` 由目标资产元数据构造（只含该款白底图）；
    - 其他模式：返回既有批次上下文（template 快照 + 批次业务/上架字段）。

    本函数不做磁盘 I/O、不修改 ``batch``；复刻款快照缺失抛
    :class:`ReplicaStyleSnapshotMissingError`。
    """
    if batch.get("mode") != "replica":
        return {
            "template": batch.get("template"),
            "business_fields": batch.get("business_fields"),
            "listing_fields": batch.get("listing_fields"),
        }
    targets = batch.get("replica_targets_by_index") or {}
    target = targets.get(int(style_index))
    if target is None:
        raise ReplicaStyleSnapshotMissingError(
            f"POD 复刻款快照缺失：style_index={style_index}"
        )
    asset = target.get("asset") or {}
    template = {
        "snapshot_id": "",
        "template_id": str(batch.get("template_id") or ""),
        "workspace_id": str(batch.get("workspace_id") or ""),
        "owner_user_id": str(batch.get("owner_user_id") or ""),
        "version": 1,
        "name": str(asset.get("filename") or ""),
        "source": "personal",
        "asset_id": str(asset.get("asset_id") or ""),
        "width": int(asset.get("width") or 0),
        "height": int(asset.get("height") or 0),
        "created_at": str(batch.get("created_at") or ""),
    }
    return {
        "template": template,
        "business_fields": target.get("business_fields"),
        "listing_fields": target.get("listing_fields"),
    }