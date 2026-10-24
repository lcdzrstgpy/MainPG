"""把模板的结构视图集装配成生图参考图。

全定制的参考图顺序固定为 ``primary → side → top``（主图永远第一：生图模型对首图
权重最高）。``dimension`` 只参与上传时的结构识图，**不下发给生图模型**，避免把
尺寸线、箭头与彩色标注底带进成品图。

按平台分流：第一版只保证豆包 Seedream 的多参考图；速创命中时退回单主图
（调用方据 ``model_supports_multi_view`` 判断），行为与现状一致。
"""

from __future__ import annotations

from typing import Any

from .runtime_contracts import ListingReferenceImage
from .structure_analysis import GENERATION_VIEW_ROLES, MAX_GENERATION_VIEWS


def load_generation_references(
    *,
    repository: Any,
    assets: Any,
    template_id: str,
    workspace_id: str,
    owner_user_id: str,
    primary_content: bytes,
    primary_content_type: str,
) -> tuple[ListingReferenceImage, ...]:
    """返回有序参考图元组；只有主图时也返回单元素（调用方可据此回退）。

    视图行来自 ``pod_customization_template_views``；读取失败（文件被清理）
    时跳过该视图，但绝不丢弃主图。
    """
    if not primary_content:
        return ()
    references = [
        ListingReferenceImage(
            role="product_primary",
            content=primary_content,
            content_type=primary_content_type,
        )
    ]
    rows = repository.list_template_views(template_id, workspace_id, owner_user_id)
    for row in rows:
        role = str(row.get("role") or "")
        if role not in GENERATION_VIEW_ROLES or role == "primary":
            continue
        if len(references) >= MAX_GENERATION_VIEWS:
            break
        try:
            content = assets.read(str(row["relative_path"]))
        except (FileNotFoundError, OSError, ValueError):
            continue
        references.append(
            ListingReferenceImage(
                role=f"product_{role}",
                content=content,
                content_type=str(row.get("content_type") or "image/png"),
            )
        )
    return tuple(references)
