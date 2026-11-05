"""店铺登记的业务规则。

建店顺序固定为：先有员工标识，再校验店名，最后发号。
同名店铺按归属区分处理：
- 自己账号已有 → 直接复用，不重复发号；
- 别的账号已有 → 不新建店铺，登记为协助账号并打查重标记。
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .dedupe import (
    BEIJING,
    FLAG_SAME_ACCOUNT_DUP_ORDER,
    FLAG_SAME_STORE_MULTI_ACCOUNT,
)
from .repository import TeamRepository
from .rules import (
    StoreNameError,
    decide_store_name,
    normalize_staff_no,
    normalize_store_name,
    validate_staff_no,
    validate_store_name,
)

# 每个账号最多主动关联几个账号；被关联方不限制。
MAX_ACCOUNT_LINKS = 3


class StaffNoRequired(ValueError):
    """首次建店前必须先填写员工标识。"""


class StaffNoLocked(ValueError):
    """员工标识一经提交不可修改。"""


class StaffNoTaken(ValueError):
    """员工标识已被其他账号占用。"""


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


class TeamStoreService:
    def __init__(self, repository: TeamRepository) -> None:
        self._repository = repository
        # 登录账号补写进画像的记忆，避免每个请求都写一次库。
        self._noted_identities: dict[str, str] = {}

    def profile(self, account_id: str) -> dict[str, Any]:
        return {"profile": self._repository.get_profile(account_id)}

    def note_identity(self, account_id: str, username: str) -> None:
        """把登录账号补写进画像，别人就能用登录账号发起关联。

        还没登记员工标识的账号（例如只登录过团队看板）也先建一条只含登录账号的
        画像，否则别人按登录账号根本找不到它。
        """
        text = str(username or "").strip()
        if not account_id or not text:
            return
        if self._noted_identities.get(account_id) == text:
            return
        profile = self._repository.get_profile(account_id)
        if not profile:
            self._repository.save_profile(
                account_id,
                username=text,
                staff_no="",
                staff_no_norm="",
                now=_now(),
            )
        elif str(profile.get("username") or "") != text:
            self._repository.update_profile_username(account_id, text, _now())
        self._noted_identities[account_id] = text

    def save_profile(
        self,
        account_id: str,
        *,
        staff_no: object,
        username: str = "",
    ) -> dict[str, Any]:
        """首次填写员工标识；已填过的账号重复提交相同值视为幂等。"""
        validated = validate_staff_no(staff_no)
        normalized = normalize_staff_no(validated)
        current = self._repository.get_profile(account_id)
        if current and current.get("staff_no_norm"):
            if current.get("staff_no_norm") == normalized:
                if username and str(current.get("username") or "") != str(username).strip():
                    current = self._repository.save_profile(
                        account_id,
                        username=str(username).strip(),
                        staff_no=str(current.get("staff_no") or validated),
                        staff_no_norm=normalized,
                        now=_now(),
                    )
                return {"profile": current, "changed": False}
            raise StaffNoLocked("员工标识已提交，不能在原账号上修改")
        holder = self._repository.find_profile_by_staff_no(normalized)
        if holder and holder.get("account_id") != account_id:
            raise StaffNoTaken(f"员工标识「{validated}」已被其他账号使用")
        profile = self._repository.save_profile(
            account_id,
            username=username,
            staff_no=validated,
            staff_no_norm=normalized,
            now=_now(),
        )
        return {"profile": profile, "changed": True}

    def register_store(
        self,
        account_id: str,
        *,
        name: object,
        platform: str = "temu",
        mall_id: str = "",
    ) -> dict[str, Any]:
        """登记店铺并返回店铺码。

        跨账号重名不报错也不新建店铺，而是挂成协助账号，便于后续统计去重。
        """
        profile = self._repository.get_profile(account_id)
        if not profile or not profile.get("staff_no"):
            raise StaffNoRequired("首次创建店铺需要先填写员工标识")

        store_name = validate_store_name(name)
        normalized = normalize_store_name(store_name)
        existing = self._repository.find_store_by_name(normalized)
        mall = str(mall_id or "").strip()
        now = _now()

        if existing is None:
            store = self._repository.create_store(
                store_name=store_name,
                store_name_norm=normalized,
                owner_account_id=account_id,
                platform=str(platform or "temu"),
                mall_id=mall,
                now=now,
            )
            return {
                "ok": True,
                "created": True,
                "conflict": "",
                "store": {**store, "role": "owner"},
            }

        store_code = str(existing["store_code"])
        decision = decide_store_name(
            store_name,
            account_id=account_id,
            registry=[
                {
                    "name": existing["store_name"],
                    "account_id": existing["owner_account_id"],
                    "store_code": store_code,
                }
            ],
        )
        if mall:
            self._repository.fill_mall_id(store_code, mall, now)

        if not decision.is_conflict:
            self._repository.bind_account(store_code, account_id, "owner", now)
            return {
                "ok": True,
                "created": False,
                "conflict": "",
                "store": {**existing, "role": "owner"},
            }

        # 店名被别的账号占了：允许继续录入，但不新建店铺，只挂协助关系并打标记。
        self._repository.bind_account(store_code, account_id, "helper", now)
        self._repository.add_flag(
            kind=FLAG_SAME_STORE_MULTI_ACCOUNT,
            account_id=account_id,
            store_code=store_code,
            other_store_code="",
            data_key="",
            detail=(
                f'{{"store_name":"{store_name}",'
                f'"owner_account_id":"{existing["owner_account_id"]}"}}'
            ),
            now=now,
        )
        return {
            "ok": True,
            "created": False,
            "conflict": "other_account",
            "store": {**existing, "role": "helper"},
        }

    def list_stores(self, account_id: str) -> dict[str, Any]:
        return {"stores": self._repository.list_stores_for_account(account_id)}

    def list_flags(self, account_id: str) -> dict[str, Any]:
        return {"flags": self._repository.list_flags(account_id)}

    def delete_store(self, account_id: str, store_code: str) -> dict[str, Any]:
        """删除自己名下的店铺（连同汇总、明细、绑定、查重标记）。"""
        code = str(store_code or "").strip()
        store = self._repository.get_store_by_code(code)
        if store is None:
            raise LookupError("店铺不存在")
        if str(store.get("owner_account_id") or "") != account_id:
            raise PermissionError("只能删除自己登记的店铺")
        counts = self._repository.delete_store(code)
        return {"ok": True, "store_code": code, "deleted": counts}

    # ---------- 每周提交 ----------

    def submit_rollup(
        self,
        account_id: str,
        *,
        week_start: object,
        stores: list[dict[str, Any]] | None = None,
        batches: list[dict[str, Any]] | None = None,
        submitted_at: str = "",
    ) -> dict[str, Any]:
        """接收一周的店铺日汇总。

        同一店铺同一天只保留一份、源时间新的覆盖旧的；跨账号写同一家店、
        以及同账号跨店重复订单都只打标记，不删数据。
        """
        week = _normalize_week_start(week_start)
        stamp = str(submitted_at or "").strip() or _now()
        bound = {
            str(item.get("store_code")): item
            for item in self._repository.list_stores_for_account(account_id)
        }
        counts = {"created": 0, "updated": 0, "stale": 0}
        skipped: list[dict[str, str]] = []
        flags = {FLAG_SAME_STORE_MULTI_ACCOUNT: 0, FLAG_SAME_ACCOUNT_DUP_ORDER: 0}
        store_count = 0
        day_rows = 0
        record_total = 0

        for store in stores or []:
            code = str((store or {}).get("store_code") or "").strip()
            binding = bound.get(code)
            if not binding:
                skipped.append({"store_code": code, "reason": "store_not_bound"})
                continue
            store_count += 1
            owner_account = str(binding.get("owner_account_id") or "")

            for day in (store or {}).get("days") or []:
                stat_date = _normalize_date((day or {}).get("stat_date"))
                if not stat_date:
                    continue
                result = self._repository.upsert_store_daily(
                    store_code=code,
                    stat_date=stat_date,
                    source_account_id=account_id,
                    source_updated_at=str(day.get("source_updated_at") or stamp),
                    source_changed=False,
                    metrics=day,
                    now=stamp,
                )
                day_rows += 1
                counts[result["state"]] = counts.get(result["state"], 0) + 1
                if result["state"] == "stale":
                    continue
                previous_account = str((result.get("previous") or {}).get("source_account_id") or "")
                if (previous_account and previous_account != account_id) or (
                    owner_account and owner_account != account_id
                ):
                    self._repository.add_flag(
                        kind=FLAG_SAME_STORE_MULTI_ACCOUNT,
                        account_id=account_id,
                        store_code=code,
                        other_store_code="",
                        data_key="",
                        detail=json.dumps(
                            {
                                "store_code": code,
                                "owner_account_id": owner_account,
                                "previous_account_id": previous_account,
                            },
                            ensure_ascii=False,
                        ),
                        now=stamp,
                    )
                    flags[FLAG_SAME_STORE_MULTI_ACCOUNT] += 1

            # 明细（订单/财务/结算/流量/销量/指标）：整店全量替换。
            store_records = (store or {}).get("records") or []
            if store_records:
                record_total += self._repository.replace_store_records(
                    store_code=code,
                    records=store_records,
                    source_account_id=account_id,
                    now=stamp,
                )

            duplicated = (store or {}).get("dup_orders") or {}
            dup_count = int(duplicated.get("count") or 0)
            if dup_count > 0:
                self._repository.add_flag(
                    kind=FLAG_SAME_ACCOUNT_DUP_ORDER,
                    account_id=account_id,
                    store_code=code,
                    other_store_code=str(duplicated.get("other_store_code") or ""),
                    data_key="",
                    detail=json.dumps(
                        {
                            "count": dup_count,
                            "samples": list(duplicated.get("samples") or [])[:5],
                        },
                        ensure_ascii=False,
                    ),
                    now=stamp,
                )
                flags[FLAG_SAME_ACCOUNT_DUP_ORDER] += dup_count

        # 采集批次是工作台级的最近入库记录，随提交一起覆盖保存。
        batch_total = self._repository.replace_capture_batches(
            account_id,
            batches=batches or [],
            now=stamp,
        )

        submission = self._repository.save_submission(
            submission_id=uuid.uuid4().hex,
            account_id=account_id,
            week_start=week,
            submitted_at=stamp,
            store_count=store_count,
            day_rows=day_rows,
        )
        return {
            "ok": True,
            "submission": submission,
            "accepted": counts.get("created", 0) + counts.get("updated", 0),
            "created": counts.get("created", 0),
            "updated": counts.get("updated", 0),
            "stale": counts.get("stale", 0),
            "skipped": skipped,
            "flags": flags,
            "records": record_total,
            "batches": batch_total,
        }

    def list_submissions(self, account_id: str) -> dict[str, Any]:
        return {"submissions": self._repository.list_submissions(account_id)}

    def list_batches(self, account_id: str, *, limit: int = 50) -> dict[str, Any]:
        """当前账号最近上报的采集批次，供团队看板分类展示。"""
        return {"batches": self._repository.list_capture_batches(account_id, limit=limit)}

    # ---------- 组长视角 ----------

    def scope(self, account_id: str) -> dict[str, Any]:
        """当前账号能看哪些账号和店铺：自己 + 我担任组长的组内成员 + 我关联的账号。"""
        group_ids = self._repository.leader_group_ids(account_id)
        members = self._repository.member_account_ids(group_ids)
        visible = [account_id] + [item for item in members if item != account_id]
        linked = self._repository.list_links(account_id)
        for item in linked:
            if item not in visible:
                visible.append(item)
        stores = self._repository.store_codes_for_accounts(visible)
        groups = [
            group
            for group in self._repository.list_groups()
            if str(group.get("group_id")) in set(group_ids)
        ]
        return {
            "account_id": account_id,
            "is_leader": bool(group_ids),
            "groups": groups,
            "visible_account_ids": visible,
            "visible_store_count": len(stores),
            "linked_account_ids": linked,
        }

    def links(self, account_id: str) -> dict[str, Any]:
        """账号关联概览：已关联、我的申请、待我同意、谁关联了我。"""
        out_rows = self._repository.list_link_rows(account_id)
        pending_in = self._repository.list_linker_rows(account_id, status="pending")
        accepted_by = self._repository.list_linker_rows(account_id, status="accepted")
        accepted_out = [row for row in out_rows if row.get("status") == "accepted"]
        pending_out = [row for row in out_rows if row.get("status") == "pending"]
        ids = list(
            dict.fromkeys(
                [str(row["target_account_id"]) for row in out_rows]
                + [str(row["linker_account_id"]) for row in pending_in]
                + [str(row["linker_account_id"]) for row in accepted_by]
            )
        )
        profiles = self._repository.profiles_by_ids(ids)

        def describe(row: dict[str, Any], id_field: str) -> dict[str, Any]:
            other = str(row.get(id_field) or "")
            profile = profiles.get(other) or {}
            return {
                "account_id": other,
                "username": str(profile.get("username") or ""),
                "staff_no": str(profile.get("staff_no") or ""),
                "store_count": len(
                    self._repository.store_codes_for_accounts([other])
                ),
                "status": str(row.get("status") or ""),
                "requested_at": str(row.get("created_at") or ""),
            }

        mine = self._repository.get_profile(account_id) or {}
        return {
            "account_id": account_id,
            "username": str(mine.get("username") or ""),
            "staff_no": str(mine.get("staff_no") or ""),
            "max_links": MAX_ACCOUNT_LINKS,
            "linked": [describe(row, "target_account_id") for row in accepted_out],
            "pending_out": [describe(row, "target_account_id") for row in pending_out],
            "pending_in": [describe(row, "linker_account_id") for row in pending_in],
            "linked_by": [describe(row, "linker_account_id") for row in accepted_by],
            # 名额按「已关联 + 待同意」一起算，避免无限占用
            "linked_count": len(accepted_out) + len(pending_out),
            "accepted_count": len(accepted_out),
            "pending_out_count": len(pending_out),
            "pending_in_count": len(pending_in),
            "linked_by_count": len(accepted_by),
        }

    def _resolve_link_target(self, target: str) -> str:
        """把用户填的内容换成账号 ID：账号 ID / 登录账号 / 员工标识都能用。"""
        if self._repository.get_profile(target):
            return target
        profile = self._repository.find_profile_by_username(target)
        if profile:
            return str(profile["account_id"])
        profile = self._repository.find_profile_by_staff_no(normalize_staff_no(target))
        if profile:
            return str(profile["account_id"])
        if self._repository.store_codes_for_accounts([target]):
            return target
        raise ValueError("账号不存在：请填对方的账号 ID、登录账号或员工标识（对方需先登录过团队看板）")

    def add_link(self, account_id: str, target_account_id: str) -> dict[str, Any]:
        """发起关联申请，必须对方同意后才生效。"""
        target = str(target_account_id or "").strip()
        if not target:
            raise ValueError("请填写对方账号 ID、登录账号或员工标识")
        target = self._resolve_link_target(target)
        if target == account_id:
            raise ValueError("不能关联自己的账号")
        existing = self._repository.get_link(account_id, target)
        if existing:
            return self.links(account_id)
        # 对方已经申请过我：视为互相同意，直接建立关联
        reverse = self._repository.get_link(target, account_id)
        if reverse and str(reverse.get("status") or "") == "pending":
            self._repository.set_link_status(target, account_id, "accepted", _now())
            return self.links(account_id)
        used = len(self._repository.list_links(account_id, status=""))
        if used >= MAX_ACCOUNT_LINKS:
            raise ValueError(f"最多关联 {MAX_ACCOUNT_LINKS} 个账号，请先移除一个")
        self._repository.add_link(account_id, target, _now(), status="pending")
        return self.links(account_id)

    def accept_link(self, account_id: str, requester_account_id: str) -> dict[str, Any]:
        """同意别人的关联申请（我是被关联方）。"""
        requester = str(requester_account_id or "").strip()
        row = self._repository.get_link(requester, account_id)
        if not row or str(row.get("status")) != "pending":
            raise ValueError("没有待同意的关联申请")
        self._repository.set_link_status(requester, account_id, "accepted", _now())
        return self.links(account_id)

    def reject_link(self, account_id: str, requester_account_id: str) -> dict[str, Any]:
        requester = str(requester_account_id or "").strip()
        self._repository.remove_link(requester, account_id)
        return self.links(account_id)

    def remove_link(self, account_id: str, target_account_id: str) -> dict[str, Any]:
        """移除已关联的账号，或撤回自己发出的申请。"""
        self._repository.remove_link(account_id, str(target_account_id or "").strip())
        return self.links(account_id)

    def summary(
        self,
        account_id: str,
        *,
        start: str,
        end: str,
        group_by: str = "shop",
    ) -> dict[str, Any]:
        scope = self.scope(account_id)
        stores = self._repository.store_codes_for_accounts(scope["visible_account_ids"])
        rows = self._repository.summarize_range(
            start=start,
            end=end,
            store_codes=stores,
            group_by=group_by if group_by in {"shop", "account", "date"} else "shop",
        )
        return {
            "scope": scope,
            "group_by": group_by,
            "start": start,
            "end": end,
            "rows": rows,
            "totals": _sum_totals(rows),
        }

    def overview(
        self,
        account_id: str,
        *,
        start: str,
        end: str,
        top: int = 8,
        exclude: Iterable[str] = (),
    ) -> dict[str, Any]:
        """看板总览。``start``/``end`` 为空表示所有日期，``exclude`` 是要剔除的店铺码。"""
        scope = self.scope(account_id)
        all_codes = self._repository.store_codes_for_accounts(scope["visible_account_ids"])
        hidden = {str(code) for code in exclude if str(code)}
        stores = [code for code in all_codes if code not in hidden]
        by_date = self._repository.summarize_range(
            start=start, end=end, store_codes=stores, group_by="date"
        )
        by_shop = self._repository.summarize_range(
            start=start, end=end, store_codes=stores, group_by="shop"
        )
        by_account = self._repository.summarize_range(
            start=start, end=end, store_codes=stores, group_by="account"
        )
        # 所有日期时没有固定起止，用实际有数据的首尾日期回显。
        if not start or not end:
            start = str(by_date[0].get("key") or "") if by_date else ""
            end = str(by_date[-1].get("key") or "") if by_date else ""
        return {
            "scope": scope,
            "start": start,
            "end": end,
            "totals": _sum_totals(by_date),
            "trend": by_date,
            "top_stores": by_shop[: max(1, int(top))],
            "by_account": by_account,
            "stores": self._repository.list_stores_by_codes(stores),
            # 供筛选面板使用：包含被剔除的店铺，方便再勾回来。
            "all_stores": self._repository.list_stores_by_codes(all_codes),
        }

    def records(
        self,
        account_id: str,
        *,
        kinds: Iterable[str],
        stores: Iterable[str] = (),
        exclude: Iterable[str] = (),
        start: str = "",
        end: str = "",
        status: str = "",
        keyword: str = "",
        limit: int = 2000,
    ) -> dict[str, Any]:
        """明细查询：订单 / 财务 / 结算 / 流量 / 销量 / 店铺指标。"""
        scope = self.scope(account_id)
        visible = self._repository.store_codes_for_accounts(scope["visible_account_ids"])
        hidden = {str(code) for code in exclude if str(code)}
        wanted = {str(code) for code in stores if str(code)}
        codes = [code for code in visible if code not in hidden]
        if wanted:
            codes = [code for code in codes if code in wanted]
        kind_list = [str(kind).strip() for kind in kinds if str(kind).strip()] or ["order"]
        rows = self._repository.query_records(
            kinds=kind_list,
            store_codes=codes,
            start=start,
            end=end,
            limit=limit,
        )
        keyword_text = str(keyword or "").strip().lower()
        status_text = str(status or "").strip()
        if keyword_text or status_text:
            filtered: list[dict[str, Any]] = []
            for row in rows:
                data = row.get("payload") or {}
                if status_text and status_text not in str(data.get("order_status") or ""):
                    continue
                if keyword_text:
                    haystack = " ".join(
                        str(data.get(field) or "")
                        for field in (
                            "parent_order_sn",
                            "sub_order_sn",
                            "sku_id",
                            "goods_name",
                            "tracking_no",
                            "note",
                            "entry_type",
                        )
                    ).lower()
                    if keyword_text not in haystack:
                        continue
                filtered.append(row)
            rows = filtered
        return {
            "scope": scope,
            "kinds": kind_list,
            "start": start,
            "end": end,
            "count": len(rows),
            "records": rows,
            "stores": self._repository.list_stores_by_codes(codes),
            "all_stores": self._repository.list_stores_by_codes(visible),
        }

    def trends(
        self,
        account_id: str,
        *,
        start: str,
        end: str,
        exclude: Iterable[str] = (),
        top: int = 6,
    ) -> dict[str, Any]:
        """多店铺趋势：每个店铺一条曲线，另给一条总计曲线。"""
        scope = self.scope(account_id)
        all_codes = self._repository.store_codes_for_accounts(scope["visible_account_ids"])
        hidden = {str(code) for code in exclude if str(code)}
        codes = [code for code in all_codes if code not in hidden]
        rows = self._repository.daily_series(store_codes=codes, start=start, end=end)
        dates = sorted({str(row["stat_date"]) for row in rows})
        empty_point = {
            "order_count": 0,
            "gmv_cny": 0.0,
            "settlement_payback_cny": 0.0,
            "profit_cny": None,
        }
        totals = {
            day: {
                "stat_date": day,
                "order_count": 0,
                "gmv_cny": 0.0,
                "settlement_payback_cny": 0.0,
                "profit_cny": None,
            }
            for day in dates
        }
        by_store: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row["store_code"])
            day = str(row["stat_date"])
            bucket = by_store.setdefault(
                code,
                {
                    "store_code": code,
                    "store_name": str(row.get("store_name") or code),
                    "points": {},
                },
            )
            bucket["points"][day] = {
                "order_count": int(row["order_count"] or 0),
                "gmv_cny": float(row["gmv_cny"] or 0),
                "settlement_payback_cny": float(row["settlement_payback_cny"] or 0),
                "profit_cny": row.get("profit_cny"),
            }
            if day in totals:
                totals[day]["order_count"] += int(row["order_count"] or 0)
                totals[day]["gmv_cny"] += float(row["gmv_cny"] or 0)
                totals[day]["settlement_payback_cny"] += float(
                    row["settlement_payback_cny"] or 0
                )
        series: list[dict[str, Any]] = []
        for bucket in by_store.values():
            points = [
                {"stat_date": day, **(bucket["points"].get(day) or empty_point)}
                for day in dates
            ]
            series.append(
                {
                    "store_code": bucket["store_code"],
                    "store_name": bucket["store_name"],
                    "points": points,
                    "total_gmv": sum(float(point["gmv_cny"] or 0) for point in points),
                }
            )
        series.sort(key=lambda item: item["total_gmv"], reverse=True)
        return {
            "scope": scope,
            "start": dates[0] if dates else start,
            "end": dates[-1] if dates else end,
            "dates": dates,
            "series": series[: max(1, int(top))],
            "series_total": len(series),
            "totals": [totals[day] for day in dates],
            "stores": self._repository.list_stores_by_codes(codes),
            "all_stores": self._repository.list_stores_by_codes(all_codes),
        }


def _sum_totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    keys = (
        "order_count",
        "sub_order_count",
        "gmv_cny",
        "refund_cny",
        "profit_cny",
        "settlement_payback_cny",
        "finance_expense_cny",
        "exposure",
        "clicks",
        "visitors",
        "sales_today",
    )
    totals = {key: 0.0 for key in keys}
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            totals[key] += float(value)
    totals["store_count"] = float(len(rows))
    totals["avg_order_value"] = (
        totals["gmv_cny"] / totals["order_count"] if totals["order_count"] else 0.0
    )
    return totals



def _normalize_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _normalize_week_start(value: object) -> str:
    """任意日期都收敛到所在周的周一。"""
    parsed = _normalize_date(value)
    if not parsed:
        today = datetime.now(BEIJING).date()
        return (today - timedelta(days=today.weekday())).isoformat()
    current = date.fromisoformat(parsed)
    return (current - timedelta(days=current.weekday())).isoformat()



__all__ = [
    "StaffNoRequired",
    "StaffNoLocked",
    "StaffNoTaken",
    "StoreNameError",
    "TeamStoreService",
]
