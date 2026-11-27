"""营销图生成 v2（clean-room 重写）。

旧方案的问题不是代码写得丑，是「一次生成就定稿」——模型方差极大，
一张图的成败靠运气。v2 只做三件事：

1. **短提示词**：只写场景/光/构图，产品交给参考图（长合同反而分散模型注意力）。
2. **多候选**：每个块一次生成 3 张候选。
3. **视觉择优**：豆包逐个打分（产品一致性 / 构图干净 / 无文字杂物），取最高分；
   打分失败就取第一张（不阻断）。

排版层（文字/标签/圆圈/卡片）保持确定性绘制——那部分从来不是短板。
"""

from __future__ import annotations

import base64
from typing import Any, Callable

CANDIDATE_COUNT = 3

# 短提示词：不同版式只差构图与留白位置。
# 全屏叠字的版式（headline_hero / action_proof）要求商品压在下 60%、上方留空；
# 分区版式（icon_grid / material_strip / use_cases / side_rail / feature_cards /
# feature_strip 的 hero）只要求商品居中偏小，背景一律交给品类场景片段决定。
# 商品必须小、背景必须多：商品只占画面中央约一半，四周全是环境——背景少了显得
# 局促，这是用户反复反馈的重点。
_SMALL_PRODUCT = (
    "the product sits centred and small, occupying only the middle 50% of the frame "
    "height, wide empty background space above, below and on both sides"
)

# 商品展示图里商品必须是「空的」：参考图里装在商品内部的东西（衣服、道具）是拍摄
# 时的陈列物，不是商品的一部分，不能跟着一起画出来。使用场景图除外——那正是展示
# 「装东西」的用途，保留内容物才对。
_SCENE_TEMPLATES = {"use_cases", "action_proof"}
_EMPTY_PRODUCT = (
    "The product is empty: anything stored inside it in the reference photos is a prop, "
    "not part of the product, leave the inside empty."
)

_COMPOSITIONS: dict[str, str] = {
    "headline_hero": (
        "Square product photo of the exact product in the reference images, the product "
        "sits small in the lower middle of the frame, the entire top 40% and both sides "
        "are empty seamless backdrop with nothing in them. Bright soft studio lighting, "
        "natural contact shadow, realistic materials."
    ),
    "action_proof": (
        "Square macro photo of the exact product in the reference images, working end in sharp "
        "focus in the lower right 55% of the frame, the upper-left 45% is empty seamless "
        "backdrop. Bright soft studio lighting."
    ),
    "icon_grid": (
        "Square product photo of the exact product in the reference images, "
        f"{_SMALL_PRODUCT}. Bright soft even lighting, natural contact shadow."
    ),
    "material_strip": (
        "Square product photo of the exact product in the reference images, "
        f"{_SMALL_PRODUCT}. Bright soft key light, natural contact shadow."
    ),
    "use_cases": (
        "Square product photo of the exact product in the reference images, "
        f"{_SMALL_PRODUCT}. Bright soft even lighting, natural contact shadow."
    ),
    "side_rail": (
        "Square product photo of the exact product in the reference images, "
        f"{_SMALL_PRODUCT}. Bright soft even lighting, natural contact shadow."
    ),
    "callout_ring": (
        "Square hero photo of the exact product in the reference images, "
        f"{_SMALL_PRODUCT}, bright even studio lighting."
    ),
    "feature_strip": (
        "Square hero photo of the exact product in the reference images, "
        f"{_SMALL_PRODUCT}. Bright soft key light, natural contact shadow."
    ),
    "feature_cards": (
        "Square hero photo of the exact product in the reference images, "
        f"{_SMALL_PRODUCT}. Bright soft studio lighting."
    ),
    # 信息图 A：商品在右半幅，左半幅留干净背景给文案与左下角小卡。
    "infographic_hero": (
        "Square product photo of the exact product in the reference images, the product is "
        "centred in the right half of the frame and fully visible, the left half is clean "
        "dark uncluttered background with nothing in it, bright rim light, realistic materials."
    ),
    # 信息图 B：整机居中、四周留白，编号引线需要完整商品与可绘制空间。
    "infographic_details": (
        "Square product photo of the exact product in the reference images, the whole product "
        "centred with generous even empty margins on every side, dark uncluttered studio "
        "background, soft even lighting, no props."
    ),
    # 白底主图：角度/朝向按尺寸画布需要固定——正立、正面、全身入画、四周留白。
    "white_main": (
        "Square studio product photo of the exact product in the reference images, standing "
        "upright and centred, straight-on front view with no perspective tilt, the whole product "
        "visible with even margin on every side, seamless pure white background, soft shadowless "
        "even lighting, no props, no reflections."
    ),
}

