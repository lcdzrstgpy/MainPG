"""预检图片清单诊断（只读）。

排查线上报错

    preview manifest references an asset outside its task or draft

（前端旧包会把它错显示成「更新信息校验失败」）时的定位工具：把每个草稿保存的
图片清单 ``image_manifest_v2`` 里引用的 asset id，与
``product_processing_preview_image_assets`` 里实际存在的行逐一对账，输出：

* ``missing`` ：清单引用了库里根本没有的 id（从未注册 / 记录被清掉）；
* ``foreign`` ：清单引用了属于**别的任务或草稿**的 id，并指出它实际属于谁；
* 每个草稿的引用总数与对得上数量。

后端行为（`_save_preview_items`）：
* 「完成预检并导出」遇到 ``foreign``/``missing`` 会直接报上面那句错（现在会带上
  ``draft=`` 与 ``ids=[...]``）；
* 「保存预检修改」会把这些引用从清单里剔除后落库，并在响应里返回
  ``dropped_asset_ids``，前端提示用户重选。

只读：不写库、不发网络请求。

用法::

    python devtools/diagnose_preview_manifest_assets.py                 # 本机默认数据目录
    python devtools/diagnose_preview_manifest_assets.py --database <path\\workbench.sqlite3>
    python devtools/diagnose_preview_manifest_assets.py --workspace ws-1 --task 108
    python devtools/diagnose_preview_manifest_assets.py --json

退出码：0 = 全部对得上；1 = 发现对不上的引用（便于脚本/客服判断）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from wh_local.config import default_config  # noqa: E402
from wh_local.modules.product_processing.domain.preview_images import (  # noqa: E402
    MANIFEST_KEY,
    PreviewImageManifest,
)
from wh_local.modules.product_processing.infrastructure.database import (  # noqa: E402
    create_database,
)
from wh_local.modules.product_processing.infrastructure.orm import (  # noqa: E402
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
)
from wh_local.modules.product_processing.infrastructure.preview_image_orm import (  # noqa: E402
    PreviewImageAssetRow,
)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "") or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _referenced_ids(overrides: dict[str, Any]) -> tuple[str, ...]:
    if MANIFEST_KEY not in overrides:
        return ()
    manifest = PreviewImageManifest.from_value(overrides.get(MANIFEST_KEY))
    return tuple(dict.fromkeys((*manifest.live_asset_ids(), *manifest.library_asset_ids)))


def diagnose(
    database_url: str,
    *,
    workspace: str = "",
    task_id: int | None = None,
    draft_id: int | None = None,
) -> dict[str, Any]:
    database = create_database(database_url)
    with database.sessions() as session:
        tasks = {
            int(row.id): row for row in session.scalars(select(ProcessingTaskRow)).all()
        }
        items = session.scalars(select(ProcessingTaskItemRow)).all()
        drafts = {
            int(row.id): row
            for row in session.scalars(select(ProductDraftRow)).all()
        }
        assets = session.scalars(select(PreviewImageAssetRow)).all()

    asset_by_id = {str(row.id): row for row in assets}
    reports: list[dict[str, Any]] = []
    draft_rows: list[dict[str, Any]] = []

    for item in items:
        if item.product_draft_id is None:
            continue
        draft = drafts.get(int(item.product_draft_id))
        if draft is None:
            continue
        if task_id is not None and int(item.task_id) != int(task_id):
            continue
        if draft_id is not None and int(draft.id) != int(draft_id):
            continue
        if workspace and str(draft.workspace_id) != workspace:
            continue
        overrides = _loads(draft.preview_overrides_json)
        referenced = _referenced_ids(overrides)
        if not referenced:
            continue
        task = tasks.get(int(item.task_id))
        task_label = (
            f"#{item.task_id} {str(getattr(task, 'title', '') or '')[:40]}".strip()
        )
        draft_label = (
            f"draft={draft.id} skc={draft.skc or '-'} title={str(draft.title or '')[:40]}"
        )
        ok_ids: list[str] = []
        missing: list[str] = []
        foreign: list[dict[str, Any]] = []
        for asset_id in referenced:
            row = asset_by_id.get(asset_id)
            if row is None:
                missing.append(asset_id)
                continue
            if int(row.task_id) == int(item.task_id) and int(row.product_draft_id) == int(draft.id):
                ok_ids.append(asset_id)
                continue
            owner_task = tasks.get(int(row.task_id))
            foreign.append(
                {
                    "asset_id": asset_id,
                    "belongs_to_task": int(row.task_id),
                    "belongs_to_task_title": str(getattr(owner_task, "title", "") or "")[:40],
                    "belongs_to_draft": int(row.product_draft_id),
                    "origin": str(row.origin or ""),
                    "source_kind": str(row.source_kind or ""),
                }
            )
        draft_rows.append(
            {
                "task_id": int(item.task_id),
                "task": task_label,
                "workspace": str(draft.workspace_id),
                "draft_id": int(draft.id),
                "draft": draft_label,
                "preview_revision": int(draft.preview_revision or 0),
                "referenced": len(referenced),
                "ok": len(ok_ids),
                "missing": missing,
                "foreign": foreign,
            }
        )
        if missing or foreign:
            reports.append(draft_rows[-1])

    return {
        "database": database_url,
        "scanned_drafts_with_manifest": len(draft_rows),
        "problem_drafts": reports,
    }


def _print_human(result: dict[str, Any]) -> None:
    print(f"数据库：{result['database']}")
    print(f"扫描到带图片清单的草稿：{result['scanned_drafts_with_manifest']} 个")
    problems = result["problem_drafts"]
    if not problems:
        print("✅ 所有清单引用的图片都属于本任务/草稿，没有对不上的引用。")
        return
    print(f"❌ 发现 {len(problems)} 个草稿的清单引用了不属于本任务/草稿（或已不存在）的图片：\n")
    for row in problems:
        print(f"— 任务 {row['task']} | 工作区 {row['workspace']} | {row['draft']} "
              f"(revision={row['preview_revision']})")
        print(f"    引用 {row['referenced']} 个 id，其中对得上 {row['ok']} 个")
        for asset_id in row["missing"]:
            print(f"    [missing] {asset_id} —— 预览素材表里没有这行（未注册或已被清理）")
        for entry in row["foreign"]:
            print(
                f"    [foreign] {entry['asset_id']} —— 实际属于 任务#{entry['belongs_to_task']}"
                f"「{entry['belongs_to_task_title']}」/ 草稿#{entry['belongs_to_draft']}"
                f"（origin={entry['origin'] or '-'}, source_kind={entry['source_kind'] or '-'}）"
            )
        print()
    print("处理建议：")
    print("  1) 让用户在预检页点「重新加载」，把清单里这几张图重新选一遍（覆盖掉脏引用），再保存/导出；")
    print("  2) 若确认是跨任务/跨草稿串了，检查前端页面状态是否与当前任务对不上（长开页面 / 重新派发后再操作）；")
    print("  3) 保存（非导出）已由后端自愈：会剔除脏引用并在响应里返回 dropped_asset_ids；导出仍严格拦截。")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="预检图片清单对账（只读）")
    parser.add_argument("--database", default="", help="workbench.sqlite3 路径，默认取本机数据目录")
    parser.add_argument("--workspace", default="", help="只看某个工作区")
    parser.add_argument("--task", type=int, default=None, help="只看某个任务 id")
    parser.add_argument("--draft", type=int, default=None, help="只看某个草稿 id")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（便于转给支持同学）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.database:
        database_path = Path(args.database)
    else:
        database_path = Path(default_config().database_path)
    if not database_path.is_file():
        print(f"找不到数据库文件：{database_path}", file=sys.stderr)
        print("可用 --database 指定 workbench.sqlite3 的路径。", file=sys.stderr)
        return 2

    result = diagnose(
        f"sqlite:///{database_path.as_posix()}",
        workspace=args.workspace,
        task_id=args.task,
        draft_id=args.draft,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 1 if result["problem_drafts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
