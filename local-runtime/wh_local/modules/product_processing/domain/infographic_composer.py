"""信息图版式：对标 A+ 详情页的两张 1:1 营销图。

    infographic_hero     卖点 + 利益点 + 2×2 场景/细节卡（图 A）
    infographic_details  细节清单 + 大图编号引线 + 尺寸标注（图 B）

与既有 S1-S4 骨架的区别：这两张是「多区块信息图」，不是单张照片排版。
所有文字、图标、引线仍由本地 HTML/CSS 确定性绘制，模型只负责照片层。

场景卡当前由 hero 的局部裁切占位；后续接入带手场景图时，只需把
``cards`` 换成生成图，版式与渲染不用改。
"""

from __future__ import annotations

import html
from io import BytesIO
from typing import Any, Sequence

from PIL import Image

from .detail_specs import FALLBACK_REGIONS as _FALLBACK_REGIONS

# 24×24 线性图标（Feather 风格）；内联 SVG，无外部依赖。
_ICON_PATHS: dict[str, str] = {
    "shield": '<path d="M12 3l7 3v5c0 4.4-2.9 7.4-7 9-4.1-1.6-7-4.6-7-9V6z"/>',
    "grip": (
        '<path d="M7 12V6a2 2 0 0 1 4 0v5"/>'
        '<path d="M11 11V4a2 2 0 0 1 4 0v7"/>'
        '<path d="M15 11V6a2 2 0 0 1 4 0v8a6 6 0 0 1-6 6h-2a6 6 0 0 1-6-6v-2"/>'
    ),
    "tools": (
        '<path d="M14.7 6.3a4 4 0 0 1-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 1 '
        '5.4-5.4l-2.3 2.3 1.4 1.4 2.3-2.3z"/>'
    ),
    "zap": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    "ruler": '<path d="M3 15l12-12 6 6-12 12H3z"/><path d="M8 10l2 2M11 7l2 2M5 13l2 2"/>',
    "bike": (
        '<circle cx="6" cy="17" r="3.5"/><circle cx="18" cy="17" r="3.5"/>'
        '<path d="M6 17l4-6h5l3 6M10 11l-2-3h3"/>'
    ),
    "tree": '<path d="M12 3l5 7h-3l4 5H6l4-5H7z"/><path d="M12 15v6"/>',
    "home": '<path d="M3 11l9-8 9 8"/><path d="M5 10v11h14V10"/>',
    "droplet": '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
    "wrench": (
        '<path d="M21 4a5 5 0 0 1-6.7 6.7L6 19l-3 1 1-3 8.3-8.3A5 5 0 0 1 '
        '19 3l-3 3 2 2z"/>'
    ),
}

_BENEFIT_ICONS = ("shield", "grip", "tools", "zap")
_CASE_ICONS = ("wrench", "home", "bike", "tree")


def icon_svg(name: str, color: str, size: int, stroke_width: float = 1.8) -> str:
    """返回内联 SVG 图标；未知名称回落到对勾。"""

    path = _ICON_PATHS.get(str(name or ""), _ICON_PATHS["check"])
    return (
        f'<svg viewBox="0 0 24 24" width="{int(size)}" height="{int(size)}" fill="none" '
        f'stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{path}</svg>'
    )


