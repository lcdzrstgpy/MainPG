from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, cast

from ...session import Actor
from .billing_contract import PodCallPlan
from .remote_billing import attach_freeze_snapshot, classify_billing_exception
from .repository import PodCustomizationRepository


logger = logging.getLogger(__name__)

_SHANGHAI = timezone(timedelta(hours=8))

# 与服务端日报逐项比对的字段（设计 7.4）。任一字段差额 ≠ 0 都要下钻，但不自动修数。
DAILY_FIELDS = (
    "freeze_count",
    "freeze_points",
    "settle_count",
    "charged_points",
    "refunded_points",
    "close_count",
    "release_count",
    "released_points",
    "retained_points",
    "zero_count",
    "open_freezes",
)

_NOT_FOUND_CODES = {"not_found", "freeze_not_found"}


def deadline_passed(value: object) -> bool:
    """「还能不能结算」只认服务端时间点；解析不了时按已到期处理。"""
    text = str(value or "").strip()
    if not text:
        return True
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= moment


def _on_day(value: object, day: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_SHANGHAI).date().isoformat() == day


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class PodBillingReconciler:
    """启动 / 定时对账 + 与服务端日报对平。

    职责边界：只读服务端状态、按确定规则推进本地状态；金额一律取回执，
    解释不了的差额只记录，不自动修数。
    """

    def __init__(
        self,
        repository: PodCustomizationRepository,
        coordinator: Any,
        *,
        settle_run: Callable[[dict[str, Any]], None],
        plan_builder: Callable[[dict[str, Any]], PodCallPlan],
        device_id: Callable[[], str],
    ) -> None:
        self.repository = repository
        self.coordinator = coordinator
        self._settle_run = settle_run
        self._plan_builder = plan_builder
        self._device_id = device_id

    # ------------------------------------------------------------------ #
    # 第一 / 二层：启动与定时对账
    # ------------------------------------------------------------------ #

    def reconcile_actor(self, actor: Actor, *, scope: str = "startup") -> dict[str, Any]:
        started_at = _now()
        self.repository.expire_stale_intents()
        runs = self.repository.list_non_terminal_billing_runs(
            actor.workspace_id, actor.id
        )
        repaired = 0
        loss = 0
        loss_points = 0.0
        notes: list[str] = []
        for stored in runs:
            try:
                result = self._reconcile_run(actor, stored)
            except Exception as exc:  # noqa: BLE001 - 单笔失败不阻断整轮
                notes.append(f"{stored.get('freeze_id') or stored.get('action_key')}: {exc}")
                continue
            if result == "repaired":
                repaired += 1
            elif result == "loss":
                loss += 1
                loss_points += _number(stored.get("frozen_points"))

        unclaimed = self._unclaimed_freezes(actor, notes)
        if unclaimed:
            notes.append(
                f"unclaimed={len(unclaimed)}:"
                + ",".join(item["freeze_id"] for item in unclaimed[:20])
            )
        record = self.repository.insert_recon_run(
            scope=scope,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            checked_count=len(runs),
            repaired_count=repaired,
            loss_count=loss,
            loss_points=round(loss_points, 1),
            note="; ".join(notes)[:2000],
            started_at=started_at,
        )
        return {
            "record": record,
            "checked": len(runs),
            "repaired": repaired,
            "loss": loss,
            "loss_points": round(loss_points, 1),
            "unclaimed": unclaimed,
            "notes": notes,
        }

    def _reconcile_run(self, actor: Actor, stored: Mapping[str, Any]) -> str:
        if str(stored.get("status")) == "intent" or not stored.get("freeze_id"):
            return self._replay_intent(actor, stored)
        return self._probe_run(actor, stored)

    def _replay_intent(self, actor: Actor, stored: Mapping[str, Any]) -> str:
        """意图行用 action_key 重放冻结：服务器只认第一次。"""
        plan = self._plan_builder(stored["plan"])
        try:
            grant = self.coordinator.freeze(actor, plan)
        except Exception as exc:  # noqa: BLE001 - 按码分流
            code, _retryable, status_code = classify_billing_exception(exc)
            if code == "freeze_not_active":
                return self._probe_run(actor, stored, freeze_id=str(stored["action_key"]))
            self.repository.mark_billing_intent_failed(
                str(stored["action_key"]), str(exc)
            )
            if status_code == 404:
                return "loss"
            # 400 等业务拒绝：保留意图等用户重发，不判损失。
            return "skipped"
        attach_freeze_snapshot(
            self.repository,
            str(stored["action_key"]),
            plan,
            grant,
            device_id=self._device_id(),
        )
        return "repaired"

    def _probe_run(
        self,
        actor: Actor,
        stored: Mapping[str, Any],
        *,
        freeze_id: str = "",
    ) -> str:
        prober = getattr(self.coordinator, "freeze_status", None)
        if not callable(prober):
            # v1 兼容期没有探活接口：保持现状，等 v2 上线后再收敛。
            return "skipped"
        try:
            probe = cast(
                Mapping[str, Any],
                prober(actor, freeze_id or str(stored.get("freeze_id") or "")),
            )
        except Exception as exc:  # noqa: BLE001 - 404 才判孤儿
            code, _retryable, status_code = classify_billing_exception(exc)
            if status_code == 404 or code in _NOT_FOUND_CODES:
                self.repository.finish_billing_from_probe(
                    str(stored["action_key"]), "orphaned", "freeze not found"
                )
                return "loss"
            raise
        return self._apply_probe(stored, probe)

    def _apply_probe(
        self, stored: Mapping[str, Any], probe: Mapping[str, Any]
    ) -> str:
        action_key = str(stored["action_key"])
        remote_status = str(probe.get("status") or "")
        if remote_status == "released":
            self.repository.insert_settlement(
                freeze_id=str(stored.get("freeze_id") or probe.get("freeze_id") or action_key),
                source="ttl",
                released_points=probe.get("released_points"),
                retained_points=probe.get("retained_points"),
                release_reason=str(probe.get("release_reason") or "ttl"),
                raw_response=probe.get("raw") or probe,
            )
            self.repository.finish_billing_from_probe(action_key, "expired_released")
            return "repaired"
        if remote_status == "settled":
            self.repository.finish_billing_from_probe(action_key, "settled")
            return "repaired"
        if remote_status == "frozen":
            if not deadline_passed(
                stored.get("settle_deadline") or stored.get("grant_expires_at")
            ):
                return "skipped"
            if any(
                str(outcome.get("status")) == "started"
                for outcome in stored.get("outcomes") or ()
            ):
                self.repository.release_started_outcomes(action_key)
                refreshed = self.repository.find_billing_run_by_action_key(action_key)
                if refreshed is None:
                    return "skipped"
                stored = refreshed
            self._settle_run(dict(stored))
            return "repaired"
        return "skipped"

    def _unclaimed_freezes(self, actor: Actor, notes: list[str]) -> list[dict[str, Any]]:
        """多设备规则：只自动处理「本机 device_id + 本地有 run」的冻结，其余只展示。"""
        lister = getattr(self.coordinator, "open_freezes", None)
        if not callable(lister):
            return []
        try:
            payload = lister(actor)
        except Exception as exc:  # noqa: BLE001 - 列表失败不阻断主流程
            notes.append(f"open freezes unavailable: {exc}")
            return []
        if not isinstance(payload, Mapping) or payload.get("unavailable"):
            return []
        local_device = self._device_id()
        unclaimed: list[dict[str, Any]] = []
        for freeze in payload.get("freezes") or ():
            if not isinstance(freeze, Mapping):
                continue
            freeze_id = str(freeze.get("freeze_id") or "")
            if not freeze_id:
                continue
            local_run = self.repository.find_billing_run_by_freeze_id(freeze_id)
            if local_run is not None and str(freeze.get("device_id") or "") == local_device:
                # 本机且本地有记录 → 已在上面的逐笔循环里处理。
                continue
            unclaimed.append(
                {
                    "freeze_id": freeze_id,
                    "device_id": str(freeze.get("device_id") or ""),
                    "module": str(freeze.get("module") or ""),
                    "billing_profile": str(freeze.get("billing_profile") or ""),
                    "frozen_points": freeze.get("frozen_points"),
                    "settle_deadline": str(freeze.get("settle_deadline") or ""),
                    "reason": "local_record_missing" if local_run is None else "other_device",
                }
            )
        return unclaimed

    # ------------------------------------------------------------------ #
    # 第三 / 四层：本地日报 + 与服务端日报对平
    # ------------------------------------------------------------------ #

    def local_daily_summary(
        self, workspace_id: str, owner_user_id: str, day: str
    ) -> dict[str, Any]:
        runs, settlements = self.repository.list_daily_billing_rows(
            workspace_id, owner_user_id
        )
        todays_runs = [row for row in runs if _on_day(row.get("created_at"), day)]
        billed = [row for row in todays_runs if row.get("freeze_id") and not row.get("zero_billed")]
        zero = [row for row in todays_runs if row.get("zero_billed")]
        todays_settlements = [
            row
            for row in settlements
            if _on_day(row.get("settled_at") or row.get("created_at"), day)
        ]
        normal = [row for row in todays_settlements if row.get("source") == "settle"]
        close = [row for row in todays_settlements if row.get("source") == "close"]
        released = [row for row in todays_settlements if row.get("source") == "ttl"]
        active = set(self.repository.BILLING_ACTIVE_STATUSES)
        return {
            "date": day,
            "freeze_count": len(billed),
            "freeze_points": round(sum(_number(row.get("frozen_points")) for row in billed), 1),
            "settle_count": len(normal),
            "charged_points": round(sum(_number(row.get("charged_points")) for row in normal), 1),
            "refunded_points": round(sum(_number(row.get("refunded_points")) for row in normal), 1),
            "close_count": len(close),
            "close_charged_points": round(sum(_number(row.get("charged_points")) for row in close), 1),
            "release_count": len(released),
            "released_points": round(sum(_number(row.get("released_points")) for row in released), 1),
            "retained_points": round(sum(_number(row.get("retained_points")) for row in released), 1),
            "zero_count": len(zero),
            "open_freezes": sum(
                1 for row in runs if str(row.get("status") or "") in active
            ),
            "orphan_count": sum(
                1 for row in todays_runs if str(row.get("status")) == "orphaned"
            ),
            "anomaly_count": sum(
                1 for row in todays_runs if str(row.get("status")) == "anomaly"
            ),
            "expected_mismatch_count": sum(
                1
                for row in todays_settlements
                if abs(_number(row.get("expected_points")) - _number(row.get("charged_points")))
                > 0.1
            ),
        }

    def reconcile_daily(
        self, actor: Actor, *, day: str = "", tz: str = "Asia/Shanghai"
    ) -> dict[str, Any]:
        target = day or datetime.now(_SHANGHAI).date().isoformat()
        started_at = _now()
        local = self.local_daily_summary(actor.workspace_id, actor.id, target)
        report = {}
        detail: dict[str, Any] = {}
        differences: dict[str, dict[str, float]] = {}
        reader = getattr(self.coordinator, "daily_report", None)
        if callable(reader):
            try:
                payload = reader(actor, date=target, tz=tz)
                if isinstance(payload, Mapping) and not payload.get("unavailable"):
                    nested = payload.get("report")
                    report = dict(nested) if isinstance(nested, Mapping) else dict(payload)
            except Exception as exc:  # noqa: BLE001 - 日报不可用不阻断本地出账
                detail["unavailable_reason"] = str(exc)
        if report:
            for field in DAILY_FIELDS:
                if field not in report:
                    continue
                expected = _number(local.get(field))
                actual = _number(report.get(field))
                if abs(expected - actual) > 0.05:
                    differences[field] = {"local": expected, "server": actual}
            if differences:
                driller = getattr(self.coordinator, "daily_report_detail", None)
                if callable(driller):
                    try:
                        drilled = cast(
                            Mapping[str, Any],
                            driller(actor, date=target, tz=tz, freeze_id=""),
                        )
                        detail.update(dict(drilled))
                    except Exception as exc:  # noqa: BLE001
                        detail["detail_error"] = str(exc)
        total_difference = round(
            sum(abs(item["local"] - item["server"]) for item in differences.values()), 1
        )
        record = self.repository.insert_recon_run(
            scope="daily",
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            checked_count=len(DAILY_FIELDS),
            daily_difference=total_difference,
            note=str(differences or detail)[:2000],
            started_at=started_at,
        )
        return {
            "date": target,
            "local": local,
            "server": report,
            "differences": differences,
            "detail": detail,
            "record": record,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