# 明亮品类（包袋/美妆/家居等）的信息图构图：留白位置不变，背景换成亮调影棚，
# 不能再沿用五金工具的暗黑工业风。共通模板可按用户选择覆盖。
_INFOGRAPHIC_COMPOSITIONS_STYLES: dict[str, dict[str, str]] = {
    "bright": {
        "infographic_hero": (
            "Square product photo of the exact product in the reference images, the product "
            "fills the right 55% of the frame and is the clear focal point, the left 45% is "
            "clean bright uncluttered background with nothing in it; strong subject separation, "
            "soft contact shadow, rich texture and colour, never washed out."
        ),
        "infographic_details": (
            "Square product photo of the exact product in the reference images, the whole product "
            "centred with generous even empty margins on every side, bright uncluttered studio "
            "background, soft even lighting, strong subject separation, not washed out, no props."
        ),
    },
    "white": {
        "infographic_hero": (
            "Square product photo of the exact product in the reference images, the product "
            "fills the right 55% of the frame and is the clear focal point, the left 45% is "
            "clean pure white background with nothing in it, soft shadowless studio light, "
            "crisp edges and a soft contact shadow so the product separates from the white."
        ),
        "infographic_details": (
            "Square product photo of the exact product in the reference images, the whole product "
            "centred with generous even empty margins on every side, pure white seamless "
            "background, soft even lighting, crisp edges and a soft contact shadow, no props."
        ),
    },
    "lifestyle": {
        "infographic_hero": (
            "Square product photo of the exact product in the reference images, the product "
            "fills the right 55% of the frame and is the clear focal point, the left 45% is a "
            "clean softly blurred home background, natural daylight, strong subject separation."
        ),
        "infographic_details": (
            "Square product photo of the exact product in the reference images, the whole product "
            "centred with generous even empty margins on every side, a bright home environment "
            "background softly blurred, natural daylight, strong subject separation, no props."
        ),
    },
}

MEDIA_RULES = (
    "Reproduce the product exactly as in the reference images: same shape, parts, colours and "
    "markings. Never redesign or merge parts. No text, no logos, no hands, no extra objects. "
    "Photographic realism."
)

# 主图裁切是全链路的通病：模型倾向把长条工具画满画面，上下端被切掉后排版层
# 无论怎么裁都救不回来（白底主图明确写了「全身入画」就很少出问题）。
# 所有版式统一追加一条硬约束，要求整件商品完整入画并留边。
_FRAME_FIT = (
    "The entire product must be fully inside the frame with at least 8% empty background "
    "on every side; never crop it. Full-product photo, not a close-up."
)

# 有结构骨架时，构图完全由骨架决定，不再叠加模板自带的「居中 50%」等描述——
# 两段构图要求互相矛盾时，模型只会取折中，预留区就白留了。
_SKELETON_BASE = "Square product photo of the exact product in the reference images."

# 所有品类都用促销排版：暗色、低干扰背景，给大标题和彩色标签让位；轮廓光按品类变化，
# 不再固定「橙色轮廓光」。
_PROMO_RIM: dict[str, str] = {
    "hand_tools": "warm amber rim light",
    "auto_accessories": "warm amber rim light",
    "garden_tools": "soft natural daylight rim",
    "sports_outdoor": "cool blue rim light",
    "home_kitchen": "warm window-light rim",
    "apparel_accessories": "soft warm rim light",
    "beauty_tools": "soft rose rim light",
    "passive_tech": "cool cyan rim light",
    "office_stationery": "clean cool rim light",
    "pet_supplies": "soft neutral rim light",
    "adult_games": "low warm rim light",
}


def _promo_rim(category_id: str) -> str:
    return _PROMO_RIM.get(
        str(category_id or "").strip().lower(), "soft neutral rim light"
    )


