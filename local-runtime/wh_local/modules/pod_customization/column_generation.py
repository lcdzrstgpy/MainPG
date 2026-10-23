"""列式生成（两级批次）的纯函数层。

这一层只做「组怎么分、轮次怎么排、调用 id 怎么拼」这类确定性计算，不碰数据库、
不发请求，便于在 worker / repository / billing 之间共享同一套口径，也便于单测。

两级批次（方案 §3）：
- L1 批次：定款数、整批风格、计费；
- L2 组：固定 4 款，内部 4 轮调用 —— 第 1 轮出 4 款主图，第 2/3/4 轮以这 4 张主图为锚，
  分别出 4 款细节 A / 细节 B / 素材图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .contracts import (
    COLUMN_STYLES_PER_GROUP,
    COMPOSITION_PANEL_KEYS,
    MAX_COLUMN_STYLE_COUNT,
    MIN_COLUMN_STYLE_COUNT,
)

# 轮次角色：顺序即调用顺序，也固定了 panel_1..panel_4 与轮次的对应关系。
# 标识符沿用历史值（lifestyle=主图、hero=素材），避免历史批次错标。
COLUMN_ROLES = ("lifestyle", "detail_a", "detail_b", "hero")
PRIMARY_ROLE = COLUMN_ROLES[0]
DERIVED_ROLES = COLUMN_ROLES[1:]
# 每个角色最多两次尝试（与 legacy 的 attempt 1/2 口径一致）。
ROLE_ATTEMPTS = 2

ROLE_PANEL_KEY = {
    "lifestyle": COMPOSITION_PANEL_KEYS[0],
    "detail_a": COMPOSITION_PANEL_KEYS[1],
    "detail_b": COMPOSITION_PANEL_KEYS[2],
    "hero": COMPOSITION_PANEL_KEYS[3],
}
ROLE_LABELS = {
    "lifestyle": "主图",
    "detail_a": "细节图 A",
    "detail_b": "细节图 B",
    "hero": "素材图",
}
# 组状态：执行单元的状态机；batch 状态仍由 style_grid_results 的计数汇总。
GROUP_STATUS_QUEUED = "queued"
GROUP_STATUS_GENERATING_PRIMARY = "generating_primary"
GROUP_STATUS_GENERATING_DERIVED = "generating_derived"
GROUP_STATUS_COMPLETED = "completed"
GROUP_STATUS_PARTIAL_FAILURE = "partial_failure"
GROUP_STATUS_FAILED = "failed"
GROUP_STATUS_CANCELLED = "cancelled"
GROUP_STATUSES = (
    GROUP_STATUS_QUEUED,
    GROUP_STATUS_GENERATING_PRIMARY,
    GROUP_STATUS_GENERATING_DERIVED,
    GROUP_STATUS_COMPLETED,
    GROUP_STATUS_PARTIAL_FAILURE,
    GROUP_STATUS_FAILED,
    GROUP_STATUS_CANCELLED,
)
# 角色 sheet 状态。
SHEET_STATUS_QUEUED = "queued"
SHEET_STATUS_GENERATING = "generating"
SHEET_STATUS_COMPLETED = "completed"
SHEET_STATUS_FAILED = "failed"
SHEET_STATUSES = (
    SHEET_STATUS_QUEUED,
    SHEET_STATUS_GENERATING,
    SHEET_STATUS_COMPLETED,
    SHEET_STATUS_FAILED,
)

# 资产 kind：列式母图仍是一张 2×2 四宫格，拆出的面板仍是 direct_listing_panel。
SHEET_ASSET_KIND = "direct_listing_sheet"


@dataclass(frozen=True)
class ColumnGroupPlan:
    """一个二级批次（组）的静态定义：组号 + 它覆盖的 4 个款号。"""

    group_index: int
    style_indices: tuple[int, ...]

    @property
    def first_style_index(self) -> int:
        return self.style_indices[0]


def validate_style_count(style_count: int) -> int:
    """校验列式生成的款数：4 的正整数倍，4–200。"""
    if isinstance(style_count, bool) or not isinstance(style_count, int):
        raise ValueError("列式生成的款数必须是整数")
    if not MIN_COLUMN_STYLE_COUNT <= style_count <= MAX_COLUMN_STYLE_COUNT:
        raise ValueError("列式生成的款数必须在 4 到 200 之间")
    if style_count % COLUMN_STYLES_PER_GROUP != 0:
        raise ValueError("列式生成的款数必须是 4 的倍数")
    return style_count


def group_count_for(style_count: int) -> int:
    return validate_style_count(style_count) // COLUMN_STYLES_PER_GROUP


def group_style_indices(group_index: int) -> tuple[int, ...]:
    """组 ``group_index``（从 1 开始）覆盖的款号：4g-3 .. 4g。"""
    if group_index < 1:
        raise ValueError("group_index must be positive")
    base = (group_index - 1) * COLUMN_STYLES_PER_GROUP
    return tuple(base + offset for offset in range(1, COLUMN_STYLES_PER_GROUP + 1))


def style_group_index(style_index: int) -> int:
    if style_index < 1:
        raise ValueError("style_index must be positive")
    return (style_index - 1) // COLUMN_STYLES_PER_GROUP + 1


def style_quadrant_offset(style_index: int) -> int:
    """款在组内的象限序号（0=左上，1=右上，2=左下，3=右下）。"""
    if style_index < 1:
        raise ValueError("style_index must be positive")
    return (style_index - 1) % COLUMN_STYLES_PER_GROUP


def plan_style_groups(style_count: int) -> tuple[ColumnGroupPlan, ...]:
    count = validate_style_count(style_count)
    return tuple(
        ColumnGroupPlan(group_index=index, style_indices=group_style_indices(index))
        for index in range(1, count // COLUMN_STYLES_PER_GROUP + 1)
    )


def column_image_call_id(batch_id: str, group_index: int, role: str, attempt: int) -> str:
    if role not in COLUMN_ROLES:
        raise ValueError(f"unknown column role: {role}")
    if attempt not in range(1, ROLE_ATTEMPTS + 1):
        raise ValueError("attempt must be 1 or 2")
    return f"{batch_id}:group:{group_index}:role:{role}:image:{attempt}"


def column_title_call_id(
    batch_id: str, group_index: int, style_index: int, attempt: int
) -> str:
    return f"{batch_id}:group:{group_index}:style:{style_index}:title:{attempt}"


def column_group_from_call_id(call_id: str) -> int | None:
    """从调用 id 解析组号；非列式调用返回 None。"""
    marker = ":group:"
    if marker not in call_id:
        return None
    for part in call_id.rsplit(marker, 1)[1].split(":"):
        if part.isdigit():
            return int(part)
        break
    return None


def column_role_from_call_id(call_id: str) -> str:
    """从调用 id 解析轮次角色；非列式图片调用返回空串。"""
    marker = ":role:"
    if marker not in call_id:
        return ""
    tail = call_id.rsplit(marker, 1)[1]
    return tail.split(":", 1)[0]


def column_style_from_call_id(call_id: str) -> int | None:
    """从列式调用 id 解析款号（标题调用用 `:group:{g}:style:{i}:` 形状）。"""
    marker = ":style:"
    if marker not in call_id:
        return None
    tail = call_id.rsplit(marker, 1)[1]
    head = tail.split(":", 1)[0]
    return int(head) if head.isdigit() else None


def group_roles_to_generate(
    sheet_statuses: dict[str, str],
) -> tuple[str, ...]:
    """按 sheet 状态推导该组还需要跑的轮次（顺序 = 主图 → 细节 A → 细节 B → 素材）。

    - 主图未完成：只跑主图（派生轮依赖它，barrier）；
    - 主图已完成：返回仍未完成的派生轮；
    - 已失败的 sheet 不重跑（v1 不做用户重跑）。
    """
    primary_status = sheet_statuses.get(PRIMARY_ROLE, SHEET_STATUS_QUEUED)
    if primary_status != SHEET_STATUS_COMPLETED:
        return () if primary_status == SHEET_STATUS_FAILED else (PRIMARY_ROLE,)
    return tuple(
        role
        for role in DERIVED_ROLES
        if sheet_statuses.get(role, SHEET_STATUS_QUEUED) == SHEET_STATUS_QUEUED
    )


def group_roles_to_freeze(
    sheet_statuses: dict[str, str],
) -> tuple[str, ...]:
    """暂停续跑需要冻结的轮次。

    与「本轮要跑什么」不同：主图未完成时必须把 3 个派生轮也一并冻结——主图跑完后
    派生轮会在同一次续跑里立即发起，否则冻结计划里没有对应调用，计费会报
    「call is not in the frozen plan」。
    """
    primary_status = sheet_statuses.get(PRIMARY_ROLE, SHEET_STATUS_QUEUED)
    if primary_status == SHEET_STATUS_FAILED:
        return ()
    if primary_status == SHEET_STATUS_COMPLETED:
        return tuple(
            role
            for role in DERIVED_ROLES
            if sheet_statuses.get(role, SHEET_STATUS_QUEUED) == SHEET_STATUS_QUEUED
        )
    return COLUMN_ROLES


def completed_roles(sheet_statuses: dict[str, str]) -> tuple[str, ...]:
    return tuple(role for role in COLUMN_ROLES if sheet_statuses.get(role) == SHEET_STATUS_COMPLETED)


def merge_sheet_statuses(rows: Sequence[dict[str, str]]) -> dict[str, str]:
    return {str(row.get("role") or ""): str(row.get("status") or "") for row in rows}
