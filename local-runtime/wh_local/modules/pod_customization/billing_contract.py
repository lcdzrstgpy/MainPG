from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, Sequence

from ...session import Actor


PodFeature = Literal["pod.title", "pod.image"]
PodCallStatus = Literal["success", "no_return"]

# 计费画像：字符串由远端计费服务解释，并据此决定每 link 的随机单价区间。
POD_BILLING_PROFILE_RANDOM = "pod_random_v1"
POD_BILLING_PROFILE_SEMI = "pod_semi_v1"

# 半定制使用的计费画像。
# 半定制按「组」上报：4 款 = 1 个 link = 固定 32 积分（即 8 积分/款）。这与全定制
# 按「款」计价（pod_random_v1，40–50/款）是两套口径，必须走独立画像，否则远端会
# 按 POD 通用单价对「组」计价，单价直接翻 4 倍。
SEMI_BILLING_PROFILE = POD_BILLING_PROFILE_SEMI

_PRODUCT_BATCH_FEATURES: tuple[tuple[PodFeature, str], ...] = (
    ("pod.title", "title"),
    ("pod.image", "four_grid"),
)

# 每个款式预留的标题调用次数。与 title_runtime.MAX_ATTEMPTS 保持一致；标题重生不额外计费。
TITLE_ATTEMPTS = 5

# 智能前置层一次生成预留的文本调用次数：与契约修复重试上限一致。
# 这些调用全部记为 pod.title —— 服务端对 POD 画像的纯 title scope 显式零计费。
BRIEF_ATTEMPTS = 3


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

    @property
    def semi_group_count(self) -> int:
        """半定制的「组」数（4 款一组），也是上报给远端的 link_count。

        冻结与结算都以「组」为单位：一组 4 款共享该组生图调用的成败，远端按 link
        计费，一组固定 32 积分。非半定制计划、或款数不是 4 的倍数时返回 0。
        """
        if not self.semi_item_count or self.semi_item_count % 4 != 0:
            return 0
        return self.semi_item_count // 4

    @classmethod
    def for_semi_batch(cls, batch_id: str, *, count: int) -> "PodCallPlan":
        """半定制初始批次：``count`` 为交付款数（4 的倍数），组数 = count / 4。

        生图调用仍按组下发（每组一次速创四宫格），计费按款展开
        （见 ``product_batch_freeze_payload`` / ``_semi_batch_settlement_payload``）。
        """
        if isinstance(count, bool) or not isinstance(count, int) or not 4 <= count <= 200:
            raise ValueError("semi count must be between 4 and 200")
        if count % 4 != 0:
            raise ValueError("semi count must be a multiple of 4")
        group_count = count // 4
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
            semi_item_count=len(indices) * 4,
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
            semi_item_count=4,
        )

    @classmethod
    def for_batch(cls, batch_id: str, *, style_count: int) -> "PodCallPlan":
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
        return cls(idempotency_key=f"pod:batch:{batch_id}:initial", calls=calls)

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
    def for_style_retry(cls, action_id: str, *, include_title: bool) -> "PodCallPlan":
        calls = [
            PodPlannedCall(f"{action_id}:image:1", "pod.image"),
            PodPlannedCall(f"{action_id}:image:2", "pod.image"),
        ]
        if include_title:
            calls.extend(
                PodPlannedCall(f"{action_id}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(idempotency_key=f"pod:retry:{action_id}", calls=tuple(calls))

    @classmethod
    def for_batch_retry(
        cls,
        action_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool,
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
        return cls(idempotency_key=f"pod:retry:{action_id}", calls=tuple(calls))

    @classmethod
    def for_batch_resume(
        cls,
        batch_id: str,
        resume_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool = False,
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
        if self.semi_item_count:
            # 半定制按「组」冻结：一组 4 款 = 1 个 link（固定 32 积分，即 8 积分/款）。
            # 款数仍保留在 semi_item_count 里用于结算展开与界面展示，不上报。
            return {
                "idempotency_key": self.idempotency_key,
                "link_count": self.semi_group_count,
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

    def _semi_batch_settlement_payload(
        self,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, object]:
        """半定制结算：按「组」折叠成一条 subitem（一组 = 1 个 link = 固定 32 积分）。

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
    ) -> None: ...

    def regrant(self, actor: Actor, freeze_id: str) -> PodExecutionGrant: ...