def _escape(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def _data_url(content: bytes) -> str:
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(bytes(content or b"")).decode()


def crop_region(content: bytes, box: tuple[float, float, float, float]) -> bytes:
    """按归一化坐标从 hero 裁一块 JPEG（场景卡占位用）。"""

    with Image.open(BytesIO(bytes(content or b""))) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    x0, y0, x1, y1 = box
    left = max(0, min(width - 1, int(round(width * x0))))
    top = max(0, min(height - 1, int(round(height * y0))))
    right = max(left + 1, min(width, int(round(width * x1))))
    bottom = max(top + 1, min(height, int(round(height * y1))))
    buffer = BytesIO()
    image.crop((left, top, right, bottom)).save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _headline_html(headline: str) -> str:
    """标题分两行，最后一行用强调色（对标样图的白+橙层级）。"""

    words = str(headline or "").strip().split()
    if not words:
        words = ["Product"]
    if len(words) >= 4:
        cut = max(1, len(words) // 2)
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    else:
        lines = [" ".join(words)]
    if len(lines) == 1:
        return _escape(lines[0])
    return f"{_escape(lines[0])}<br><span>{_escape(lines[1])}</span>"


def _benefit_labels(
    copy: Any, details: Sequence[tuple[bytes, str]]
) -> list[str]:
    labels = [str(item or "").strip() for item in (getattr(copy, "bullets", []) or [])]
    labels = [item for item in labels if item]
    if len(labels) < 3:
        extra = [str(label or "").strip() for _content, label in details]
        labels.extend(item for item in extra if item and item not in labels)
    return labels[:3]


def _cards(
    details: Sequence[tuple[bytes, str]],
    hero: bytes,
    copy: Any,
) -> list[tuple[bytes, str, str]]:
    """场景卡：只用真实细节图（最多 4 张），不足不硬凑。

    以前不足 4 张会从 hero 裁固定区域补齐——那种小卡往往是一块看不清的背景色
    （「一坨」的来源）。现在宁可少放卡，也只放真实近景；卡片区按实际张数自适应。
    """

    return [
        (content, str(label or "").strip(), "")
        for content, label in details[:4]
        if content and str(label or "").strip()
    ]


def _benefits_html(copy: Any, details: Sequence[tuple[bytes, str]]) -> str:
    labels = _benefit_labels(copy, details)
    rows = []
    for index, label in enumerate(labels):
        icon = _BENEFIT_ICONS[index % len(_BENEFIT_ICONS)]
        rows.append(
            f'<div class="benefit"><div class="bic">{icon_svg(icon, "#ffffff", 34)}</div>'
            f'<b>{_escape(label)}</b></div>'
        )
    return "".join(rows)


def _cards_html(
    cards: Sequence[tuple[bytes, str, str]], accent: str, accent_light: str
) -> str:
    rows = []
    for index, (content, title, caption) in enumerate(cards):
        icon = _CASE_ICONS[index % len(_CASE_ICONS)]
        caption_html = f"<span>{_escape(caption)}</span>" if caption else ""
        rows.append(
            f'<div class="case"><img src="{_data_url(content)}" alt="">'
            f'<div class="case-badge">{icon_svg(icon, "#ffffff", 26)}</div>'
            f'<div class="case-cap"><b>{_escape(title)}</b>{caption_html}</div></div>'
        )
    return "".join(rows)


def _details_html(details: Sequence[tuple[bytes, str]]) -> str:
    rows = []
    for index, (content, label) in enumerate(details[:5]):
        rows.append(
            f'<div class="drow"><div class="dthumb"><img src="{_data_url(content)}" alt=""></div>'
            f'<div class="dcopy"><b>{_escape(label)}</b></div>'
            f'<span class="dnum">{index + 1}</span></div>'
        )
    return "".join(rows)


def _callout_svg(
    parts: Sequence[dict], *, size: int, accent: str, accent_light: str
) -> str:
    """编号引线：商品上的圆点 → 右侧编号圆。全部按 size 等比换算。"""

    if not parts:
        return ""
    pane_left = 0.4125 * size
    pane_top = 0.05 * size
    pane_width = 0.55 * size
    pane_height = 0.90 * size
    side = min(pane_width, pane_height)
    offset_x = pane_left + (pane_width - side) / 2
    offset_y = pane_top + (pane_height - side) / 2
    label_x = 0.945 * size
    stroke = max(1.0, size * 0.0026)
    dot_r = size * 0.008
    num_r = size * 0.021
    entries: list[tuple[int, float, float]] = []
    for index, part in enumerate(list(parts)[:5]):
        try:
            x = float(part.get("x")) / 1000.0
            y = float(part.get("y")) / 1000.0
        except (TypeError, ValueError):
            continue
        entries.append((index, offset_x + x * side, offset_y + y * side))
    # 编号按原顺序；右侧槽位按部位的实际高度排序分配，引线不会互相交叉。
    slot_order = sorted(range(len(entries)), key=lambda item: entries[item][2])
    marks: list[str] = []
    for slot_index, entry_index in enumerate(slot_order):
        number, px, py = entries[entry_index]
        slot_y = 0.16 * size + slot_index * 0.14 * size
        elbow_x = label_x - num_r * 3.4
        marks.append(
            f'<path d="M{px:.1f},{py:.1f} L{elbow_x:.1f},{slot_y:.1f} L{label_x - num_r:.1f},{slot_y:.1f}" '
            f'fill="none" stroke="{accent}" stroke-width="{stroke:.2f}" opacity=".92"/>'
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{dot_r:.1f}" fill="{accent_light}"/>'
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{dot_r * 2.1:.1f}" fill="none" '
            f'stroke="{accent_light}" stroke-width="{stroke:.2f}" opacity=".55"/>'
            f'<circle cx="{label_x:.1f}" cy="{slot_y:.1f}" r="{num_r:.1f}" fill="{accent}"/>'
            f'<text x="{label_x:.1f}" y="{slot_y:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{size * 0.024:.1f}" font-weight="800" '
            f'fill="#ffffff">{number + 1}</text>'
        )
    return (
        f'<svg viewBox="0 0 {int(size)} {int(size)}" width="{int(size)}" height="{int(size)}" '
        f'style="position:absolute;inset:0;pointer-events:none;z-index:3">{"".join(marks)}</svg>'
    )


def _hero_document(
    *,
    hero: bytes,
    copy: Any,
    details: Sequence[tuple[bytes, str]],
    category_id: str,
    size: int,
    style_template: str = "auto",
) -> str:
    from .html_composer import _promo_palette, font_pair  # noqa: PLC0415
    from .marketing_gen import resolve_style_template, style_is_bright  # noqa: PLC0415

    palette = _promo_palette(category_id, hero)
    accent = palette["accent"]
    accent_dark = palette["dark"]
    accent_light = palette["light"]
    head_font, body_font = font_pair(category_id)
    # 共通模板优先：工具也能选亮色，包袋也能选暗色；auto 才按品类回落。
    style = resolve_style_template(style_template, category_id)
    bright = style_is_bright(style)
    if style == "white":
        page = "#ffffff"
        stage_bg = "linear-gradient(160deg,#ffffff 0%,#fbfbfb 55%,#f5f5f5 100%)"
    elif bright:
        page = "#f4efe6"
        stage_bg = "linear-gradient(160deg,#faf6ef 0%,#f2ebdf 55%,#ece2d3 100%)"
    else:
        page = "#08090b"
        stage_bg = "linear-gradient(160deg,#101216 0%,#0a0b0e 55%,#07080a 100%)"
    ink = "#1f2328" if bright else "#fff"
    sub_ink = "rgba(31,35,40,.78)" if bright else "rgba(255,255,255,.82)"
    kicker_ink = "var(--acc)" if bright else "var(--acc-light)"
    # 亮色遮罩只兜左侧文字区：42% 后快速衰减、48% 完全透明，商品（右 55%）不被洗白。
    veil = (
        "linear-gradient(90deg,rgba(250,246,239,.97) 0%,rgba(250,246,239,.95) 22%,"
        "rgba(250,246,239,.86) 30%,rgba(250,246,239,.55) 37%,"
        "rgba(250,246,239,.12) 43%,rgba(250,246,239,0) 48%)"
        if bright
        else "linear-gradient(90deg,rgba(6,7,9,.97) 0%,rgba(6,7,9,.90) 36%,rgba(6,7,9,.45) 58%,"
        "rgba(6,7,9,.04) 76%,rgba(6,7,9,.18) 100%),"
        "linear-gradient(180deg,rgba(6,7,9,.55) 0%,rgba(6,7,9,.04) 32%,rgba(6,7,9,.12) 62%,"
        "rgba(6,7,9,.74) 100%)"
    )
    left_width = "42%" if bright else "50%"
    grain = "rgba(31,35,40,.025)" if bright else "rgba(255,255,255,.018)"
    h1_shadow = (
        "0 6px 22px rgba(255,255,255,.30)" if bright else "0 10px 36px rgba(0,0,0,.6)"
    )
    case_bg = "#ffffff" if bright else "#111317"
    case_shadow = (
        "0 14px 32px rgba(60,48,32,.16)" if bright else "0 14px 32px rgba(0,0,0,.46)"
    )
    cap_bg = (
        "linear-gradient(0deg,rgba(255,255,255,.95),rgba(255,255,255,.55) 62%,transparent)"
        if bright
        else "linear-gradient(0deg,rgba(0,0,0,.88),rgba(0,0,0,.35) 62%,transparent)"
    )
    blur_filter = "blur(30px) brightness(1.05)" if bright else "blur(30px) brightness(.55)"
    cards = _cards(details, hero, copy)
    benefits = _benefits_html(copy, details)
    headline = _headline_html(getattr(copy, "headline", ""))
    subline = _escape(getattr(copy, "subline", ""))
    subline_html = (
        f'<p class="sub" data-max-height="{size * 0.07:.0f}" '
        f'data-min-height="{size * 0.013:.0f}">{subline}</p>'
        if subline
        else ""
    )
    # 卡片区按真实张数自适应：4 张才分「左下 2×2 / 底部 1×4」，1-3 张单行卡带，
    # 0 张不画卡区（hero 满幅、不硬凑）。商品碰到左下卡带时改用底部卡带，不压商品。
    placement = "band"
    card_count = len(cards)
    cards_right = 0.46 if bright else 0.54
    if card_count == 0:
        placement = "none"
    elif card_count == 4:
        try:
            from .dimension_source_reuse import subject_bbox_ratios  # noqa: PLC0415

            bbox = subject_bbox_ratios(hero)
        except Exception:  # noqa: BLE001 - 抠图不可用就走底部卡带
            bbox = None
        if bbox:
            left_ratio, top_ratio, right_ratio, bottom_ratio = bbox
            overlap_x = min(right_ratio, cards_right) - max(left_ratio, 0.04)
            overlap_y = min(bottom_ratio, 0.96) - max(top_ratio, 0.62)
            if not (overlap_x > 0 and overlap_y > 0):
                placement = "left"
    case_cols = card_count if placement == "band" else 2
    cases_html = (
        f'<div class="cases is-{placement}">{_cards_html(cards, accent, accent_light)}</div>'
        if cards
        else ""
    )
    blur_html = (
        f'<img class="bg-blur" src="{_data_url(hero)}" alt="">'
        if placement == "band"
        else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{size}px;height:{size}px;overflow:hidden;background:{page}}}
.stage{{position:relative;width:{size}px;height:{size}px;overflow:hidden;
  font-family:{head_font};color:{ink};--acc:{accent};--acc2:{accent_dark};--acc-light:{accent_light};
  background:{stage_bg}}}
.bg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:74% center;
  filter:{'saturate(1.10) contrast(1.10)' if bright else 'saturate(1.06) contrast(1.03)'}}}
