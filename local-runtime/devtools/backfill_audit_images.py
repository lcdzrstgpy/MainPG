"""从核价审核表（标准审核表.xlsx）批量提取图片并回填利润活动产品记录。

背景
----
WPS 生成的审核表把图片以 DISPIMG 公式嵌入单元格（163MB 文件里是上千张图），
openpyxl 读不到这些图，所以导入时 image_path 为空。本脚本直接解析 xlsx 内部结构：

    sheet XML 单元格公式  DISPIMG("ID_xxx")
        -> xl/cellimages.xml（name=ID_xxx -> r:embed=rIdN）
        -> xl/_rels/cellimages.xml.rels（rIdN -> media/imageN.ext）
        -> 从 zip 中读出原始图片字节

每个 SKC 提取：
    C 列（skc对应图）            -> 商品主图（kind=product）
    AC/AE/AH 列（截图1/2/3）     -> 货源图（kind=source），按 AD/AF/AI 的 1688 链接分组

图片保存到资产目录 assets/{site}/{skc}/{kind}/ 下（与 save_asset 一致），
然后回填 profit_activity_records：
    image_path / source_image_path / source_groups_json

用法（在 local-runtime 目录下，使用后端同一 Python 环境）：
    python devtools/backfill_audit_images.py --xlsx "d:/syq/MainPG/标准审核表.xlsx"
    python devtools/backfill_audit_images.py --xlsx "..." --db "..." --asset-root "..." --dry-run
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

# 允许从任意 cwd 运行：把 local-runtime 加入模块搜索路径
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_RUNTIME = Path(__file__).resolve().parents[1]
if str(LOCAL_RUNTIME) not in sys.path:
    sys.path.insert(0, str(LOCAL_RUNTIME))

from wh_local.modules.profit_activity.infrastructure.assets import save_asset  # noqa: E402

# 资产根目录默认与 service._output_root() 一致（settings.save_root 为空时）
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "real-workbench" / "employee_workbench" / "outputs" / "profit_activity"
DEFAULT_DB = LOCAL_RUNTIME / "outputs" / "wh-local" / "workbench.sqlite3"

# 表头 -> 列：按表头文本定位列（对 美国站/哥伦比亚站 通用）
HEADERS = {
    "skc": "skc",
    "product_image": "skc对应图",
    "source_image_1": "截图1",
    "source_url_1": "1688产品对应链接",
    "source_image_2": "截图2",
    "source_url_2": "1688产品对应链接2",
    "source_image_3": "截图3",
    "source_url_3": "1688产品对应链接3",
}
SHEET_SITES = {
    "美国站": "US",
    "哥伦比亚站": "CO",
    "厄瓜多尔站": "EC",
}


def col_index(letters: str) -> int:
    """A -> 0, B -> 1 ..."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
    return n - 1


def norm_header(value: object) -> str:
    return str(value or "").strip().lower()


def load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        xml = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
    except KeyError:
        return []
    result: list[str] = []
    for si in re.findall(r"<si>(.*?)</si>", xml, re.S):
        parts = re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)
        # 正则读取未走 XML 解析器，需要手动反转义实体（如 &amp;）
        result.append(html.unescape("".join(parts)))
    return result


def build_id_to_media(zf: zipfile.ZipFile) -> dict[str, str]:
    """DISPIMG 图片 ID -> zip 内 media 路径。"""
    rid_to_target: dict[str, str] = {}
    try:
        rels = zf.read("xl/_rels/cellimages.xml.rels").decode("utf-8", errors="replace")
    except KeyError:
        return {}
    for rid, target in re.findall(r'<Relationship Id="(rId\d+)"[^>]*Target="([^"]+)"', rels):
        rid_to_target[rid] = target

    id_to_media: dict[str, str] = {}
    try:
        xml = zf.read("xl/cellimages.xml").decode("utf-8", errors="replace")
    except KeyError:
        return id_to_media
    for block in re.findall(r"<etc:cellImage>(.*?)</etc:cellImage>", xml, re.S):
        name_m = re.search(r'<xdr:cNvPr[^>]*name="(ID_[A-Za-z0-9]+)"', block)
        embed_m = re.search(r"<a:blip[^>]*r:embed=\"(rId\d+)\"", block)
        if name_m and embed_m:
            target = rid_to_target.get(embed_m.group(1))
            if target:
                id_to_media[name_m.group(1)] = f"xl/{target}"
    return id_to_media


