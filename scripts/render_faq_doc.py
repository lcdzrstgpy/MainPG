#!/usr/bin/env python3
"""把操作答疑 FAQ 库渲染成可读的 Markdown 清单，供人工审阅。

数据源：local-runtime/wh_local/modules/help_agent/data/faqs.json
输出：  docs/操作答疑FAQ清单.md

用法：
    python scripts/render_faq_doc.py            # 写文件
    python scripts/render_faq_doc.py --check     # 只比对，不写（供 CI / 提交前自查）

注意：本文件生成的内容**只读**。要改答案请改 faqs.json，别改生成的 md（会被覆盖）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAQS = REPO / "local-runtime" / "wh_local" / "modules" / "help_agent" / "data" / "faqs.json"
OUT = REPO / "docs" / "操作答疑FAQ清单.md"

# 分类展示顺序（未列出的分类排在后面，按出现顺序）
CATEGORY_ORDER = [
    "开始使用",
    "报错与处理",
    "功能导航",
    "产品处理 · 采集",
    "产品处理 · 草稿池",
    "产品处理 · 处理流程",
    "产品处理 · SKU规格图",
    "产品处理 · 尺寸画布",
    "产品处理 · 导出",
    "产品处理 · 历史记录",
    "POD定制",
    "核价及货源 · 数据采集",
    "核价及货源 · STEP 01 数据初筛",
    "核价及货源 · STEP 02 1688报价审核",
    "核价及货源 · STEP 03 产品-货源关联",
    "利润活动 · 站点费率",
    "利润活动 · 单品利润",
    "利润活动 · 产品资料导入",
    "利润活动 · 活动过滤",
    "产品库 · 查询",
    "产品库 · 图片与货源",
    "产品库 · 行内编辑",
    "产品库 · 删除与批量操作",
    "积分与充值",
    "积分与计费",
    "数据与隐私",
    "问题反馈",
    "系统与版本",
]

# 分类一句话导读（可选，没有就不写）
CATEGORY_BLURB = {
    "开始使用": "注册、装插件、连工作台、账号相关。第一次用先看这里。",
    "报错与处理": "照着界面上的报错原文答案，含登录注册、采集、处理、导出、插件、更新等常见故障。",
    "功能导航": "左侧导航每个模块是干什么的，找不到功能时看这里。",
    "产品处理 · 采集": "采集商品、按什么条件筛、采集完怎么入池。",
    "产品处理 · 草稿池": "草稿从哪来、怎么批量筛选和编辑。",
    "产品处理 · 处理流程": "开始处理要配什么、预检怎么走、什么情况能进预检。",
    "产品处理 · SKU规格图": "规格图怎么传、三个策略怎么选、为什么不生效。",
    "产品处理 · 尺寸画布": "尺寸线怎么画、尺寸和物流包裹尺寸的区别、审核机制。",
    "产品处理 · 导出": "导出到店小秘和妙手，服饰与非服饰模板怎么选。",
    "POD定制": "POD 模版、风格描述、生成失败怎么办、导出表格。",
    "核价及货源 · 数据采集": "核价数据怎么从 Temu 采到工作台。",
    "利润活动 · 站点费率": "各站点算利润的公共参数在哪填。",
    "利润活动 · 单品利润": "单品利润怎么算、成本包含什么。",
    "产品库 · 查询": "按站点、店铺、商品 ID 查产品。",
    "积分与充值": "积分是什么、怎么充、退款规则。",
    "问题反馈": "答疑答不上来时怎么找我们。",
    "系统与版本": "检查更新、版本升级。",
}


def _fmt_count(n: int) -> str:
    return f"{n} 条"


def build(data: dict) -> str:
    faqs = data["faqs"]
    version = data.get("version", 0)

    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for faq in faqs:
        groups.setdefault(str(faq.get("category") or "未分类"), []).append(faq)

    ordered = [c for c in CATEGORY_ORDER if c in groups]
    ordered += [c for c in groups if c not in ordered]

    lines: list[str] = []
    lines.append("# 操作答疑智能体 · 问答清单")
    lines.append("")
    lines.append(
        f"> 共 **{len(faqs)}** 条问答，数据版本 v{version}，{len(groups)} 个分类。"
    )
    lines.append(
        "> 本清单是 `faqs.json` 的可读版本，便于人工审阅。"
        "**改动请改 JSON，不要改这里**（本文件由 `scripts/render_faq_doc.py` 生成，会被覆盖）。"
    )
    lines.append("")
    lines.append("**生成命令**：`python scripts/render_faq_doc.py`")
    lines.append("")
    lines.append("**答案格式提醒**：答疑面板是纯文本渲染，不解析 Markdown。"
                 "答案里不要出现 `**加粗**`、`·`、`——`、`①②③`，"
                 "有序步骤写成 `1. 2. 3.`。详见 `data/README.md`。")
    lines.append("")
    lines.append("---")
    lines.append("")

    for category in ordered:
        items = groups[category]
        lines.append(f"## {category}（{_fmt_count(len(items))}）")
        lines.append("")
        blurb = CATEGORY_BLURB.get(category)
        if blurb:
            lines.append(blurb)
            lines.append("")
        for index, faq in enumerate(items, start=1):
            lines.append(f"### {index}. {faq.get('question', '')}")
            lines.append("")
            lines.append(f"`{faq.get('id', '')}`")
            if faq.get("draft"):
                lines.append("")
                lines.append("> ⚠️ `draft: true`：自动抽取、尚未人工确认。")
            lines.append("")
            lines.append(str(faq.get("answer", "")).strip("\n"))
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args()

    data = json.loads(FAQS.read_text(encoding="utf-8"))
    rendered = build(data)

    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current == rendered:
            print(f"清单与数据一致（{len(data['faqs'])} 条）")
            return 0
        print("清单与数据不一致，请运行 python scripts/render_faq_doc.py")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"已生成 {OUT.relative_to(REPO)}（{len(data['faqs'])} 条，{rendered.count(chr(10))} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
