"""团队服务运维命令：在服务器上手工维护账号画像、查看汇总。

员工标识在客户端是锁死的（填错了不能自己改），只有这里能以管理员身份修改：

    python devtools/team_admin.py --database /opt/wh-workbench/data/team.sqlite3 list
    python devtools/team_admin.py --database /opt/wh-workbench/data/team.sqlite3 \
        set-staff-no --account-id acc-123 --staff-no 张三
    python devtools/team_admin.py --database /opt/wh-workbench/data/team.sqlite3 summary --week 2026-10-19
    python devtools/team_admin.py --database /opt/wh-workbench/data/team.sqlite3 flags
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from wh_local.modules.team_store.dedupe import BEIJING  # noqa: E402
from wh_local.modules.team_store.repository import TeamRepository  # noqa: E402
from wh_local.modules.team_store.rules import normalize_staff_no, validate_staff_no  # noqa: E402


def _print_summary(rows: list[dict]) -> None:
    if not rows:
        print("（这段时间还没有上报数据）")
        return
    header = (
        f'{"店铺":<18}{"店铺码":<14}{"天数":>4}{"订单":>6}{"GMV":>12}'
        f'{"利润":>10}{"回款":>12}{"费用":>10}{"曝光":>10}{"点击":>8}'
    )
    print(header)
    print("-" * len(header))
    totals = {key: 0.0 for key in ("order_count", "gmv_cny", "profit_cny", "settlement_payback_cny", "finance_expense_cny", "exposure", "clicks")}
    for row in rows:
        gmv = float(row.get("gmv_cny") or 0)
        profit = row.get("profit_cny")
        print(
            f'{str(row.get("store_name") or "-"):<18}{str(row.get("store_code")):<14}'
            f'{int(row.get("day_count") or 0):>4}{int(row.get("order_count") or 0):>6}'
            f'{gmv:>12.2f}{("-" if profit is None else f"{float(profit):.2f}"):>10}'
            f'{float(row.get("settlement_payback_cny") or 0):>12.2f}'
            f'{float(row.get("finance_expense_cny") or 0):>10.2f}'
            f'{int(row.get("exposure") or 0):>10}{int(row.get("clicks") or 0):>8}'
        )
        for key in totals:
            totals[key] += float(row.get(key) or 0)
    print("-" * len(header))
    print(
        f'{"合计":<18}{"":<14}{"":>4}{int(totals["order_count"]):>6}'
        f'{totals["gmv_cny"]:>12.2f}{totals["profit_cny"]:>10.2f}'
        f'{totals["settlement_payback_cny"]:>12.2f}{totals["finance_expense_cny"]:>10.2f}'
        f'{int(totals["exposure"]):>10}{int(totals["clicks"]):>8}'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="MainPG 团队服务运维命令")
    parser.add_argument("--database", default="", help="团队服务 SQLite 路径")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="列出账号画像")
    rename = sub.add_parser("set-staff-no", help="管理员修改员工标识")
    rename.add_argument("--account-id", required=True)
    rename.add_argument("--staff-no", required=True)

    summary = sub.add_parser("summary", help="按店铺汇总")
    summary.add_argument("--from", dest="start", default="")
    summary.add_argument("--to", dest="end", default="")
    summary.add_argument("--week", default="", help="按某一周的周一，如 2026-10-19")
    summary.add_argument("--store", action="append", default=[], help="只看指定店铺码")

    sub.add_parser("flags", help="列出去重标记")

    sub.add_parser("group-list", help="列出组长组")
    group_create = sub.add_parser("group-create", help="新建组长组")
    group_create.add_argument("--name", required=True)
    group_create.add_argument("--leader", required=True, help="组长 account_id")
    group_create.add_argument("--group-id", default="", help="不填则自动生成")
    group_add = sub.add_parser("group-add", help="把账号加入组")
    group_add.add_argument("--group-id", required=True)
    group_add.add_argument("--account-id", required=True)
    group_remove = sub.add_parser("group-remove", help="把账号移出组")
    group_remove.add_argument("--group-id", required=True)
    group_remove.add_argument("--account-id", required=True)


    args = parser.parse_args()
    database = Path(args.database) if args.database else RUNTIME_ROOT / "data" / "team.sqlite3"
    repository = TeamRepository(database)

    if args.action == "list":
        profiles = repository.list_profiles()
        if not profiles:
            print("（还没有账号画像）")
            return 0
        for item in profiles:
            print(
                f'{item["account_id"]}  {item["staff_no"] or "-"}  '
                f'{item["username"] or "-"}  {item["updated_at"]}'
            )
        return 0

    if args.action == "summary":
        today = datetime.now(BEIJING).date()
        if args.week:
            anchor = date.fromisoformat(args.week[:10])
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
        else:
            start = date.fromisoformat(args.start[:10]) if args.start else today - timedelta(days=29)
            end = date.fromisoformat(args.end[:10]) if args.end else today
        rows = repository.summarize_daily(
            start=start.isoformat(),
            end=end.isoformat(),
            store_codes=args.store or None,
        )
        print(f"统计区间：{start.isoformat()} ~ {end.isoformat()}")
        _print_summary(rows)
        return 0

    if args.action == "flags":
        flags = repository.list_flags_for_admin()
        if not flags:
            print("（没有查重标记）")
            return 0
        for item in flags:
            detail = item.get("detail") or "{}"
            try:
                detail = json.dumps(json.loads(detail), ensure_ascii=False)
            except ValueError:
                pass
            print(
                f'{item["created_at"]}  {item["kind"]}  '
                f'账号={item["account_id"]}  店铺={item["store_code"]}  {detail}'
            )
        return 0

    if args.action == "group-list":
        groups = repository.list_groups()
        if not groups:
            print("（还没有组长组）")
            return 0
        for group in groups:
            members = repository.member_account_ids([str(group["group_id"])])
            print(
                f'{group["group_id"]}  {group["name"]}  '
                f'组长={group["leader_account_id"]}  '
                f'成员 {group["member_count"]}：{", ".join(members) or "-"}'
            )
        return 0

    if args.action == "group-create":
        group_id = args.group_id or f"g-{uuid.uuid4().hex[:8]}"
        repository.create_group(
            group_id=group_id,
            name=args.name,
            leader_account_id=args.leader,
            now=datetime.now(BEIJING).isoformat(timespec="seconds"),
        )
        print(f"已创建组长组 {group_id}（{args.name}），组长={args.leader}")
        return 0

    if args.action == "group-add":
        repository.add_member(
            group_id=args.group_id,
            account_id=args.account_id,
            now=datetime.now(BEIJING).isoformat(timespec="seconds"),
        )
        print(f"已把 {args.account_id} 加入 {args.group_id}")
        return 0

    if args.action == "group-remove":
        repository.remove_member(group_id=args.group_id, account_id=args.account_id)
        print(f"已把 {args.account_id} 移出 {args.group_id}")
        return 0

    staff_no = validate_staff_no(args.staff_no)
    normalized = normalize_staff_no(staff_no)
    holder = repository.find_profile_by_staff_no(normalized)
    if holder and holder.get("account_id") != args.account_id:
        print(f"员工标识「{staff_no}」已被 {holder['account_id']} 占用，未修改")
        return 1
    current = repository.get_profile(args.account_id)
    if current is None:
        print(f"账号 {args.account_id} 没有画像，先让该账号在客户端填一次标识")
        return 1
    repository.save_profile(
        args.account_id,
        username=str(current.get("username") or ""),
        staff_no=staff_no,
        staff_no_norm=normalized,
        now=datetime.now(BEIJING).isoformat(timespec="seconds"),
    )
    print(f"已把 {args.account_id} 的员工标识改为「{staff_no}」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
