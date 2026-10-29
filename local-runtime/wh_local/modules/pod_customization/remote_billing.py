from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from ...config import billing_contract_version
from ...customer.contracts import (
    CustomerBillingPermissionError,
    CustomerBillingRemoteError,
)
from ...session import Actor
from .billing_contract import (
    POD_BILLING_PROFILE_RANDOM,
    POD_BILLING_PROFILE_SEMI,
    SEMI_STYLES_PER_GROUP,
    PodBillingCoordinator,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)


class PodBillingRemoteError(RuntimeError):
    """带机器码的计费失败，供 service / worker 按 code 分流终态。

    ``retryable`` 只对 429 / 5xx / 网络为真；4xx 业务拒绝一律不可重试。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = str(code or "")
        self.retryable = bool(retryable)


def use_billing_contract_v2() -> bool:
    """v2 开关：默认 v1；服务端 S1/S2 未上线前不得默认切 v2。"""
    return billing_contract_version() == "v2"


# v1 的 feature 名 → v2 的 feature 名（`four_grid` 在 v2 统一叫 `image`）。
_V2_FEATURE_NAMES = {"four_grid": "image"}


def build_v2_freeze_payload(plan: PodCallPlan) -> dict[str, Any]:
    """把四模块计划无损映射到 v2 冻结载荷。

    差异只在形状：``scope`` 数组 → 对象、列式/半定制的组信息不变、
    单元 ID 用计划里持久化的 0 基值。
    """
    base: Mapping[str, Any] = plan.product_batch_freeze_payload()
    raw_scope = base.get("scope") or ()
    scope = {str(item) for item in raw_scope} if isinstance(raw_scope, Sequence) else set()
    payload: dict[str, Any] = {
        "idempotency_key": plan.idempotency_key,
        "module": plan.module,
        "billing_profile": plan.billing_profile,
        "unit_kind": plan.unit_kind,
        "link_count": _int(base.get("link_count")),
        "scope": {"image": "four_grid" in scope, "title": "title" in scope},
        "unit_ids": list(plan.unit_ids),
    }
    if _int(base.get("item_count")):
        payload["item_count"] = _int(base.get("item_count"))
    return payload


def build_v2_settlement_payload(
    plan: PodCallPlan,
    outcomes: Sequence[PodCallOutcome],
) -> dict[str, Any]:
    """把逐调用结果折成 v2 的 ``links[]``：``link_idx`` 0 基、`four_grid` → `image`。"""
    folded: Mapping[str, Any] = plan.product_batch_settlement_payload(outcomes)
    links: list[dict[str, Any]] = []
    for item in folded.get("items", ()):
        link_idx = int(item["link_idx"])
        unit_id = (
            plan.unit_ids[link_idx - 1] if 0 < link_idx <= len(plan.unit_ids) else ""
        )
        links.append(
            {
                "link_idx": link_idx - 1,
                "unit_id": unit_id,
                "subitems": [
                    {
                        "feature": _V2_FEATURE_NAMES.get(
                            str(subitem.get("feature") or ""),
                            str(subitem.get("feature") or ""),
                        ),
                        "status": str(subitem.get("status") or "no_return"),
                    }
                    for subitem in item.get("subitems", ())
                ],
            }
        )
    return {"idempotency_key": plan.idempotency_key, "links": links}


def parse_v2_settlement_receipt(payload: Mapping[str, Any]) -> dict[str, Any]:
    """解析结算 / close 回执；金额只取服务端值，不做任何本地换算。"""
    if not isinstance(payload, Mapping):
        raise PodBillingRemoteError("POD billing service returned an invalid settlement receipt")
    nested = payload.get("settlement")
    receipt: Mapping[str, Any] = nested if isinstance(nested, Mapping) else payload
    links: list[dict[str, Any]] = []
    for index, link in enumerate(receipt.get("links") or ()):
        if not isinstance(link, Mapping):
            continue
        status = str(link.get("status") or "")
        links.append(
            {
                "link_idx": int(link.get("link_idx", index) or 0),
                "unit_id": str(link.get("unit_id") or ""),
                "status": status,
                "feature_status": dict(link.get("feature_status") or {})
                or ({"status": status} if status else {}),
                "charged_points": _number(link.get("charged_points")),
                "refunded_points": _number(link.get("refunded_points")),
            }
        )
    return {
        "settlement_id": str(receipt.get("settlement_id") or receipt.get("freeze_id") or ""),
        "source": str(receipt.get("source") or "settle"),
        "charged_points": _number(receipt.get("charged_points")),
        "refunded_points": _number(receipt.get("refunded_points")),
        "released_points": _optional_number(receipt.get("released_points")),
        "retained_points": _optional_number(receipt.get("retained_points")),
        "release_reason": str(receipt.get("release_reason") or ""),
        "rule_version": str(receipt.get("rule_version") or ""),
        "links": links,
        "raw": dict(receipt),
    }


def expected_points_for(
    plan: PodCallPlan,
    outcomes: Sequence[PodCallOutcome],
    link_prices: Sequence[float],
) -> float:
    """本地应扣 = 冻结回执的 ``link_prices`` × 适配层判定，只用于告警。"""
    try:
        folded: Mapping[str, Any] = plan.product_batch_settlement_payload(outcomes)
    except ValueError:
        return 0.0
    total = 0.0
    for item in folded.get("items", ()):
        index = int(item["link_idx"]) - 1
        price = float(link_prices[index]) if 0 <= index < len(link_prices) else 0.0
        if any(
            str(subitem.get("feature")) in {"four_grid", "image"}
            and str(subitem.get("status")) == "success"
            for subitem in item.get("subitems", ())
        ):
            total += price
    return round(total, 1)


def attach_freeze_snapshot(
    repository: Any,
    action_key: str,
    plan: PodCallPlan,
    grant: PodExecutionGrant,
    *,
    device_id: str = "",
) -> bool:
    """把冻结回执一次性回填到意图行（唯一实现，服务与启动对账共用）。"""
    snapshot: Mapping[str, Any] = getattr(grant, "snapshot", None) or {}
    return repository.attach_billing_freeze(
        action_key,
        freeze_id=grant.freeze_id,
        rule_version=grant.rule_version,
        grant_expires_at=grant.expires_at,
        frozen_points=snapshot.get("frozen_points"),
        link_prices=snapshot.get("link_prices") or (),
        unit_ids=snapshot.get("unit_ids") or plan.unit_ids,
        multiplier_percent=_int(snapshot.get("multiplier_percent"), 100),
        settle_deadline=str(snapshot.get("settle_deadline") or ""),
        freeze_raw=snapshot.get("raw") or {},
        module=plan.module,
        unit_kind=plan.unit_kind,
        billing_profile=plan.billing_profile,
        link_count=len(plan.unit_ids),
        item_count=_int(
            plan.semi_item_count or plan.column_style_count or plan.free_panel_count
        ),
        device_id=device_id,
    )


class RemotePodBillingCoordinator(PodBillingCoordinator):
    """Adapt the host remote-auth client to POD's in-memory billing contract."""

    def __init__(
        self,
        remote_client: Any,
        remote_token_resolver: Callable[[Actor], str],
    ) -> None:
        self._remote_client = remote_client
        self._remote_token_resolver = remote_token_resolver

    def freeze(self, actor: Actor, plan: PodCallPlan) -> PodExecutionGrant:
        remote_token = self._required_remote_token(actor)
        if use_billing_contract_v2():
            response = self._remote_client.pod_v2_freeze(
                remote_token,
                build_v2_freeze_payload(plan),
                idempotency_key=plan.idempotency_key,
            )
            freeze = self._freeze_record(response)
            status = str(freeze.get("status") or "")
            if status and status != "frozen":
                # 重放可能拿到已终态记录：只有 frozen 才能继续。
                raise PodBillingRemoteError(
                    f"POD freeze is not active (status={status})",
                    status_code=409,
                    code="freeze_not_active",
                    retryable=False,
                )
            return replace(
                self._grant(freeze, remote_token=remote_token),
                snapshot=_freeze_snapshot(freeze),
            )
        payload = plan.product_batch_freeze_payload()
        # 各 POD 模式按画像分流到自己的计费通道（v1 兼容路径）：
        # 半定制走 pod-semi（默认 32/组，实际单价由服务端倍率配置决定）；
        # 爆款裂变走 pod-fission（独立通道，按款计价，与全定制同价）；
        # 全定制/商品处理/复刻走通用 batch 接口。
        if plan.semi_item_count:
            response = self._remote_client.freeze_pod_semi_points(remote_token, payload)
        else:
            # 裂变与全定制共用同一个入口与画像命名空间（不再单独路由）。
            response = self._remote_client.freeze_batch_points(remote_token, payload)
        freeze = self._freeze_record(response)
        return self._grant(freeze, remote_token=remote_token)

    def settle(
        self,
        actor: Actor,
        grant: PodExecutionGrant,
        plan: PodCallPlan,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, Any] | None:
        del actor
        if use_billing_contract_v2():
            response = self._remote_client.pod_v2_settle(
                grant.remote_token,
                grant.freeze_id,
                build_v2_settlement_payload(plan, outcomes),
            )
            return parse_v2_settlement_receipt(response)
        payload = plan.product_batch_settlement_payload(outcomes)
        if plan.semi_item_count:
            self._remote_client.settle_pod_semi_points(
                grant.remote_token,
                grant.freeze_id,
                payload,
            )
        else:
            self._remote_client.settle_batch_points(
                grant.remote_token,
                grant.freeze_id,
                payload,
            )
        return None

    def close(
        self,
        actor: Actor,
        grant: PodExecutionGrant,
        plan: PodCallPlan,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, Any] | None:
        """用户主动放弃：v2 走 ``POST /close``；v1 降级为「全 no_return 结算」。"""
        if use_billing_contract_v2():
            response = self._remote_client.pod_v2_close(
                grant.remote_token,
                grant.freeze_id,
                build_v2_settlement_payload(plan, outcomes),
            )
            receipt = parse_v2_settlement_receipt(response)
            receipt["source"] = "close"
            return receipt
        no_return = tuple(
            PodCallOutcome(call.call_id, call.feature, "no_return") for call in plan.calls
        )
        return self.settle(actor, grant, plan, no_return)

    def freeze_status(self, actor: Actor, freeze_id: str) -> dict[str, Any]:
        """探活：返回远端冻结状态与金额字段（不做本地推算）。"""
        remote_token = self._required_remote_token(actor)
        if use_billing_contract_v2():
            response = self._remote_client.pod_v2_freeze_status(remote_token, freeze_id)
        else:
            response = self._remote_client.batch_freeze_status(remote_token, freeze_id)
        freeze = self._freeze_record(response)
        return {
            "freeze_id": str(freeze.get("freeze_id") or freeze_id),
            "status": str(freeze.get("status") or ""),
            "settle_deadline": str(freeze.get("settle_deadline") or ""),
            "expires_at": str(freeze.get("expires_at") or ""),
            "frozen_points": _optional_number(freeze.get("frozen_points")),
            "released_points": _optional_number(freeze.get("released_points")),
            "retained_points": _optional_number(freeze.get("retained_points")),
            "release_reason": str(freeze.get("release_reason") or ""),
            "raw": dict(freeze),
        }

    def regrant(self, actor: Actor, freeze_id: str) -> PodExecutionGrant:
        remote_token = self._required_remote_token(actor)
        if use_billing_contract_v2():
            # 先探活拿冻结元数据（freeze_id / expires_at / settle_deadline），
            # 再补领密钥；/keys 只保证返回密钥，不假定它回显冻结元数据。
            status = self._freeze_record(
                self._remote_client.pod_v2_freeze_status(remote_token, freeze_id)
            )
            key_source = self._freeze_record(
                self._remote_client.pod_v2_keys(remote_token, freeze_id, {})
            )
            merged = dict(status)
            merged["freeze_id"] = freeze_id
            if key_source.get("keys") is not None:
                merged["keys"] = key_source.get("keys")
            return replace(
                self._grant(merged, remote_token=remote_token),
                snapshot=_freeze_snapshot(status),
            )
        status_response = self._remote_client.batch_freeze_status(remote_token, freeze_id)
        status = self._freeze_record(status_response)
        if str(status.get("freeze_id") or "") != freeze_id:
            raise RuntimeError("POD billing service returned an invalid freeze status")
        try:
            link_count = max(1, int(status.get("link_count") or 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("POD billing service returned an invalid freeze status") from exc
        billing_profile = str(status.get("billing_profile") or POD_BILLING_PROFILE_RANDOM)
        payload = {
            "idempotency_key": freeze_id,
            "link_count": link_count,
            "scope": list(status.get("scope") or []),
            "billing_profile": billing_profile,
        }
        # 重新冻结也按画像分流：半定制走独立接口（补 item_count），裂变走独立接口，
        # 其余走通用接口，避免被通用接口重新按全定制单价计价。
        if billing_profile == POD_BILLING_PROFILE_SEMI:
            # 与初始冻结同口径：补款数（组数 × 4），供服务端校验 link_count/item_count。
            payload["item_count"] = link_count * SEMI_STYLES_PER_GROUP
            response = self._remote_client.freeze_pod_semi_points(remote_token, payload)
        else:
            response = self._remote_client.freeze_batch_points(remote_token, payload)
        freeze = self._freeze_record(response)
        return self._grant(freeze, remote_token=remote_token)

    def settlement_grant(
        self,
        actor: Actor,
        freeze_id: str,
        *,
        rule_version: int,
        expires_at: str,
    ) -> PodExecutionGrant:
        """Authenticate settlement without requiring an active provider-key grant."""
        remote_token = self._required_remote_token(actor)
        if use_billing_contract_v2():
            response = self._remote_client.pod_v2_freeze_status(remote_token, freeze_id)
        else:
            response = self._remote_client.batch_freeze_status(remote_token, freeze_id)
        freeze = response.get("freeze") if isinstance(response, Mapping) else None
        status = freeze if isinstance(freeze, Mapping) else response
        if not isinstance(status, Mapping) or str(status.get("freeze_id") or "") != freeze_id:
            raise RuntimeError("POD billing service returned an invalid freeze status")
        return PodExecutionGrant(
            freeze_id=freeze_id,
            rule_version=_rule_version_int(status.get("rule_version")) or rule_version,
            expires_at=str(status.get("expires_at") or status.get("settle_deadline") or expires_at),
            provider_keys={},
            remote_token=remote_token,
        )

    def open_freezes(self, actor: Actor, *, module: str = "") -> dict[str, Any]:
        """未结清列表（多设备对账用）；v1 兼容期没有该接口。"""
        if not use_billing_contract_v2():
            return {"freezes": [], "unavailable": True}
        remote_token = self._required_remote_token(actor)
        response = self._remote_client.pod_v2_open_freezes(
            remote_token, module=module
        )
        freezes = response.get("freezes") if isinstance(response, Mapping) else None
        return {"freezes": list(freezes or ()), "unavailable": False}

    def daily_report(
        self, actor: Actor, *, date: str, tz: str = "Asia/Shanghai"
    ) -> dict[str, Any]:
        """服务端日报；v1 兼容期没有该接口。"""
        if not use_billing_contract_v2():
            return {"unavailable": True}
        remote_token = self._required_remote_token(actor)
        return dict(self._remote_client.pod_v2_daily_report(remote_token, date=date, tz=tz))

    def daily_report_detail(
        self,
        actor: Actor,
        *,
        date: str,
        tz: str = "Asia/Shanghai",
        freeze_id: str = "",
    ) -> dict[str, Any]:
        if not use_billing_contract_v2():
            return {"unavailable": True}
        remote_token = self._required_remote_token(actor)
        return dict(
            self._remote_client.pod_v2_daily_report_detail(
                remote_token, date=date, tz=tz, freeze_id=freeze_id
            )
        )

    def _required_remote_token(self, actor: Actor) -> str:
        token = str(self._remote_token_resolver(actor) or "")
        if not token:
            raise CustomerBillingPermissionError()
        return token

    @staticmethod
    def _freeze_record(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        freeze = payload.get("freeze") if isinstance(payload, Mapping) else None
        record = freeze if isinstance(freeze, Mapping) else payload
        if not isinstance(record, Mapping):
            raise RuntimeError("POD billing service returned an invalid freeze")
        return record

    @staticmethod
    def _grant(payload: Mapping[str, Any], *, remote_token: str) -> PodExecutionGrant:
        raw_keys = payload.get("keys")
        provider_keys: dict[str, str] = {}
        key_expiries: list[str] = []
        if isinstance(raw_keys, Mapping):
            # v2 形状：provider → 数组（取第一个非空值）。
            for provider, value in raw_keys.items():
                api_key = _first_provider_key(value)
                if api_key:
                    provider_keys[str(provider)] = api_key
        elif isinstance(raw_keys, Sequence) and not isinstance(raw_keys, (str, bytes, bytearray)):
            # v1 形状：``[{provider, api_key, expires_at}, ...]``。
            for item in raw_keys:
                if not isinstance(item, Mapping):
                    continue
                provider = str(item.get("provider") or "")
                api_key = _first_provider_key(item.get("api_key"))
                if provider and api_key:
                    provider_keys[provider] = api_key
                expiry = str(item.get("expires_at") or "")
                if expiry:
                    key_expiries.append(expiry)
        freeze_id = str(payload.get("freeze_id") or "")
        expires_at = min(key_expiries) if key_expiries else str(
            payload.get("expires_at") or payload.get("settle_deadline") or ""
        )
        if not freeze_id or not expires_at:
            raise RuntimeError("POD billing service returned an invalid grant")
        # 空密钥不再抛错：服务端明确「密钥发不出来时冻结依然成功」，
        # 缺密钥交给执行阶段（PodBillingRun.start 会按 provider 判空）。
        return PodExecutionGrant(
            freeze_id=freeze_id,
            rule_version=_rule_version_int(payload.get("rule_version")),
            expires_at=expires_at,
            provider_keys=provider_keys,
            remote_token=remote_token,
            keys_missing=not provider_keys,
        )


def _freeze_snapshot(freeze: Mapping[str, Any]) -> dict[str, Any]:
    prices = freeze.get("link_prices")
    unit_ids = freeze.get("unit_ids")
    return {
        "freeze_id": str(freeze.get("freeze_id") or ""),
        "status": str(freeze.get("status") or ""),
        "frozen_points": _optional_number(freeze.get("frozen_points")),
        "link_prices": [
            _number(value)
            for value in (prices if isinstance(prices, Sequence) and not isinstance(prices, (str, bytes, bytearray)) else ())
        ],
        "unit_ids": [str(value) for value in (unit_ids if isinstance(unit_ids, Sequence) and not isinstance(unit_ids, (str, bytes, bytearray)) else ())],
        "multiplier_percent": _int(freeze.get("multiplier_percent"), 100),
        "settle_deadline": str(freeze.get("settle_deadline") or ""),
        "expires_at": str(freeze.get("expires_at") or ""),
        "billing_profile": str(freeze.get("billing_profile") or ""),
        "module": str(freeze.get("module") or ""),
        "unit_kind": str(freeze.get("unit_kind") or ""),
        "link_count": _int(freeze.get("link_count")),
        "item_count": _int(freeze.get("item_count")),
        "rule_version": str(freeze.get("rule_version") or ""),
        "raw": dict(freeze),
    }


def _first_provider_key(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            if isinstance(item, str) and item:
                return item
            if isinstance(item, Mapping):
                api_key = str(item.get("api_key") or item.get("key") or "")
                if api_key:
                    return api_key
    return ""


def _rule_version_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    return _number(value)


def classify_billing_exception(exc: BaseException) -> tuple[str, bool, int | None]:
    """把远端异常归一成 ``(code, retryable, status_code)``，供错误码分流使用。

    未知异常默认「可重试」：宁可多试一次，也不要把网络抖动判成不可重试终态。
    """
    if isinstance(exc, PodBillingRemoteError):
        return exc.code, exc.retryable, exc.status_code
    if isinstance(exc, CustomerBillingRemoteError):
        return exc.code, exc.retryable, getattr(exc, "status_code", None)
    if isinstance(exc, CustomerBillingPermissionError):
        return "permission_denied", False, getattr(exc, "status_code", 401)
    status_code = getattr(exc, "status_code", None)
    if type(status_code) is int and 400 <= status_code < 500:
        # 非结构化 4xx：一律不可重试，避免把载荷错误刷成无限重试。
        return "client_error", False, status_code
    return "transient", True, status_code if type(status_code) is int else None


def session_remote_token_resolver(sessions: Any) -> Callable[[Actor], str]:
    """Resolve a remote token from the live session service only."""

    def resolve(actor: Actor) -> str:
        resolver = getattr(sessions, "remote_token_for_actor", None)
        if not callable(resolver):
            return ""
        # 各模块的 actor 契约并不统一：会话层的 Actor 暴露 ``id``，而
        # daily-selection / 价格核验的 actor 只暴露 ``actor_id``。二者都要
        # 兼容，否则取不到用户标识，采集凭据申请会不带 token 而被服务端 401
        # 拒绝（前端展示为“登录状态已过期”）。
        actor_id = getattr(actor, "id", None) or getattr(actor, "actor_id", "")
        if not actor_id:
            return ""
        return str(resolver(actor_id, actor.workspace_id) or "")

    return resolve


__all__ = [
    "PodBillingRemoteError",
    "RemotePodBillingCoordinator",
    "build_v2_freeze_payload",
    "build_v2_settlement_payload",
    "classify_billing_exception",
    "expected_points_for",
    "parse_v2_settlement_receipt",
    "session_remote_token_resolver",
    "use_billing_contract_v2",
]
