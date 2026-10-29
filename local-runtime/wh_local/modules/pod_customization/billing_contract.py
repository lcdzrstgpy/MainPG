from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, Sequence

from ...session import Actor
from .column_generation import (
    COLUMN_ROLES,
    ROLE_ATTEMPTS,
    column_group_from_call_id,
    column_image_call_id,
    column_role_from_call_id,
    column_style_from_call_id,
    column_title_call_id,
    group_style_indices,
    plan_style_groups,
    style_group_index,
    validate_style_count,
)


PodFeature = Literal["pod.title", "pod.image"]
PodCallStatus = Literal["success", "no_return"]

# 计费画像：字符串由远端计费服务解释，并据此决定每 link 的单价。
POD_BILLING_PROFILE_RANDOM = "pod_random_v1"
# 列式生成（4 款一组）使用服务端专属画像：unit_kind = group，item_count = link_count × 4。
POD_BILLING_PROFILE_RANDOM_GRID = "pod_random_grid_v1"
POD_BILLING_PROFILE_FISSION = "pod_fission_v1"
POD_BILLING_PROFILE_SEMI = "pod_semi_v1"
# 爆款复刻统一画像：不再区分「普通生成 / 高清直出」，也不区分四宫格与四图；
# 复刻固定每款直出四张单图，客户端只上报款数（link_count），由远端按「款」统一定价。
# 历史批次冻结/结算里仍可能留有 pod_replica_v1_a / pod_replica_v1_b（旧 A/B 两档），
# 仅作历史流水展示，不再由客户端上报。
POD_BILLING_PROFILE_REPLICA = "pod_replica_v1"

# v2 请求里的 module 字段（与账本 module 列同值）。
POD_MODULE_CUSTOM = "pod_custom"
POD_MODULE_SEMI = "pod_semi"
POD_MODULE_REPLICA = "pod_replica"
POD_MODULE_FISSION = "pod_fission"
# 零计费动作（纯 title scope：brief / composition / 识图 / 结构分析 …）单独归类。
POD_MODULE_ZERO = "pod_zero"

# 半定制使用的计费画像。
# 半定制按「组」上报：4 款 = 1 个 link，默认 32 积分/组（即 8 积分/款），实际单价由
# 服务端倍率配置决定，以 freeze 响应的 link_prices 为准。这与全定制按「款」计价
# （pod_random_v1，固定 45/款）是两套口径，必须走独立画像，否则远端会按 POD 通用单价
# 对「组」计价，单价直接翻 4 倍。
SEMI_BILLING_PROFILE = POD_BILLING_PROFILE_SEMI

# 半定制一组（一次速创四宫格生图调用）包含的款数，与远端声明保持一致。
# 冻结请求同时上报组数（link_count）与款数（item_count），远端据此校验
# ``item_count == link_count * SEMI_STYLES_PER_GROUP``，兜底「漏除 4、把款数当组数上报」
# 的接线错误（那会把冻结积分放大 4 倍）。
SEMI_STYLES_PER_GROUP = 4

# 结算单元 ID 从调用 id 里解析 ``:style:{n}:``；n 是历史 1 基序号，unit_id 统一 0 基。
_STYLE_MARKER = re.compile(r":style:(\d+):")

_PRODUCT_BATCH_FEATURES: tuple[tuple[PodFeature, str], ...] = (
    ("pod.title", "title"),
    ("pod.image", "four_grid"),
)

# 每个款式预留的标题调用次数。与 title_runtime.MAX_ATTEMPTS 保持一致；标题重生不额外计费。
TITLE_ATTEMPTS = 5

# 智能前置层一次生成预留的文本调用次数：与契约修复重试上限一致。
# 这些调用全部记为 pod.title —— 服务端对 POD 画像的纯 title scope 显式零计费。
# v2：带模板图时预留 1 条给批级美术指导，因此字段修复仍有 3 次（共 4 条）。
BRIEF_ATTEMPTS = 4

# 构图定制一次生成预留的文本调用次数：与 brief 同口径（纯 pod.title，零计费）。
COMPOSITION_ATTEMPTS = 3

# 复刻主图方案：一次视觉调用（含契约修复重试），与 brief/composition 同口径
# （纯 pod.title，零计费），但走同一条冻结 → 短期密钥 → 结算底座。
REPLICA_ANALYSIS_ATTEMPTS = 3
# 模板结构识图：一次视觉调用 + 一次契约修复调用（与复刻识图同口径，纯 title 零计费）。
STRUCTURE_ANALYSIS_ATTEMPTS = 2
# Fission A/B analysis is one provider call plus one contract repair call.
FISSION_ANALYSIS_ATTEMPTS = 2

# 复刻独立面板策略（independent_panels）：每款 4 个面板，每个面板预留 2 次图片调用
# （失败回溯预算），与 grid 每款 2 次尝试的口径一致。
REPLICA_PANEL_COUNT = 4
REPLICA_PANEL_ATTEMPTS = 2

# 裂变独立面板策略：每款 1 张主图 + 3 张伴生格，每格预留 2 次图片调用。
FISSION_PANEL_COUNT = 4
FISSION_PANEL_ATTEMPTS = 2