# 明亮品类列表已并入默认策略：auto 一律明亮影棚（用户要求不出「黑黑的环境 +
# 暗产品」的图），暗色只在用户显式选「暗色质感」时使用。
STYLE_TEMPLATES: tuple[str, ...] = ("auto", "bright", "dark", "white", "lifestyle")
_BRIGHT_STYLES = {"bright", "white", "lifestyle"}


def resolve_style_template(value: str, category_id: str) -> str:
    style = str(value or "auto").strip().lower()
    if style in {"bright", "dark", "white", "lifestyle"}:
        return style
    return "bright"


def style_is_bright(style: str) -> bool:
    return str(style or "").strip().lower() in _BRIGHT_STYLES


def _promo_lighting(category_id: str, style_template: str = "auto") -> str:
    style = resolve_style_template(style_template, category_id)
    if style == "white":
        return (
            "Studio lighting: seamless pure white background, soft shadowless even lighting, "
            "crisp product edges and a soft contact shadow so the product separates from the "
            "white background; bright airy high-key look, never washed out."
        )
    if style == "lifestyle":
        return (
            "Natural daylight in a real home or workspace setting, softly blurred lived-in "
            f"background, {_promo_rim(category_id)}, strong subject separation, product clearly "
            "visible and evenly exposed; warm, clean and realistic."
        )
    if style_is_bright(style):
        return (
            "Studio lighting: bright airy warm light-grey background (not pure white), soft "
            "diffused key light, open shadows, clear subject separation, soft contact shadow, "
            "rich texture and colour; product is the unmistakable focal point, never washed out."
        )
    return (
        "Studio lighting: dark low-key background, "
        f"{_promo_rim(category_id)}, bright soft key light keeps the product clearly "
        "visible, open shadows, product is the brightest object, the product itself is "
        "bright, well exposed and clearly readable, never muddy or underexposed."
    )


def _promo_fragment(category_id: str, style_template: str = "auto") -> str:
    rim = _promo_rim(category_id)
    category = str(category_id or "").strip().lower()
    style = resolve_style_template(style_template, category_id)
    if style == "white":
        return (
            "Pure white seamless studio: no props, no background objects, soft contact "
            "shadow, clean e-commerce listing look, product clearly lit."
        )
    if style == "lifestyle":
        return (
            "Real lifestyle scene: a tidy modern home or workspace surface with soft natural "
            f"window light and a gently blurred background, {rim}, product clearly lit and "
            "in focus. Avoid hands and clutter."
        )
    if style_is_bright(style):
        return (
            "Bright editorial studio: warm light-grey seamless surface with a softly "
            f"blurred bright background, {rim}, product clearly lit with open shadows "
            "and readable detail, tidy. Avoid hands and clutter."
        )
    if category in {"hand_tools", "auto_accessories"}:
        return (
            "Dark industrial studio: black steel surface and blurred tool cabinet behind, "
            f"{rim} raking across brushed metal, product brightly lit with open shadows, "
            "vivid colour and readable detail, the product is clearly the brightest "
            "object and never underexposed. Avoid hands and other tools."
        )
    return (
        "Dark studio: deep charcoal surface with a softly blurred background, "
        f"{rim}, product brightly lit with open shadows, vivid colour and readable "
        "detail, the product is clearly the brightest object and never underexposed. "
        "Avoid hands and clutter."
    )
_PROMO_SPACE = {
    "S1": (
        "the product occupies the right 62% of the upper 64% of the frame, the top-left "
        "area and the bottom 36% are clean uncluttered background with nothing in them"
    ),
    "S2": (
        "the product is centred inside the left 58% of the frame, the right 42% is clean "
        "seamless background with nothing in it"
    ),
    "S3": (
        "the product sits in the middle of the frame, the top 30% and bottom 12% are clean "
        "background, the left and right edges stay uncluttered"
    ),
    "S4": (
        "the product is centred with generous even margins, the whole product fully visible "
        "on a clean seamless background with nothing else in the frame"
    ),
}

_SELECT_SYSTEM = (
    "You are an e-commerce photo editor choosing the best frame from several AI attempts."
)