def parse_sheet_cells(xml: str, shared: list[str]) -> dict[int, dict[int, object]]:
    """解析工作表 XML，返回 {行号: {列下标: 值}}；DISPIMG 单元格的值为其图片 ID。"""
    cells_by_row: dict[int, dict[int, object]] = {}
    for rownum, body in re.findall(r'<row r="(\d+)"[^>]*>(.*?)</row>', xml, re.S):
        row_cells: dict[int, object] = {}
        for cm in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', body, re.S):
            col_letters, attrs, content = cm.group(1), cm.group(2), cm.group(3) or ""
            if "t=\"s\"" in attrs:
                m = re.search(r"<v>(\d+)</v>", content)
                row_cells[col_index(col_letters)] = shared[int(m.group(1))] if m and int(m.group(1)) < len(shared) else None
            elif "t=\"str\"" in attrs:
                img = re.search(r'_xlfn\.DISPIMG\(&quot;(ID_[A-Za-z0-9]+)&quot;,1\)', content)
                if img:
                    row_cells[col_index(col_letters)] = ("__DISPIMG__", img.group(1))
                else:
                    m = re.search(r"<v>(.*?)</v>", content)
                    row_cells[col_index(col_letters)] = m.group(1) if m else None
            else:
                m = re.search(r"<v>(.*?)</v>", content)
                row_cells[col_index(col_letters)] = m.group(1) if m else None
        cells_by_row[int(rownum)] = row_cells
    return cells_by_row


def extract_images(xlsx: str) -> tuple[dict[str, dict], dict[str, int]]:
    """返回 {skc: {product, sources}} 与统计信息。"""
    zf = zipfile.ZipFile(xlsx)
    shared = load_shared_strings(zf)
    id_to_media = build_id_to_media(zf)

    workbook_xml = zf.read("xl/workbook.xml").decode("utf-8", errors="replace")
    workbook_rels = zf.read("xl/_rels/workbook.xml.rels").decode("utf-8", errors="replace")
    rid_to_sheet = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="(worksheets/[^"]+\.xml)"', workbook_rels))
    sheet_names = re.findall(r'<sheet[^>]*name="([^"]+)"[^>]*r:id="(rId\d+)"', workbook_xml)

    skc_by_site: dict[str, dict[str, dict]] = {}
    stats: dict[str, int] = {"sheets": 0, "rows_with_images": 0, "product_images": 0, "source_images": 0, "unresolved_ids": 0}
    seen_ids: set[str] = set()

    for title, rid in sheet_names:
        site = None
        for key, code in SHEET_SITES.items():
            if key in title:
                site = code
                break
        sheet_path = rid_to_sheet.get(rid)
        if site is None or not sheet_path:
            continue
        xml = zf.read(f"xl/{sheet_path}").decode("utf-8", errors="replace")
        cells = parse_sheet_cells(xml, shared)

        # 从表头行（第 1 行）定位各列
        header_cells = cells.get(1, {})
        col_map: dict[str, int | None] = {}
        for field, label in HEADERS.items():
            for idx, value in header_cells.items():
                if norm_header(value) == label:
                    col_map[field] = idx
                    break
            else:
                col_map[field] = None

        skc_col = col_map.get("skc")
        product_col = col_map.get("product_image")
        source_pairs = [
            (col_map.get(f"source_image_{i}"), col_map.get(f"source_url_{i}")) for i in (1, 2, 3)
        ]
        if skc_col is None:
            continue

        stats["sheets"] += 1
        by_skc = skc_by_site.setdefault(site, {})
        for rownum, row_cells in cells.items():
            skc = str(row_cells.get(skc_col) or "").strip()
            if not skc:
                continue
            if skc in by_skc:
                continue
            product_media = None
            if product_col is not None:
                value = row_cells.get(product_col)
                if isinstance(value, tuple) and value[0] == "__DISPIMG__":
                    product_media = id_to_media.get(value[1])
                    seen_ids.add(value[1])
                    if product_media is None:
                        stats["unresolved_ids"] += 1
            sources: list[tuple[str | None, str]] = []
            for img_col, url_col in source_pairs:
                url = str(row_cells.get(url_col) or "").strip() if url_col is not None else ""
                if img_col is None:
                    continue
                value = row_cells.get(img_col)
                if isinstance(value, tuple) and value[0] == "__DISPIMG__":
                    media = id_to_media.get(value[1])
                    seen_ids.add(value[1])
                    if media is None:
                        stats["unresolved_ids"] += 1
                        continue
                    sources.append((media, url))
            if product_media is None and not sources:
                continue
            by_skc[skc] = {"product": product_media, "sources": sources}
            stats["rows_with_images"] += 1
            if product_media:
                stats["product_images"] += 1
            stats["source_images"] += len(sources)

    stats["sheets_with_skc"] = len(skc_by_site)
    zf.close()
    return skc_by_site, stats


def resolve_media_bytes(xlsx: str, media_path: str) -> tuple[bytes, str] | None:
    """读取 media 字节与扩展名。"""
    with zipfile.ZipFile(xlsx) as zf:
        try:
            content = zf.read(media_path)
        except KeyError:
            return None
    return content, Path(media_path).suffix