.veil{{position:absolute;inset:0;background:{veil}}}
.glow{{position:absolute;inset:0;background:
  radial-gradient(70% 50% at 84% 8%,var(--acc-light),transparent 62%);opacity:{'.07' if bright else '.18'}}}
.grain{{position:absolute;inset:0;pointer-events:none;z-index:1;background:
  repeating-linear-gradient(115deg,{grain} 0 2px,transparent 2px 30px)}}
.left{{position:absolute;left:4%;top:5.5%;width:{left_width};display:flex;flex-direction:column;z-index:2}}
.kicker{{font-family:{body_font};font-size:{size * 0.015}px;letter-spacing:6px;
  color:{kicker_ink};font-weight:700;text-transform:uppercase;margin-bottom:{size * 0.010}px}}
h1{{font-size:{size * 0.042}px;line-height:.99;letter-spacing:-1px;text-transform:uppercase;
  font-style:italic;text-shadow:{h1_shadow};overflow-wrap:anywhere}}
h1 span{{color:var(--acc)}}
.sub{{margin-top:{size * 0.012}px;font-family:{body_font};font-size:{size * 0.017}px;
  line-height:1.35;color:{sub_ink};max-width:96%}}
.ribbon{{margin-top:{size * 0.020}px;width:78%;height:{size * 0.036}px;
  background:linear-gradient(90deg,var(--acc),var(--acc2));display:flex;align-items:center;
  padding-left:{size * 0.018}px;transform:skewX(-9deg);
  box-shadow:0 12px 30px rgba(0,0,0,.45)}}