_SELECT_USER = """The image(s) before the candidates are the REAL product reference photos.
Compare every candidate with them: shape, parts, colours and proportions must match.
Then pick the single best candidate. Judge by:
1. product fidelity — same product as the reference, no deformed or missing parts
2. cleanliness — no extra objects, no text, no weird artifacts
3. composition — product complete in frame with margin, good light
4. layout fit — @@HINT@@

Return ONLY JSON: {"best": <0-based index>, "scores": [<score 0-10 per image in order>]}"""

_DEFAULT_JUDGE_HINT = "the product is complete with usable empty space for the copy"

# 成图/候选终检：严格按参考图核对商品，再检查排版。
_QC_SYSTEM = (
    "You are a strict e-commerce listing image QA reviewer. "
    "You compare the generated image with the real product reference and reject "
    "anything that changed the product or broke the layout."
)
_QC_USER = """Image 1 is the REAL product reference. The last image is the image under review.
Check all of them:
1. product fidelity — same shape, parts, colours and proportions; no deformation, no missing or extra parts.
   Ignore brand text, logos, engraved model numbers and printed markings: AI re-shooting is NOT expected
   to reproduce them, and their absence, blur or altered spelling is NOT a failure.
2. text safety — no text or graphic covers the product; labels do not overflow the canvas
3. legibility — headline and labels are readable at listing size
4. layout — the image matches the @@SKELETON@@ layout and keeps the product complete.
   A different camera angle, arrangement or background versus the reference is allowed; do not fail for that.

Return ONLY JSON: {"pass": true or false, "issues": ["short issue", ...]}"""


# QC 提示词里的布局描述：给代号（S1）模型看不懂，等于没做布局检查。
_QC_SKELETON_HINTS = {
    "S1": "the photo occupies the top area with a clean text band across the bottom",
    "S2": "the photo card sits on the left with a text column on the right",
    "S3": "full-bleed photo with the headline overlaid near the top",
    "S4": "a large photo card centred with generous margins, text above and below",
}