def main() -> None:
    parser = argparse.ArgumentParser(description="从核价审核表提取图片并回填利润活动产品记录")
    parser.add_argument("--xlsx", required=True, help="核价审核表路径（标准审核表.xlsx）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"workbench.sqlite3 路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--asset-root", default=str(DEFAULT_ASSET_ROOT), help=f"资产根目录（默认 {DEFAULT_ASSET_ROOT}）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做的修改，不写库、不落盘")
    args = parser.parse_args()

    xlsx = Path(args.xlsx)
    if not xlsx.is_file():
        sys.exit(f"找不到文件: {xlsx}")

    print(f"[1/3] 解析 {xlsx.name}（大文件，需要一点时间）...")
    skc_by_site, stats = extract_images(str(xlsx))
    total_skc = sum(len(v) for v in skc_by_site.values())
    total_imgs = stats["product_images"] + stats["source_images"]
    print(f"  完成：{stats['sheets_with_skc']} 个站点表，{total_skc} 个 SKC 有图，共 {total_imgs} 张"
          f"（主图 {stats['product_images']}、货源图 {stats['source_images']}）"
          + (f"，{stats['unresolved_ids']} 个图片 ID 无法解析" if stats["unresolved_ids"] else ""))

    print(f"[2/3] 连接数据库 {args.db} ...")
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    records = conn.execute(
        "SELECT id, workspace_id, site_code, skc, image_path, source_image_path, source_groups_json "
        "FROM profit_activity_records"
    ).fetchall()
    print(f"  共 {len(records)} 条产品记录")

    matched = 0
    updated = 0
    root = Path(args.asset_root)
    root.mkdir(parents=True, exist_ok=True)

    # skc -> (site, 图片信息)
    skc_lookup: dict[str, tuple[str, dict]] = {}
    for site, by_skc in skc_by_site.items():
        for skc, images in by_skc.items():
            if skc not in skc_lookup:
                skc_lookup[skc] = (site, images)

    for record in records:
        item = skc_lookup.get(record["skc"])
        if item is None:
            continue
        matched += 1
        site, images = item
        site_code = record["site_code"] or site

        image_path = ""
        if images.get("product"):
            resolved = resolve_media_bytes(str(xlsx), images["product"])
            if resolved:
                content, ext = resolved
                image_path = save_asset(root, site=site_code, skc=record["skc"], kind="product",
                                        filename=f"skc{ext}", content=content)

        # 合并式回填：以现有 source_groups_json 为底（保留 cost 等字段），
        # 按 source_url 把审核表截图并入对应货源组的 image_paths，避免整体覆盖丢失数据。
        try:
            existing_groups = json.loads(record["source_groups_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            existing_groups = []
        groups_by_url: dict[str, dict] = {}
        ordered: list[dict] = []
        for group in existing_groups if isinstance(existing_groups, list) else []:
            if not isinstance(group, dict):
                continue
            group = dict(group)
            group.setdefault("image_paths", [])
            ordered.append(group)
            url = str(group.get("source_url") or "").strip()
            if url and url not in groups_by_url:
                groups_by_url[url] = group
        for media_path, url in images["sources"]:
            resolved = resolve_media_bytes(str(xlsx), media_path)
            if not resolved:
                continue
            content, ext = resolved
            saved = save_asset(root, site=site_code, skc=record["skc"], kind="source",
                               filename=f"source{ext}", content=content)
            group = groups_by_url.get(url)
            if group is None:
                group = {"source_url": url, "image_paths": [], "cost": None}
                ordered.append(group)
                if url:
                    groups_by_url[url] = group
            if saved not in group["image_paths"]:
                group["image_paths"].append(saved)
        source_groups = [g for g in ordered if g["image_paths"] or g["source_url"]]
        source_image_path = next((p for g in source_groups for p in g["image_paths"] if p), "")

        if args.dry_run:
            print(f"  [dry-run] skc={record['skc']} site={site_code} "
                  f"product={'有' if image_path else '无'} source={len(source_groups)}组")
            continue

        conn.execute(
            "UPDATE profit_activity_records SET image_path=?, source_image_path=?, source_groups_json=?, updated_at=datetime('now') WHERE id=?",
            (image_path, source_image_path, json.dumps(source_groups, ensure_ascii=False, separators=(",", ":")), record["id"]),
        )
        updated += 1

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"[3/3] 完成：{matched} 条记录匹配到审核表图片，{'更新 ' + str(updated) + ' 条' if not args.dry_run else '（dry-run，未写入）'}")
    if matched < len(records):
        print(f"  提示：{len(records) - matched} 条记录未在审核表中找到对应图片（可能来自其他导入文件）。")


if __name__ == "__main__":
    main()