.ribbon b{{transform:skewX(9deg);font-family:{body_font};font-size:{size * 0.015}px;
  letter-spacing:2px;text-transform:uppercase;color:#fff}}
.benefits{{margin-top:{size * 0.032}px;display:flex;flex-direction:column;gap:{size * 0.019}px}}
.benefit{{display:flex;align-items:center;gap:{size * 0.012}px}}
.bic{{width:{size * 0.040}px;height:{size * 0.040}px;flex:0 0 {size * 0.040}px;
  border-radius:{size * 0.011}px;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(160deg,var(--acc),var(--acc2));
  box-shadow:0 10px 24px rgba(0,0,0,.42)}}
.benefit b{{font-size:{size * 0.017}px;line-height:1.12;text-transform:uppercase;
  letter-spacing:.2px;max-width:{size * 0.36}px;overflow-wrap:anywhere}}
/* 2×2 小卡固定在左下；如果商品会碰到左下卡片区，直接改为底部 1×4
   小卡带（hero 收到上方 75%），不会把卡片挪到右侧去压商品。 */
.cases{{position:absolute;left:4%;bottom:4%;width:{left_width};height:34%;
  display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;
  gap:{size * 0.010}px;z-index:2}}
.cases.is-left{{left:4%;right:auto}}
.cases.is-band{{left:4%;right:4%;bottom:3%;width:auto;height:24%;
  grid-template-columns:repeat({case_cols},1fr);grid-template-rows:1fr}}
.bg-blur{{position:absolute;left:0;top:0;width:100%;height:75%;object-fit:cover;
  filter:{blur_filter}}}
.bg.is-band{{height:75%;object-fit:contain}}
.case{{position:relative;border:2px solid var(--acc);border-radius:{size * 0.009}px;
  overflow:hidden;background:{case_bg};box-shadow:{case_shadow}}}
.case img{{width:100%;height:100%;object-fit:cover;display:block}}
.case-badge{{position:absolute;left:{size * 0.008}px;top:{size * 0.008}px;
  width:{size * 0.030}px;height:{size * 0.030}px;border-radius:50%;
  background:var(--acc);display:flex;align-items:center;justify-content:center;
  box-shadow:0 8px 18px rgba(0,0,0,.45)}}
.case-cap{{position:absolute;left:0;right:0;bottom:0;padding:{size * 0.011}px {size * 0.010}px;
  background:{cap_bg}}}
.case-cap b{{display:block;font-size:{size * 0.0145}px;line-height:1.1;text-transform:uppercase;
  letter-spacing:.3px}}
.cases.is-band .case-cap b{{font-size:{size * 0.0125}px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}}
</style></head><body><div class="stage">
{blur_html}
<img class="bg{' is-band' if placement == 'band' else ''}" src="{_data_url(hero)}" alt="">
<div class="veil"></div><div class="glow"></div>
<div class="grain"></div>
<div class="left">
  <div class="kicker">Premium Details</div>
  <h1 data-max-height="{size * 0.18:.0f}" data-min-height="{size * 0.026:.0f}">{headline}</h1>
  {subline_html}
  <div class="ribbon"><b>Product Highlights</b></div>
  <div class="benefits">{benefits}</div>
</div>
{cases_html}
</div></body></html>"""


def _details_document(
    *,
    hero: bytes,
    copy: Any,
    details: Sequence[tuple[bytes, str]],
    callout_parts: Sequence[dict],
    category_id: str,
    size: int,
    style_template: str = "auto",
) -> str:
    from .html_composer import _promo_palette, font_pair  # noqa: PLC0415
    from .marketing_gen import resolve_style_template, style_is_bright  # noqa: PLC0415

    palette = _promo_palette(category_id, hero)
    accent = palette["accent"]
    accent_dark = palette["dark"]
    accent_light = palette["light"]
    head_font, body_font = font_pair(category_id)
    style = resolve_style_template(style_template, category_id)
    bright = style_is_bright(style)
    page = "#ffffff" if style == "white" else ("#f4efe6" if bright else "#08090b")
    ink = "#1f2328" if bright else "#fff"
    grain = "rgba(31,35,40,.025)" if bright else "rgba(255,255,255,.02)"
    sub_ink = "rgba(31,35,40,.70)" if bright else "rgba(255,255,255,.72)"
    row_bg = (
        "rgba(255,255,255,.92)"
        if style == "white"
        else ("rgba(255,255,255,.72)" if bright else "rgba(255,255,255,.035)")
    )
    row_border = "rgba(31,35,40,.08)" if bright else "rgba(255,255,255,.07)"
    pane_bg = "#ffffff" if bright else "#101216"
    pane_shadow = (
        "0 26px 60px rgba(60,48,32,.16)" if bright else "0 26px 60px rgba(0,0,0,.5)"
    )
    # 模糊补底只在暗色用，且压得很暗：亮色下它就是「模糊红斑」，纯白底更干净。
    pane_filter = "blur(60px) brightness(.42)" if not bright else "blur(60px) brightness(.9)"
    brand_ink = "rgba(31,35,40,.45)" if bright else "rgba(255,255,255,.55)"
    rows = _details_html(details)
    marker_svg = _callout_svg(callout_parts, size=size, accent=accent, accent_light=accent_light)
    pane_bg_html = (
        ""
        if bright
        else f'<img class="bg" src="{_data_url(hero)}" alt="">'
    )
    headline = _escape(getattr(copy, "headline", ""))
    subline = _escape(getattr(copy, "subline", ""))
    head_html = (
        f'<div class="dtitle">PRODUCT <span>DETAILS</span></div>'
        f'<p class="dsub" data-max-height="{size * 0.075:.0f}" '
        f'data-min-height="{size * 0.013:.0f}">{subline}</p>'
        if subline
        else '<div class="dtitle">PRODUCT <span>DETAILS</span></div>'
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{size}px;height:{size}px;overflow:hidden;background:{page}}}
.stage{{position:relative;width:{size}px;height:{size}px;overflow:hidden;
  font-family:{head_font};color:{ink};--acc:{accent};--acc2:{accent_dark};--acc-light:{accent_light}}}
.grain{{position:absolute;inset:0;pointer-events:none;z-index:2;background:
  repeating-linear-gradient(115deg,{grain} 0 2px,transparent 2px 32px)}}
.left{{position:absolute;left:4%;top:5%;width:34%}}
.dtitle{{font-size:{size * 0.045}px;line-height:.94;font-style:italic;text-transform:uppercase;
  letter-spacing:-1px}}
.dtitle span{{color:var(--acc)}}
.dsub{{margin-top:{size * 0.012}px;font-family:{body_font};font-size:{size * 0.0155}px;
  line-height:1.4;color:{sub_ink}}}
.dlist{{margin-top:{size * 0.026}px;display:flex;flex-direction:column;gap:{size * 0.016}px}}
.drow{{position:relative;display:flex;align-items:center;gap:{size * 0.012}px;
  padding:{size * 0.012}px {size * 0.012}px;border-radius:{size * 0.008}px;
  background:{row_bg};border:1px solid {row_border}}}
.dthumb{{width:{size * 0.108}px;height:{size * 0.108}px;flex:0 0 {size * 0.108}px;
  border-radius:{size * 0.007}px;overflow:hidden;border:1px solid var(--acc)}}
.dthumb img{{width:100%;height:100%;object-fit:cover;display:block}}
.dcopy b{{font-size:{size * 0.0155}px;line-height:1.12;text-transform:uppercase;
  letter-spacing:.2px}}
.dnum{{position:absolute;right:{size * 0.009}px;bottom:{size * 0.007}px;
  width:{size * 0.026}px;height:{size * 0.026}px;border-radius:50%;background:var(--acc);
  display:flex;align-items:center;justify-content:center;font-size:{size * 0.016}px;
  font-family:{body_font};font-weight:800;color:#fff}}
.pane{{position:absolute;left:41.25%;top:13%;width:55%;height:74%;border-radius:{size * 0.014}px;
  overflow:hidden;border:2px solid var(--acc);background:{pane_bg};
  box-shadow:{pane_shadow}}}
.pane .bg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  filter:{pane_filter};transform:scale(1.08)}}
.pane .hero{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}}
.brand{{position:absolute;left:4%;bottom:3%;font-family:{body_font};font-size:{size * 0.014}px;
  letter-spacing:3px;color:{brand_ink};text-transform:uppercase}}
</style></head><body><div class="stage">
<div class="grain"></div>
<div class="left">
  {head_html}
  <div class="dlist">{rows}</div>
</div>
<div class="pane">
  {pane_bg_html}
  <img class="hero" src="{_data_url(hero)}" alt="">
</div>
{marker_svg}
<div class="brand">{headline}</div>
</div></body></html>"""


def build_infographic_html(
    kind: str,
    *,
    hero: bytes,
    copy: Any,
    details: Sequence[tuple[bytes, str]] = (),
    callout_parts: Sequence[dict] = (),
    category_id: str = "",
    size: int = 1600,
    style_template: str = "auto",
) -> str:
    """拼出信息图 HTML（纯字符串，不启动浏览器，便于单测）。"""

    key = str(kind or "").strip()
    if key == "infographic_hero":
        return _hero_document(
            hero=hero,
            copy=copy,
            details=details,
            category_id=category_id,
            size=int(size),
            style_template=style_template,
        )
    if key == "infographic_details":
        return _details_document(
            hero=hero,
            copy=copy,
            details=details,
            callout_parts=callout_parts,
            category_id=category_id,
            size=int(size),
            style_template=style_template,
        )
    raise ValueError(f"未知信息图版式: {kind}")


def render_infographic(
    kind: str,
    *,
    hero: bytes,
    copy: Any,
    details: Sequence[tuple[bytes, str]] = (),
    callout_parts: Sequence[dict] = (),
    category_id: str = "",
    size: int = 1600,
    scale: float = 2.0,
    style_template: str = "auto",
) -> bytes:
    """渲染信息图；浏览器不可用时抛异常，由调用方降级/跳过。"""

    from .html_composer import (  # noqa: PLC0415
        _downscale_jpeg,
        html_layout_enabled,
        render_html,
    )

    if not html_layout_enabled():
        raise RuntimeError("html layout disabled")
    if not hero:
        raise ValueError("infographic needs a hero image")
    factor = max(1, int(round(float(scale or 1.0))))
    document = build_infographic_html(
        kind,
        hero=hero,
        copy=copy,
        details=details,
        callout_parts=callout_parts,
        category_id=category_id,
        size=int(size),
        style_template=style_template,
    )
    content = render_html(document, width=int(size), height=int(size), scale=factor)
    if factor > 1:
        content = _downscale_jpeg(content, int(size))
    return content