def _qc_messages(
    reference_urls: list[str], reviewed_url: str, skeleton: str
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for url in list(reference_urls or [])[:1]:
        content.append({"type": "image_url", "image_url": {"url": url}})
    content.append({"type": "image_url", "image_url": {"url": reviewed_url}})
    sk = str(skeleton or "S1").strip().upper()
    content.append(
        {
            "type": "text",
            "text": _QC_USER.replace(
                "@@SKELETON@@", _QC_SKELETON_HINTS.get(sk, "the chosen layout")
            ),
        }
    )
    return [
        {"role": "system", "content": _QC_SYSTEM},
        {"role": "user", "content": content},
    ]


def verify_candidate_messages(
    reference_urls: list[str], candidate_url: str, skeleton: str
) -> list[dict[str, Any]]:
    """候选 hero 的保真/构图终检；通过返回 (True, [])。"""

    return _qc_messages(reference_urls, candidate_url, skeleton)


def verify_composed_messages(
    reference_urls: list[str], composed_url: str, skeleton: str
) -> list[dict[str, Any]]:
    """成图终检：商品保真 + 文字安全 + 可读性。"""

    return _qc_messages(reference_urls, composed_url, skeleton)


def parse_qc_response(text: str) -> tuple[bool, list[str]]:
    """解析终检 JSON；模型没按格式返回时按通过处理（不因质检故障丢图）。"""

    import json
    import re

    match = re.search(r"\{.*\}", str(text or ""), re.DOTALL)
    if not match:
        return True, []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return True, []
    if not isinstance(data, dict):
        return True, []
    passed = bool(data.get("pass", True))
    issues = [
        str(item)[:160]
        for item in (data.get("issues") or [])
        if str(item or "").strip()
    ][:4]
    return passed, issues


def build_prompt(
    template: str,
    category_id: str = "",
    seed: int = 0,
    *,
    subject: str = "",
    instructions: str = "",
    skeleton: str = "",
    set_count: int = 1,
    pose: str = "",
    style_template: str = "auto",
) -> str:
    """版式构图 + 结构预留区 + 品类场景 + 保真规则。

    ``category_id`` 为空或 general 时只拼版式；命中品类时追加该大类的
    「表面/光线/氛围」描述（同品类多变体，按 ``seed`` 轮换），不改版式的留白与构图。

    ``subject``：视觉识别确认的可售主体；写明后模型不会把旁边的道具当商品。
    ``instructions``：操作者注意点（已译成英文）；作为硬约束追加在最后。
    ``skeleton``：版式结构（S1 上下分栏 / S2 左右分栏 / S3 满版叠加 / S4 贴边大卡）。
    不同结构需要的文字区位置不同，这里把对应的「商品位置 + 空区」写进提示词，
    让生图阶段就把排版空间预留出来——否则排版层只能把字缩小。

    例外：``white_main`` 不拼品类片段与结构预留——它只要求纯白背景、完整入画。

    除使用场景版式（``use_cases`` / ``action_proof``）外，都追加「商品是空的」
    规则——参考图里的内容物（如收纳筐里的衣服）是陈列道具，不是商品。
    """
    from .layout_variants import skeleton_space  # noqa: PLC0415

    key = str(template or "")
    style = resolve_style_template(style_template, category_id)
    space_rule = "" if key == "white_main" else skeleton_space(skeleton)
    if space_rule:
        space_rule = _PROMO_SPACE.get(
            str(skeleton or "").strip().upper(), space_rule
        )
    if space_rule:
        composition = (
            f"{_SKELETON_BASE} {space_rule}. "
            f"{_promo_lighting(category_id, style_template=style)}"
        )
    else:
        composition = _COMPOSITIONS.get(key, _COMPOSITIONS["headline_hero"])
        variant = _INFOGRAPHIC_COMPOSITIONS_STYLES.get(style, {}).get(key)
        if variant:
            # 用户选的共通模板优先：包袋选暗色、工具选亮色都按用户选择来。
            composition = variant
    emptiness = "" if key in _SCENE_TEMPLATES else _EMPTY_PRODUCT
    fragment = "" if key == "white_main" else _promo_fragment(category_id, style_template=style)
    frame_fit = "" if key == "white_main" else _FRAME_FIT
    subject_rule = (
        f"The product is exactly: {str(subject).strip()[:200]}."
        if str(subject or "").strip()
        else ""
    )
    instruction_text = str(instructions or "").strip()
    instruction_rule = (
        # 截断窗口要容得下「场景句 + 不画字/不改设计硬约束」——截太短会把
        # 末尾的硬约束静默切掉（「never render any text」进不了提示词）。
        f"Follow strictly: {instruction_text[:460].rstrip('.')}."
        if instruction_text
        else ""
    )
    set_rule = ""
    count = max(1, int(set_count or 1))
    if count > 1:
        set_rule = (
            f"This is a set of {count} separate pieces. Show all {count} pieces together "
            "exactly as arranged in the reference images; every piece fully visible, "
            "do not merge pieces, do not omit any."
        )
    pose_text = ""
    if key != "white_main" and str(pose or "").strip():
        from .marketing_poses import pose_rule  # noqa: PLC0415

        pose_text = pose_rule(str(pose), set_count=count)
    return " ".join(
        part
        for part in (
            composition,
            subject_rule,
            set_rule,
            pose_text,
            emptiness,
            frame_fit,
            fragment,
            instruction_rule,
            MEDIA_RULES,
        )
        if part
    )


def _data_url(content: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(content).decode()


def _judge_data_url(content: bytes, max_edge: int = 768) -> str:
    """打分用的缩略图：长边压到 768 再编码。

    打分只看构图/畸形/杂物，不需要 2048 原图的细节；原图 base64 会让请求体
    大好几倍，拖慢择优并挤占视觉模型的上下文。
    """
    try:
        from io import BytesIO

        from PIL import Image  # noqa: PLC0415

        with Image.open(BytesIO(content)) as opened:
            frame = opened.convert("RGB")
            longest = max(frame.size)
            if longest > max_edge:
                scale = max_edge / float(longest)
                frame = frame.resize(
                    (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
                    Image.LANCZOS,
                )
            buffer = BytesIO()
            frame.save(buffer, format="JPEG", quality=82)
            content = buffer.getvalue()
    except Exception:  # noqa: BLE001 - 缩略图失败就退回原图
        pass
    return _data_url(content)


def judge_order(
    candidates: list[bytes],
    *,
    complete: Callable[[list[dict[str, Any]]], str],
    structure_hint: str = "",
    reference_data_urls: list[str] | None = None,
) -> list[int]:
    """按视觉打分返回候选顺序（最佳在前）。

    ``reference_data_urls``：真实商品参考图（已压到视觉尺寸）。打分时同时看
    参考图和候选，商品变形/缺件的候选会被压分——这是拦住「好看但不是这个
    商品」的关键。任何异常都按原顺序返回，不阻断出图。
    """
    fallback = list(range(len(candidates)))
    if len(candidates) <= 1:
        return fallback
    try:
        hint = str(structure_hint or "").strip() or _DEFAULT_JUDGE_HINT
        content: list[dict[str, Any]] = []
        for url in list(reference_data_urls or [])[:1]:
            content.append({"type": "image_url", "image_url": {"url": url}})
        for candidate in candidates[:4]:
            content.append(
                {"type": "image_url", "image_url": {"url": _judge_data_url(candidate)}}
            )
        content.append({"type": "text", "text": _SELECT_USER.replace("@@HINT@@", hint)})
        messages = [
            {"role": "system", "content": _SELECT_SYSTEM},
            {"role": "user", "content": content},
        ]
        raw = str(complete(messages) or "")
        import json
        import re

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return fallback
        data = json.loads(match.group(0))
        scores = data.get("scores")
        if not isinstance(scores, list) or len(scores) < len(candidates):
            best = int(data.get("best", 0))
            if 0 <= best < len(candidates):
                return [best] + [i for i in fallback if i != best]
            return fallback
        def _score(index: int) -> float:
            try:
                return float(scores[index] or 0)
            except (TypeError, ValueError):
                return 0.0

        order = sorted(fallback, key=_score, reverse=True)
        return order
    except Exception:  # noqa: BLE001
        return fallback


def pick_best(
    candidates: list[bytes],
    *,
    complete: Callable[[list[dict[str, Any]]], str],
    structure_hint: str = "",
    reference_data_urls: list[str] | None = None,
) -> int:
    """视觉打分选最佳；任何异常返回 0（第一张）。"""

    order = judge_order(
        candidates,
        complete=complete,
        structure_hint=structure_hint,
        reference_data_urls=reference_data_urls,
    )
    return order[0] if order else 0


def generate_best(
    *,
    prompt: str,
    refs: list[str],
    render: Callable[[str, list[str]], bytes],
    complete: Callable[[list[dict[str, Any]]], str],
    count: int = CANDIDATE_COUNT,
    structure_hint: str = "",
    reference_data_urls: list[str] | None = None,
    verify: Callable[[bytes], bool] | None = None,
    gate: Callable[[bytes], bool] | None = None,
) -> bytes:
    """生成 count 张候选 → 本地构图门 → 参考图打排序 → 逐个终检。

    ``gate`` 是本地硬门槛（如商品贴边/占满画面）：先淘汰不合格候选；
    全部不合格时保留原候选，避免因为门槛过严反而丢图。
    """
    candidates: list[bytes] = []
    for _ in range(max(1, count)):
        try:
            content = render(prompt, refs)
        except Exception:  # noqa: BLE001
            continue
        if content:
            candidates.append(content)
    if not candidates:
        return b""
    if callable(gate):
        passing = [content for content in candidates if gate(content)]
        if passing:
            candidates = passing
    order = judge_order(
        candidates,
        complete=complete,
        structure_hint=structure_hint,
        reference_data_urls=reference_data_urls,
    )
    if callable(verify):
        for index in order:
            try:
                if verify(candidates[index]):
                    return candidates[index]
            except Exception:  # noqa: BLE001 - 终检故障不能阻断出图
                continue
    return candidates[order[0]] if order else candidates[0]


# ── 编号标注：视觉标坐标（0-1000 归一化）─────────────────────────
_CALLOUT_SYSTEM = (
    "You locate key parts on a product photo for callout annotations. "
    "Coordinates are normalized 0-1000 with origin at the top-left of the image."
)

_CALLOUT_USER = """Look at this product image and locate 3 to 5 key parts worth annotating
(jaws, teeth, ratchet, pivot, grip, lock lever).

Use ONLY these 9 grid anchors and pick the anchor closest to each part's centre:
(167,167) top-left, (500,167) top-centre, (833,167) top-right,
(167,500) mid-left, (500,500) centre, (833,500) mid-right,
(167,833) bottom-left, (500,833) bottom-centre, (833,833) bottom-right.

Return ONLY JSON: {"parts": [{"label": "<2-3 word English label>", "x": <0-1000>, "y": <0-1000>}]}

Rules: x and y MUST each be one of 167, 500, 833; never repeat the same anchor;
spread the points across different cells; never invent parts."""


def build_callout_messages(hero_data_url: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _CALLOUT_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": hero_data_url}},
                {"type": "text", "text": _CALLOUT_USER},
            ],
        },
    ]


def parse_callout_response(text: str) -> list[dict]:
    import json
    import re

    match = re.search(r"\{.*\}", str(text or "").strip(), re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    parts_raw = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(parts_raw, list):
        return []
    parts: list[dict] = []
    for item in parts_raw[:5]:
        if not isinstance(item, dict):
            continue
        try:
            x = float(item.get("x"))
            y = float(item.get("y"))
        except (TypeError, ValueError):
            continue
        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            continue
        label = str(item.get("label") or "").strip()[:28] or "Detail"
        parts.append({"label": label, "x": x, "y": y})
    return parts


# ── 第二段定位：在局部放大图里精确化第一段的网格锚点 ─────────────────────
_CALLOUT_REFINE_SYSTEM = (
    "You refine the location of one product part inside a close-up crop."
)

_CALLOUT_REFINE_USER = """This is a close-up crop of a product. Locate the part described as "{label}".
Return ONLY JSON: {{"x": <0-1000>, "y": <0-1000>}} pointing at its centre in THIS crop.
If the part is not visible in this crop, return {{"x": -1, "y": -1}}."""


def build_callout_refine_messages(crop_data_url: str, label: str) -> list[dict[str, Any]]:
    """局部精修请求：只问一个部件，在裁剪图里给 0-1000 坐标。"""
    return [
        {"role": "system", "content": _CALLOUT_REFINE_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": crop_data_url}},
                {
                    "type": "text",
                    "text": _CALLOUT_REFINE_USER.format(
                        label=str(label or "the part")[:60]
                    ),
                },
            ],
        },
    ]


def parse_callout_refine_response(text: str) -> tuple[float, float] | None:
    """解析局部精修坐标；模型说看不见（-1）或返回非法值时返回 None。"""
    import json
    import re

    match = re.search(r"\{.*\}", str(text or "").strip(), re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        x = float(data.get("x"))
        y = float(data.get("y"))
    except (TypeError, ValueError):
        return None
    if not (0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0):
        return None
    return x, y


# ── 参考图选择：从原始轮播里挑最干净的 1–2 张给生图模型 ─────────────────
#
# 1688 轮播里大量是带中文大字/拼贴/参数表的营销图；直接拿前两张当参考，
# 模型会把文字、箭头、拼贴一起学进照片层，导致商品变形。这里先让视觉模型
# 挑出「商品完整、无文字水印、无拼贴」的图，再交给 Seedream。
_REFERENCE_SELECT_SYSTEM = (
    "You choose the cleanest product reference photos for AI product re-shooting."
)

_REFERENCE_SELECT_USER = """These are candidate photos of the same product, in the given order.
Pick the 1-4 photos that are BEST as product references for AI re-shooting.
Requirements:
- the product is fully visible and unobstructed
- no text, no watermark, no logo overlay, no collage, no hands
- plain or clean background preferred
- prefer different camera angles/orientations across the selected photos, and include
  at least one clean upright full-product shot
Return ONLY JSON: {"best": [<0-based indices, best first>]}"""


def build_reference_select_messages(data_urls: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": url}}
        for url in list(data_urls or [])[:6]
        if str(url or "").startswith("data:image/")
    ]
    content.append({"type": "text", "text": _REFERENCE_SELECT_USER})
    return [
        {"role": "system", "content": _REFERENCE_SELECT_SYSTEM},
        {"role": "user", "content": content},
    ]


def parse_reference_select_response(text: str, count: int) -> list[int]:
    """解析参考图选择结果；非法项丢弃，最多返回 2 个。"""
    import json
    import re

    match = re.search(r"\{.*\}", str(text or "").strip(), re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    best = data.get("best") if isinstance(data, dict) else None
    if not isinstance(best, list):
        return []
    picked: list[int] = []
    for item in best:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < int(count) and index not in picked:
            picked.append(index)
        if len(picked) >= 4:
            break
    return picked


# ── 细节近景选择：细节卡从原始商品图挑真实局部，不再从 AI 主图裁 ──────────────
_DETAIL_PICK_SYSTEM = "You pick clean close-up product photos and name the visible part."

_DETAIL_PICK_USER = """These images are from the original listing.
Pick up to 4 CANDIDATE images that are CLEAN CLOSE-UPS of one product part
(jaws, teeth, blade, ratchet, pivot, grip, lever, cable, handle, strap, zipper,
pocket, stitching, clasp, base).

Requirements:
- the part is clearly visible and fills most of the frame
- each picked candidate must show a DIFFERENT product part; never pick two shots of the same part
- every label must be UNIQUE across picks (no repeated labels)
- prefer variety across parts (e.g. head, joint, grip, base) over several shots of one area
- no text, no watermark, no logo overlay, no collage, no hands or people, no packaging
- skip posters, spec tables, plain full-product shots and near-duplicates
- for each picked candidate give a 2-3 word English label naming a physical part
  that is ACTUALLY visible; never invent a part, never use a slogan
- if the first image is marked REFERENCE PRODUCT, every picked candidate must show
  that SAME product, colour and material; skip other colourways and variants

Return ONLY JSON: {"items": [{"index": <candidate number>, "label": "<2-3 words>"}]}"""


def build_detail_pick_messages(
    data_urls: list[str], *, reference_data_url: str = ""
) -> list[dict[str, Any]]:
    """细节选图消息：可选的参考图固定放第一张，候选图前标注 CANDIDATE 编号。

    给每个候选图单独加一行编号文本，比让模型自己数图片位置可靠；编号从 0 开始，
    与 ``parse_detail_pick_response`` 的返回值一一对应。
    """
    candidates = [
        str(url or "")
        for url in list(data_urls or [])[:12]
        if str(url or "").startswith("data:image/")
    ]
    content: list[dict[str, Any]] = []
    if str(reference_data_url or "").startswith("data:image/"):
        content.append({"type": "image_url", "image_url": {"url": reference_data_url}})
        content.append({"type": "text", "text": "REFERENCE PRODUCT (exact colour and material)"})
    for index, url in enumerate(candidates):
        content.append({"type": "image_url", "image_url": {"url": url}})
        content.append({"type": "text", "text": f"CANDIDATE {index}"})
    content.append({"type": "text", "text": _DETAIL_PICK_USER})
    return [
        {"role": "system", "content": _DETAIL_PICK_SYSTEM},
        {"role": "user", "content": content},
    ]


def parse_detail_pick_response(
    text: str, count: int, *, limit: int = 4
) -> list[tuple[int, str]]:
    """解析细节近景选择：返回 [(图片序号, 部位标签)]，非法项丢弃。"""
    import json
    import re

    match = re.search(r"\{.*\}", str(text or "").strip(), re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    picked: list[tuple[int, str]] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        label = str(item.get("label") or "").strip()
        if not (0 <= index < int(count)) or index in seen or not label:
            continue
        # 中文防护：标签会画进细节卡，非 ASCII 直接丢弃该候选。
        if not label.isascii():
            continue
        seen.add(index)
        picked.append((index, label[:40]))
        if len(picked) >= max(1, int(limit)):
            break
    return picked


# ── 套件识别：多件套（如 3 把钳子一套）必须按件数生成 ───────────────────
_SET_COUNT_SYSTEM = "You count the separate tools in a product listing image."

_SET_COUNT_USER = """Count the separate tools/pieces shown in this product image.
Ignore text, hands and props. A single product = 1. A set of three pliers = 3.
Return ONLY JSON: {"count": <integer 1-8>}"""


def build_set_count_messages(image_data_url: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _SET_COUNT_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_url}},
                {"type": "text", "text": _SET_COUNT_USER},
            ],
        },
    ]


def parse_set_count_response(text: str) -> int:
    """解析套件件数；非法返回 1（按单件处理）。"""
    import json
    import re

    match = re.search(r"\{.*\}", str(text or "").strip(), re.DOTALL)
    if not match:
        return 1
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return 1
    if not isinstance(data, dict):
        return 1
    try:
        count = int(data.get("count"))
    except (TypeError, ValueError):
        return 1
    return max(1, min(8, count))
