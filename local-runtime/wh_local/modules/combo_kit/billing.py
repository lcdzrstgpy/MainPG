"""combo_kit 隔离扣费：文本 20 / 生图 100，冻结占额 + 成功后结算。

复用远端计费客户端的 freeze_batch_points / settle_batch_points：
- 生图/文本请求前先冻结（占额 + 领直连密钥），
- 任务 100% 成功才按对应积分结算；失败/部分失败按结果退额。
文本与生图是两个独立 freeze/settle 周期，互不捆绑、不提前扣、不预扣。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...customer.contracts import CustomerBillingPermissionError
from ...session import Actor
from .contracts import IMAGE_POINTS, TEXT_POINTS, ComboKitError


class ComboKitBillingCoordinator:
    def __init__(
        self,
        remote_client: Any,
        remote_token_resolver: Callable[[Actor], str],
    ) -> None:
        self._remote_client = remote_client
        self._remote_token_resolver = remote_token_resolver

    def _required_remote_token(self, actor: Actor) -> str:
        token = str(self._remote_token_resolver(actor) or "")
        if not token:
            raise CustomerBillingPermissionError() from None
        return token

    def freeze(
        self,
        actor: Actor,
        *,
        billing_type: str,
        set_id: str,
        idempotency_key: str,
        scope: list[str],
    ) -> dict[str, Any]:
        """为一次文本 / 生图调用冻结对应积分并领短期密钥。

        billing_type ∈ {'text', 'image'}；points 由业务写死（20 / 100）。
        返回 {freeze_id, link_count, scope, points, keys, rule_version}。
        """
        token = self._required_remote_token(actor)
        points = _points_and_profile(billing_type)
        response = self._remote_client.freeze_batch_points(
            token,
            {
                "idempotency_key": idempotency_key,
                "link_count": 1,
                "scope": list(scope or []),
            },
        )
        freeze = response.get("freeze") if isinstance(response, Mapping) else None
        if not isinstance(freeze, Mapping):
            raise ComboKitError("套餐计费服务返回了无效冻结结果")
        keys = freeze.get("keys") if isinstance(freeze, Mapping) else []
        granted = {
            str(key.get("provider") or ""): str(key.get("api_key") or "")
            for key in keys
            if isinstance(key, dict) and key.get("api_key")
        }
        return {
            "set_id": set_id,
            "billing_type": billing_type,
            "freeze_id": str(freeze.get("freeze_id") or ""),
            "link_count": int(freeze.get("link_count") or 1),
            "scope": [str(item) for item in (freeze.get("scope") or scope or [])],
            "points": points,
            "rule_version": int(freeze.get("rule_version") or 0),
            "keys": granted,
            "token": token,
        }

    def settle(
        self,
        actor: Actor,
        freeze: Mapping[str, Any],
        *,
        success: bool,
        settled_result: str = "",
    ) -> None:
        """结算一个冻结批次：成功全价、失败/部分退额。幂等。"""
        token = str(freeze.get("token") or "")
        freeze_id = str(freeze.get("freeze_id") or "")
        if not token or not freeze_id:
            return
        status = "success" if success else "no_return"
        feature = _feature_for_billing_type(str(freeze.get("billing_type") or ""))
        items = [
            {
                "link_idx": 1,
                "subitems": [{"feature": feature, "status": status}],
            }
        ]
        try:
            self._remote_client.settle_batch_points(token, freeze_id, {"items": items})
        except Exception:
            # 结算失败不抛出：保留 open 记录，由对账 / TTL 兜底释放。
            return


def session_remote_token_resolver(sessions: Any) -> Callable[[Actor], str]:
    def resolve(actor: Actor) -> str:
        resolver = getattr(sessions, "remote_token_for_actor", None)
        if not callable(resolver):
            return ""
        return str(resolver(actor.id, actor.workspace_id) or "")

    return resolve


def _points_and_profile(billing_type: str) -> int:
    if billing_type == "text":
        return TEXT_POINTS
    if billing_type == "image":
        return IMAGE_POINTS
    raise ComboKitError(f"未知扣费类型：{billing_type}")


def _feature_for_billing_type(billing_type: str) -> str:
    if billing_type == "text":
        return "title"
    return "four_grid"


__all__ = ["ComboKitBillingCoordinator", "session_remote_token_resolver"]