class PodBillingAuthorizationRequired(RuntimeError):
    """The durable billing action must pause until a fresh grant is issued."""


@dataclass(frozen=True)
class PodPlannedCall:
    call_id: str
    feature: PodFeature

    def payload(self) -> dict[str, str]:
        return {"call_id": self.call_id, "feature": self.feature}


@dataclass(frozen=True)
class PodCallOutcome:
    call_id: str
    feature: PodFeature
    status: PodCallStatus

    def payload(self) -> dict[str, str]:
        return {
            "call_id": self.call_id,
            "feature": self.feature,
            "status": self.status,
        }


@dataclass(frozen=True)
class PodCallPlan:
    idempotency_key: str
    calls: tuple[PodPlannedCall, ...]
    # 上报给远端计费服务的画像字符串（决定单价区间）。
    billing_profile: str = POD_BILLING_PROFILE_RANDOM
    # 半定制的交付款数（>0 即为半定制）。冻结与结算的「按组展开」口径只看它，
    # 与 billing_profile 解耦：这样才能在不改远端的前提下临时换用别的画像。
    semi_item_count: int = 0
    # 列式生成的交付款数（>0 即为两级批次）。按「组」上报：1 组 = 1 link，
    # item_count 仍是款数（= 组数 × 4），使用 pod_random_grid_v1 画像。
    column_style_count: int = 0
    # v2 账本字段：module 归类、结算单元类型与单元 ID（0 基，与 link_idx 一一对应）。
    # 单元 ID 在生成本次计划时确定并持久化；冻结、续跑、重试、结算全程复用。
    module: str = ""
    unit_kind: str = ""
    unit_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.unit_kind:
            object.__setattr__(
                self,
                "unit_kind",
                "group" if (self.semi_item_count or self.column_style_count) else "style",
            )
        if not self.module:
            object.__setattr__(self, "module", self._derive_module())
        if not self.unit_ids:
            object.__setattr__(self, "unit_ids", self._derive_unit_ids())

    def _derive_module(self) -> str:
        if self.semi_item_count or self.billing_profile == POD_BILLING_PROFILE_SEMI:
            return POD_MODULE_SEMI
        if self.billing_profile == POD_BILLING_PROFILE_FISSION:
            return POD_MODULE_FISSION
        if self.billing_profile.startswith("pod_replica"):
            return POD_MODULE_REPLICA
        # 纯文本调用（brief / composition / 识图 / 结构分析）服务端零计费。
        if self.calls and all(call.feature == "pod.title" for call in self.calls):
            return POD_MODULE_ZERO
        return POD_MODULE_CUSTOM

    def _derive_unit_ids(self) -> tuple[str, ...]:
        if not self.calls:
            return ()
        if self.column_style_count:
            groups = sorted(
                {
                    group_index
                    for call in self.calls
                    if (group_index := column_group_from_call_id(call.call_id)) is not None
                }
            )
            return tuple(f"group-{group_index - 1}" for group_index in groups)
        markers = sorted(
            {
                int(match.group(1))
                for call in self.calls
                if (match := _STYLE_MARKER.search(call.call_id)) is not None
            }
        )
        if not markers:
            # 无款级标记的计划（试用 / 单动作重试 / 纯文本）只有 1 个 link。
            return ("style-0",)
        prefix = "group" if self.semi_item_count else "style"
        return tuple(f"{prefix}-{marker - 1}" for marker in markers)

    @property
    def column_group_count(self) -> int:
        if not self.column_style_count:
            return 0
        return self.column_style_count // 4

    @property
    def semi_group_count(self) -> int:
        """半定制的「组」数（4 款一组），也是上报给远端的 link_count。

        冻结与结算都以「组」为单位：一组 4 款共享该组生图调用的成败，远端按 link
        计费。单价默认 32 积分/组，实际由服务端倍率配置决定，以 freeze 响应的
        link_prices 为准。非半定制计划、或款数不是 4 的倍数时返回 0。
        """
        if not self.semi_item_count or self.semi_item_count % SEMI_STYLES_PER_GROUP != 0:
            return 0
        return self.semi_item_count // SEMI_STYLES_PER_GROUP

    @classmethod
    def for_semi_batch(cls, batch_id: str, *, count: int) -> "PodCallPlan":
        """半定制初始批次：``count`` 为交付款数（4 的倍数），组数 = count / 4。

        生图调用仍按组下发（每组一次速创四宫格），计费按款展开
        （见 ``product_batch_freeze_payload`` / ``_semi_batch_settlement_payload``）。
        """
        if isinstance(count, bool) or not isinstance(count, int) or not SEMI_STYLES_PER_GROUP <= count <= 200:
            raise ValueError("semi count must be between 4 and 200")
        if count % SEMI_STYLES_PER_GROUP != 0:
            raise ValueError("semi count must be a multiple of 4")
        group_count = count // SEMI_STYLES_PER_GROUP
        calls = tuple(
            call
            for group_index in range(1, group_count + 1)
            for call in (
                PodPlannedCall(f"{batch_id}:style:{group_index}:image:1", "pod.image"),
                PodPlannedCall(f"{batch_id}:style:{group_index}:image:2", "pod.image"),
            )
        )
        return cls(
            idempotency_key=f"pod:semi-batch:{batch_id}:initial",
            calls=calls,
            billing_profile=SEMI_BILLING_PROFILE,
            semi_item_count=count,
        )

    @classmethod
    def for_semi_batch_resume(
        cls,
        batch_id: str,
        resume_id: str,
        *,
        image_style_indices: Sequence[int],
    ) -> "PodCallPlan":
        """半定制「继续」：只为剩余组冻结，link_count = 剩余组数 × 4。

        调用 id 刻意沿用原批次的 ``:style:{组}`` 形式，worker 才能接回已持久化的生成状态。
        """
        indices = tuple(int(index) for index in image_style_indices)
        if not indices:
            raise ValueError("at least one remaining POD semi group is required")
        calls = tuple(
            PodPlannedCall(f"{batch_id}:style:{group_index}:image:{attempt}", "pod.image")
            for group_index in indices
            for attempt in (1, 2)
        )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:resume:{resume_id}",
            calls=calls,
            billing_profile=SEMI_BILLING_PROFILE,
            semi_item_count=len(indices) * SEMI_STYLES_PER_GROUP,
        )

    @classmethod
    def for_semi_style_retry(cls, action_id: str) -> "PodCallPlan":
        """半定制「整组重试」：只冻结该组的两次生图调用，按 4 款展开计费。

        不能复用 ``for_style_retry``：那个计划会带上 5 个标题调用，而半定制是纯图案、
        没有标题链路，worker 永远不会把它们记成结果，结算时 ``_validated_outcomes``
        的全量比对过不去，冻结积分也就退不回来。

        ``action_id`` 由调用方拼成 ``{batch}:style:{组}:retry:{uuid}``，组号靠其中的
        ``:style:{组}:`` 标记传递给 ``_product_batch_groups`` 与 worker 的
        ``_image_call_id``，因此这里不需要再单独传组号。
        """
        calls = tuple(
            PodPlannedCall(f"{action_id}:image:{attempt}", "pod.image")
            for attempt in (1, 2)
        )
        return cls(
            idempotency_key=f"pod:retry:{action_id}",
            calls=calls,
            billing_profile=SEMI_BILLING_PROFILE,
            semi_item_count=SEMI_STYLES_PER_GROUP,
        )

    @classmethod
    def for_batch(
        cls,
        batch_id: str,
        *,
        style_count: int,
        billing_profile: str = POD_BILLING_PROFILE_RANDOM,
    ) -> "PodCallPlan":
        if isinstance(style_count, bool) or not isinstance(style_count, int) or not 1 <= style_count <= 200:
            raise ValueError("style_count must be between 1 and 200")
        calls = tuple(
            call
            for style_index in range(1, style_count + 1)
            for call in (
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:1", "pod.image"),
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:2", "pod.image"),
                *(
                    PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                ),
            )
        )
        return cls(idempotency_key=f"pod:batch:{batch_id}:initial", calls=calls, billing_profile=billing_profile)

    @classmethod
    def for_column_batch(cls, batch_id: str, *, style_count: int) -> "PodCallPlan":
        """列式生成初始批次：4 款一组、每组 4 轮。

        调用形状（内部逐调用记账，远端按组汇总）：
        - 图片：``{batch}:group:{g}:role:{role}:image:{attempt}``（4 角色 × 2 次）；
        - 标题：``{batch}:group:{g}:style:{i}:title:{attempt}``（每款 5 次）。
        """
        validate_style_count(style_count)
        calls: list[PodPlannedCall] = []
        for group in plan_style_groups(style_count):
            for role in COLUMN_ROLES:
                calls.extend(
                    PodPlannedCall(
                        column_image_call_id(batch_id, group.group_index, role, attempt),
                        "pod.image",
                    )
                    for attempt in range(1, ROLE_ATTEMPTS + 1)
                )
            for style_index in group.style_indices:
                calls.extend(
                    PodPlannedCall(
                        column_title_call_id(
                            batch_id, group.group_index, style_index, attempt
                        ),
                        "pod.title",
                    )
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:initial",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_RANDOM_GRID,
            column_style_count=style_count,
        )

    @classmethod
    def for_column_batch_resume(
        cls,
        batch_id: str,
        resume_id: str,
        *,
        group_roles: Mapping[int, Sequence[str]],
        title_style_indices: Sequence[int] = (),
    ) -> "PodCallPlan":
        """暂停续跑：只冻结尚未完成的「组 × 角色」与未完成的标题。

        调用 id 沿用原批次的列式形状，worker 才能接回 group_sheets 的持久化状态。
        """
        roles_by_group = {
            int(group_index): tuple(role for role in roles if role in COLUMN_ROLES)
            for group_index, roles in group_roles.items()
            if roles
        }
        title_indices = tuple(int(index) for index in title_style_indices)
        resume_groups = set(roles_by_group) | {style_group_index(index) for index in title_indices}
        if not resume_groups:
            raise ValueError("at least one remaining POD column group is required")
        calls: list[PodPlannedCall] = []
        for group_index in sorted(resume_groups):
            for role in roles_by_group.get(group_index, ()):
                calls.extend(
                    PodPlannedCall(
                        column_image_call_id(batch_id, group_index, role, attempt),
                        "pod.image",
                    )
                    for attempt in range(1, ROLE_ATTEMPTS + 1)
                )
            styles = group_style_indices(group_index)
            for style_index in styles:
                if style_index not in title_indices:
                    continue
                calls.extend(
                    PodPlannedCall(
                        column_title_call_id(batch_id, group_index, style_index, attempt),
                        "pod.title",
                    )
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:resume:{resume_id}",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_RANDOM_GRID,
            # link_count = 本次续跑的组数；item_count 仍按 4 款/组上报。
            column_style_count=len(resume_groups) * 4,
        )

    @classmethod
    def for_replica_batch(
        cls, batch_id: str, *, style_count: int, generation_strategy: str = "grid"
    ) -> "PodCallPlan":
        """复刻批次冻结：确定每款预留的图片调用形状。

        复刻已统一为每款直出四张单图（``independent_panels``），不再有 grid 形态；
        ``grid`` 分支只为兼容历史批次读取，新批次不会走到。
        产品批次计费以 link 为单位（1 款 = 1 link），单款内部多出来的图片调用不会
        增加 link_count，只用于结算时逐调用对齐 outcomes；未使用的调用补 no_return。
        """
        if generation_strategy == "grid":
            return cls.for_batch(
                batch_id, style_count=style_count, billing_profile=POD_BILLING_PROFILE_REPLICA
            )
        if generation_strategy != "independent_panels":
            raise ValueError(f"unknown replica generation strategy: {generation_strategy}")
        if isinstance(style_count, bool) or not isinstance(style_count, int) or not 1 <= style_count <= 200:
            raise ValueError("style_count must be between 1 and 200")
        calls = tuple(
            call
            for style_index in range(1, style_count + 1)
            for call in (
                *(
                    PodPlannedCall(
                        f"{batch_id}:style:{style_index}:image:p{panel_index}:{attempt}",
                        "pod.image",
                    )
                    for panel_index in range(1, REPLICA_PANEL_COUNT + 1)
                    for attempt in range(1, REPLICA_PANEL_ATTEMPTS + 1)
                ),
                *(
                    PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                ),
            )
        )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:initial",
            calls=calls,
            billing_profile=POD_BILLING_PROFILE_REPLICA,
        )

    @staticmethod
    def _replica_panel_image_calls(prefix: str) -> tuple[PodPlannedCall, ...]:
        """独立面板策略下某款（或某次重试）的 4 面板 × 2 次尝试图片调用。"""
        return tuple(
            PodPlannedCall(f"{prefix}:image:p{panel_index}:{attempt}", "pod.image")
            for panel_index in range(1, REPLICA_PANEL_COUNT + 1)
            for attempt in range(1, REPLICA_PANEL_ATTEMPTS + 1)
        )

    @classmethod
    def for_replica_style_retry(
        cls, action_id: str, *, include_title: bool
    ) -> "PodCallPlan":
        """复刻「整款重试」的独立面板形态：4 面板 × 2 次尝试（+ 标题）。"""
        calls: list[PodPlannedCall] = list(cls._replica_panel_image_calls(action_id))
        if include_title:
            calls.extend(
                PodPlannedCall(f"{action_id}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:retry:{action_id}",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_REPLICA,
        )

    @classmethod
    def for_replica_batch_retry(
        cls,
        action_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool,
    ) -> "PodCallPlan":
        """复刻「批量失败重试」的独立面板形态；与 ``for_batch_retry`` 同语义。"""
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one failed style is required")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(cls._replica_panel_image_calls(f"{action_id}:style:{style_index}"))
            if include_title:
                calls.extend(
                    PodPlannedCall(
                        f"{action_id}:style:{style_index}:title:{attempt}", "pod.title"
                    )
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{action_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:retry:{action_id}",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_REPLICA,
        )

    @classmethod
    def for_replica_batch_resume(
        cls,
        batch_id: str,
        resume_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool,
    ) -> "PodCallPlan":
        """复刻「暂停续跑」的独立面板形态；调用 id 沿用原批次样式键以便接回状态。"""
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one remaining POD style is required")
        if set(image_indices) & set(title_indices):
            raise ValueError("a resumed style cannot be both image and title only")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(cls._replica_panel_image_calls(f"{batch_id}:style:{style_index}"))
            if include_title:
                calls.extend(
                    PodPlannedCall(
                        f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title"
                    )
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:resume:{resume_id}",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_REPLICA,
        )

    @staticmethod
    def _fission_panel_image_calls(prefix: str) -> tuple[PodPlannedCall, ...]:
        """裂变独立面板：每款 4 面板 × 2 次尝试。"""
        return tuple(
            PodPlannedCall(f"{prefix}:image:p{panel_index}:{attempt}", "pod.image")
            for panel_index in range(1, FISSION_PANEL_COUNT + 1)
            for attempt in range(1, FISSION_PANEL_ATTEMPTS + 1)
        )

    @classmethod
    def for_fission_batch(cls, batch_id: str, *, style_count: int) -> "PodCallPlan":
        """裂变批次冻结：每款 4 面板 × 2 次图片调用 + 标题调用，按款计 link。"""
        if isinstance(style_count, bool) or not isinstance(style_count, int) or not 1 <= style_count <= 200:
            raise ValueError("style_count must be between 1 and 200")
        calls = tuple(
            call
            for style_index in range(1, style_count + 1)
            for call in (
                *cls._fission_panel_image_calls(f"{batch_id}:style:{style_index}"),
                *(
                    PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                ),
            )
        )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:initial",
            calls=calls,
            billing_profile=POD_BILLING_PROFILE_FISSION,
        )

    @classmethod
    def for_fission_style_retry(cls, action_id: str, *, include_title: bool) -> "PodCallPlan":
        """裂变「整款重试」：4 面板 × 2 次（+ 标题）。"""
        calls: list[PodPlannedCall] = list(cls._fission_panel_image_calls(action_id))
        if include_title:
            calls.extend(
                PodPlannedCall(f"{action_id}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:retry:{action_id}",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_FISSION,
        )

    @classmethod
    def for_fission_batch_retry(
        cls,
        action_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool,
    ) -> "PodCallPlan":
        """裂变批量失败重试：按勾选款展开独立面板调用。"""
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one failed style is required")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(cls._fission_panel_image_calls(f"{action_id}:style:{style_index}"))
            if include_title:
                calls.extend(
                    PodPlannedCall(
                        f"{action_id}:style:{style_index}:title:{attempt}", "pod.title"
                    )
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{action_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:retry:{action_id}",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_FISSION,
        )

    @classmethod
    def for_fission_batch_resume(
        cls,
        batch_id: str,
        resume_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool,
    ) -> "PodCallPlan":
        """裂变暂停续跑：只冻结尚未完成的款，调用 id 沿用原批次形状。"""
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one remaining POD style is required")
        if set(image_indices) & set(title_indices):
            raise ValueError("a resumed style cannot be both image and title only")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(cls._fission_panel_image_calls(f"{batch_id}:style:{style_index}"))
            if include_title:
                calls.extend(
                    PodPlannedCall(
                        f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title"
                    )
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:resume:{resume_id}",
            calls=tuple(calls),
            billing_profile=POD_BILLING_PROFILE_FISSION,
        )

    @classmethod
    def for_retry(
        cls, action_id: str, *, feature: PodFeature, max_attempts: int = 1
    ) -> "PodCallPlan":
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        return cls(
            idempotency_key=f"pod:retry:{action_id}",
            calls=tuple(
                PodPlannedCall(f"{action_id}:attempt:{attempt}", feature)
                for attempt in range(1, max_attempts + 1)
            ),
        )

    @classmethod
    def for_trial(cls, trial_id: str, *, include_title: bool) -> "PodCallPlan":
        calls = [
            PodPlannedCall(f"{trial_id}:image:1", "pod.image"),
            PodPlannedCall(f"{trial_id}:image:2", "pod.image"),
        ]
        if include_title:
            calls.extend(
                PodPlannedCall(f"{trial_id}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(idempotency_key=f"pod:trial:{trial_id}", calls=tuple(calls))

    @classmethod
    def for_brief(cls, brief_id: str) -> "PodCallPlan":
        """智能前置层：一次模糊输入 → 结构化业务字段。

        只冻结 ``pod.title`` 文本调用。服务端对 POD 画像的纯 title scope 显式零计费
        （``billing.py`` 的 ``set(normalized_scope) == {"title"}`` 分支），所以该动作免费，
        但仍然复用冻结 → 发放短期密钥 → 结算的完整底座与幂等键。
        """
        return cls(
            idempotency_key=f"pod:brief:{brief_id}",
            calls=tuple(
                PodPlannedCall(f"{brief_id}:brief:{attempt}", "pod.title")
                for attempt in range(1, BRIEF_ATTEMPTS + 1)
            ),
        )

    @classmethod
    def for_composition(cls, composition_id: str) -> "PodCallPlan":
        """构图定制：一段大白话 → 四格画面指令。

        与 ``for_brief`` 同构：只冻结 ``pod.title`` 文本调用，服务端对纯 title scope
        显式零计费，因此该动作免费，但仍复用冻结 → 发放短期密钥 → 结算的完整底座。
        """
        return cls(
            idempotency_key=f"pod:composition:{composition_id}",
            calls=tuple(
                PodPlannedCall(f"{composition_id}:composition:{attempt}", "pod.title")
                for attempt in range(1, COMPOSITION_ATTEMPTS + 1)
            ),
        )

    @classmethod
    def for_replica_analysis(cls, analysis_id: str) -> "PodCallPlan":
        """复刻主图方案：一次视觉调用（含契约修复重试）。

        与 ``for_brief`` 同构：只冻结 ``pod.title``，服务端对纯 title scope 零计费。
        """
        return cls(
            idempotency_key=f"pod:replica-analysis:{analysis_id}",
            calls=tuple(
                PodPlannedCall(f"{analysis_id}:analysis:{attempt}", "pod.title")
                for attempt in range(1, REPLICA_ANALYSIS_ATTEMPTS + 1)
            ),
        )

    @classmethod
    def for_structure_analysis(cls, analysis_id: str) -> "PodCallPlan":
        """模板结构识图：一次视觉调用（含一次契约修复）。

        与 ``for_replica_analysis`` 同构：只冻结 ``pod.title``，服务端对纯 title
        scope 零计费，但仍复用冻结 → 短期密钥 → 结算底座与幂等键。
        """
        if not str(analysis_id).strip():
            raise ValueError("analysis_id is required")
        return cls(
            idempotency_key=f"pod:structure-analysis:{analysis_id}",
            calls=tuple(
                PodPlannedCall(f"{analysis_id}:structure:{attempt}", "pod.title")
                for attempt in range(1, STRUCTURE_ANALYSIS_ATTEMPTS + 1)
            ),
        )

    @classmethod
    def for_fission_analysis(cls, analysis_id: str) -> "PodCallPlan":
        """联合 A/B 分析：预留首次分析与一次契约修复调用。"""
        if not str(analysis_id).strip():
            raise ValueError("analysis_id is required")
        return cls(
            idempotency_key=f"pod:fission-analysis:{analysis_id}",
            calls=tuple(
                PodPlannedCall(f"{analysis_id}:analysis:{attempt}", "pod.title")
                for attempt in range(1, FISSION_ANALYSIS_ATTEMPTS + 1)
            ),
        )

    @classmethod
    def for_style_retry(
        cls,
        action_id: str,
        *,
        include_title: bool,
        billing_profile: str = POD_BILLING_PROFILE_RANDOM,
    ) -> "PodCallPlan":
        calls = [
            PodPlannedCall(f"{action_id}:image:1", "pod.image"),
            PodPlannedCall(f"{action_id}:image:2", "pod.image"),
        ]
        if include_title:
            calls.extend(
                PodPlannedCall(f"{action_id}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(idempotency_key=f"pod:retry:{action_id}", calls=tuple(calls), billing_profile=billing_profile)

    @classmethod
    def for_batch_retry(
        cls,
        action_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool,
        billing_profile: str = POD_BILLING_PROFILE_RANDOM,
    ) -> "PodCallPlan":
        """Build one durable plan for selected failed POD styles.

        Image retries keep the existing two-attempt grid allowance and then
        regenerate their listing title.  Title-only retries reserve title calls
        without re-running image generation.
        """
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one failed style is required")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(
                (
                    PodPlannedCall(f"{action_id}:style:{style_index}:image:1", "pod.image"),
                    PodPlannedCall(f"{action_id}:style:{style_index}:image:2", "pod.image"),
                )
            )
            if include_title:
                calls.extend(
                    PodPlannedCall(f"{action_id}:style:{style_index}:title:{attempt}", "pod.title")
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{action_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(idempotency_key=f"pod:retry:{action_id}", calls=tuple(calls), billing_profile=billing_profile)

    @classmethod
    def for_batch_resume(
        cls,
        batch_id: str,
        resume_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool = False,
        billing_profile: str = POD_BILLING_PROFILE_RANDOM,
    ) -> "PodCallPlan":
        """Freeze only work that remains after a previously settled pause.

        Call identifiers intentionally remain tied to the original batch and
        style so the worker can resume its persisted generation state, while
        the freeze idempotency key identifies this distinct resume action.
        """
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one remaining POD style is required")
        if set(image_indices) & set(title_indices):
            raise ValueError("a resumed style cannot be both image and title only")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:{attempt}", "pod.image")
                for attempt in (1, 2)
            )
            # A style whose four images are being regenerated also regenerates
            # its title (submitted by _process_style_grids when its lifestyle
            # panel publishes). Reserve its own title calls so the worker never
            # borrows a different style's planned title calls, which would leave
            # that other style's title stuck non-terminal.
            if include_title:
                calls.extend(
                    PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:resume:{resume_id}",
            calls=tuple(calls),
            billing_profile=billing_profile,
        )

    def freeze_payload(self, *, encrypted_session_key: str) -> dict[str, object]:
        return {
            "idempotency_key": self.idempotency_key,
            "title_call_count": sum(call.feature == "pod.title" for call in self.calls),
            "image_call_count": sum(call.feature == "pod.image" for call in self.calls),
            "calls": [call.payload() for call in self.calls],
            "encrypted_session_key": str(encrypted_session_key),
        }

    def product_batch_freeze_payload(self) -> dict[str, object]:
        """Project POD work onto the existing product-processing batch ledger.

        One POD style is one billed product link. Provider retries remain local
        attempts and do not create additional billable subitems.
        """
        if self.column_style_count:
            # 列式生成按「二级批次（4 款）」上报：1 组 = 1 link，item_count 报款数，
            # 沿用 pod_random_v1 的单价配置（不新增画像、不改远端倍率）。
            present = {call.feature for call in self.calls}
            return {
                "idempotency_key": self.idempotency_key,
                "link_count": self.column_group_count,
                "item_count": self.column_style_count,
                "scope": [remote for pod, remote in _PRODUCT_BATCH_FEATURES if pod in present],
                "billing_profile": self.billing_profile,
            }
        if self.semi_item_count:
            # 半定制按「组」冻结：一组 4 款 = 1 个 link（默认 32 积分/组，即 8 积分/款，
            # 实际单价由服务端倍率配置决定，以 freeze 响应的 link_prices 为准）。
            # 同时上报款数 item_count：供服务端校验 item_count == link_count × 4，
            # 兜底「漏除 4、把款数当组数上报」的接线错误（老服务端忽略该字段）。
            return {
                "idempotency_key": self.idempotency_key,
                "link_count": self.semi_group_count,
                "item_count": self.semi_item_count,
                "scope": ["four_grid"],
                "billing_profile": self.billing_profile,
            }
        groups = self._product_batch_groups()
        present = {call.feature for group in groups for call in group}
        return {
            "idempotency_key": self.idempotency_key,
            "link_count": len(groups),
            "scope": [remote for pod, remote in _PRODUCT_BATCH_FEATURES if pod in present],
            "billing_profile": self.billing_profile,
        }

    def product_batch_settlement_payload(
        self,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, object]:
        """Fold provider attempts into product-processing subitem outcomes."""
        # 半定制按「款」展开（口径看 semi_item_count，不看 profile 字符串）。
        if self.semi_item_count:
            return self._semi_batch_settlement_payload(outcomes)
        if self.column_style_count:
            return self._column_batch_settlement_payload(outcomes)
        outcome_by_call = self._validated_outcomes(outcomes)
        groups = self._product_batch_groups()
        # 每条 link 的子项必须恰好覆盖冻结 scope（billing.py 逐 link 校验）。
        # 混合批次（图片款+标题款混跑/混重试/混续跑）里没跑到的子项补 no_return：
        # 否则标题款只报 title 子项被 400 拒绝，冻结积分永久卡死。
        # 补的子项不影响扣费判定之外的行为——title-only 链接 four_grid=no_return
        # 自然走退款路径（title-only 重试结算后免费，语义正确）。
        present = {call.feature for group in groups for call in group}
        scope_products = {product for pod, product in _PRODUCT_BATCH_FEATURES if pod in present}
        items: list[dict[str, object]] = []
        for link_idx, group in enumerate(groups, start=1):
            subitems: list[dict[str, str]] = []
            for pod_feature, product_feature in _PRODUCT_BATCH_FEATURES:
                if product_feature not in scope_products:
                    continue
                matching = [call for call in group if call.feature == pod_feature]
                if not matching:
                    subitems.append({"feature": product_feature, "status": "no_return"})
                    continue
                succeeded = any(
                    outcome_by_call[(call.call_id, call.feature)] == "success"
                    for call in matching
                )
                subitems.append(
                    {
                        "feature": product_feature,
                        "status": "success" if succeeded else "no_return",
                    }
                )
            items.append({"link_idx": link_idx, "subitems": subitems})
        return {"items": items}

    def _column_batch_settlement_payload(
        self,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, object]:
        """列式生成结算：按「组」折叠成一条 link，两个 subitem。

        - ``four_grid``：该组第 1 轮（lifestyle 角色）任意一次尝试成功即 success；
          细节 A / 细节 B / 素材失败不影响计费（方案 D20）。
        - ``title``：该组 4 个款都有至少一次标题成功才 success。
        - 续跑只冻结部分角色时，未出现在本计划里的子项按 no_return 上报，
          与冻结 scope 保持一致（远端逐 link 校验子项覆盖）。
        """
        if not self.column_group_count:
            raise ValueError("column settlement requires a valid column_style_count")
        outcome_by_call = self._validated_outcomes(outcomes)
        groups: dict[int, list[PodPlannedCall]] = {}
        for call in self.calls:
            group_index = column_group_from_call_id(call.call_id)
            if group_index is None:
                raise ValueError(f"column plan contains a non-column call id: {call.call_id}")
            groups.setdefault(group_index, []).append(call)
        present = {call.feature for group in groups.values() for call in group}
        scope_products = {
            product for pod, product in _PRODUCT_BATCH_FEATURES if pod in present
        }
        items: list[dict[str, object]] = []
        for group_index in sorted(groups):
            group = groups[group_index]
            subitems: list[dict[str, str]] = []
            for pod_feature, product_feature in _PRODUCT_BATCH_FEATURES:
                if product_feature not in scope_products:
                    continue
                if pod_feature == "pod.image":
                    succeeded = any(
                        call.feature == pod_feature
                        and column_role_from_call_id(call.call_id) == "lifestyle"
                        and outcome_by_call[(call.call_id, call.feature)] == "success"
                        for call in group
                    )
                else:
                    succeeded = self._column_group_titles_succeeded(group, outcome_by_call)
                subitems.append(
                    {
                        "feature": product_feature,
                        "status": "success" if succeeded else "no_return",
                    }
                )
            items.append({"link_idx": len(items) + 1, "subitems": subitems})
        if len(items) != self.column_group_count:
            raise ValueError(
                "column settlement link count does not match the frozen group count"
            )
        return {"items": items}

    @staticmethod
    def _column_group_titles_succeeded(
        group: Sequence[PodPlannedCall],
        outcome_by_call: dict[tuple[str, PodFeature], PodCallStatus],
    ) -> bool:
        title_calls = [call for call in group if call.feature == "pod.title"]
        if not title_calls:
            return False
        by_style: dict[int, list[PodPlannedCall]] = {}
        for call in title_calls:
            style_index = column_style_from_call_id(call.call_id)
            if style_index is None:
                return False
            by_style.setdefault(style_index, []).append(call)
        if len(by_style) != 4:
            return False
        return all(
            any(outcome_by_call[(call.call_id, call.feature)] == "success" for call in calls)
            for calls in by_style.values()
        )

    def _semi_batch_settlement_payload(
        self,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, object]:
        """半定制结算：按「组」折叠成一条 subitem（一组 = 1 个 link，默认 32 积分/组，
        实际单价以 freeze 响应的 link_prices 为准）。

        组 g 的 4 个款共享该组速创调用的成败；上报给远端的 link 数与冻结时一致
        （= semi_group_count），否则服务端校验 item_results 条数 != link_count 会拒绝。
        组号从调用 id 实际解析（而不是假定 1..N），这样「继续」只跑部分组时也正确。
        """
        if not self.semi_group_count:
            raise ValueError("semi settlement requires a valid semi_item_count")
        outcome_by_call = self._validated_outcomes(outcomes)
        items: list[dict[str, object]] = []
        for group in self._product_batch_groups():
            image_calls = [call for call in group if call.feature == "pod.image"]
            succeeded = any(
                outcome_by_call[(call.call_id, call.feature)] == "success"
                for call in image_calls
            )
            status = "success" if succeeded else "no_return"
            items.append({
                "link_idx": len(items) + 1,
                "subitems": [{"feature": "four_grid", "status": status}],
            })
        if len(items) != self.semi_group_count:
            raise ValueError(
                "semi settlement link count does not match the frozen item count"
            )
        return {"items": items}

    def settlement_payload(
        self,
        freeze_id: str,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, object]:
        expected = {(call.call_id, call.feature) for call in self.calls}
        actual = [(outcome.call_id, outcome.feature) for outcome in outcomes]
        if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("settlement outcomes must match the frozen POD call plan exactly")
        return {
            "freeze_id": str(freeze_id),
            "items": [outcome.payload() for outcome in outcomes],
        }

    def _product_batch_groups(self) -> tuple[tuple[PodPlannedCall, ...], ...]:
        groups: dict[int, list[PodPlannedCall]] = {}
        for call in self.calls:
            group_id = 1
            marker = ":style:"
            if marker in call.call_id:
                style_suffix = call.call_id.rsplit(marker, 1)[1]
                raw_style_id = style_suffix.split(":", 1)[0]
                if raw_style_id.isdigit():
                    group_id = int(raw_style_id)
            groups.setdefault(group_id, []).append(call)
        return tuple(tuple(groups[group_id]) for group_id in sorted(groups))

    def _validated_outcomes(
        self,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[tuple[str, PodFeature], PodCallStatus]:
        expected = {(call.call_id, call.feature) for call in self.calls}
        actual = [(outcome.call_id, outcome.feature) for outcome in outcomes]
        if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("settlement outcomes must match the frozen POD call plan exactly")
        return {
            (outcome.call_id, outcome.feature): outcome.status
            for outcome in outcomes
        }


@dataclass(frozen=True)
class PodExecutionGrant:
    freeze_id: str
    rule_version: int
    expires_at: str
    provider_keys: Mapping[str, str] = field(repr=False)
    remote_token: str = field(default="", repr=False)
    # 冻结回执里没有任何可用密钥：冻结本身仍成立，缺密钥交给执行阶段。
    keys_missing: bool = False
    # v2 冻结快照（金额 / 单价 / 单元 ID / settle_deadline …），v1 路径为空。
    snapshot: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_keys",
            MappingProxyType(
                {
                    str(provider): str(value)
                    for provider, value in self.provider_keys.items()
                    if str(value)
                }
            ),
        )

    def provider_key(self, provider: str) -> str:
        try:
            expires_at = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return ""
        if expires_at <= datetime.now(timezone.utc):
            return ""
        return str(self.provider_keys.get(str(provider)) or "")


class PodBillingCoordinator(Protocol):
    """Injected adapter over the remote POD freeze/settle/regrant contract.

    The adapter owns encrypted-session-key creation and grant-envelope
    decryption.  This module receives plaintext grants only in memory.
    """

    def freeze(self, actor: Actor, plan: PodCallPlan) -> PodExecutionGrant: ...

    def settle(
        self,
        actor: Actor,
        grant: PodExecutionGrant,
        plan: PodCallPlan,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, Any] | None: ...

    def regrant(self, actor: Actor, freeze_id: str) -> PodExecutionGrant: ...
