"""产品处理视觉规划层（对齐原型 native_product_engine 的 _visual_prompt_plan 依赖链）。

原型通过“品类角色库 + 特制识别 + 证据拼接”动态组装生图提示词所需的 10 个视觉字段，
本文档完整移植该逻辑：
  listing_prompt_context() 等价于原版 _listing_prompt_context（category/value_evidence/
  verified_material_evidence/required_attributes + title/visual 计划）；
  visual_prompt_plan() 等价于原版 _visual_prompt_plan（product_visual_identity /
  visual_family / visual_style / lighting_plan / material_plan / background_plan /
  composition_plan / scene_plan / video_shot_plan / detail_plan）。

与原型差异（仅为适配本项目数据模型）：
- category_match 本项目无类目库，由 build_category_match() 用来源数据构造最小匹配对象；
- 原版源码中损坏的编码（mojibake）中文关键字在此统一为正确的 UTF-8 中文；
- 材质证据链（_source_attribute_pairs/_trusted_material_evidence）按本项目
  source_attributes/source_variant_records 结构做等价简化。
"""

from __future__ import annotations

import re
from typing import Any

_CAPTAURED_FIELD_PROMPT_DENYLIST: frozenset[str] = frozenset()
_VERIFIED_MATERIAL_TRANSLATIONS: dict[str, str] = {
    "涤纶": "Polyester",
    "聚酯": "Polyester",
    "聚酯纤维": "Polyester",
    "棉线": "Cotton Rope",
    "棉绳": "Cotton Rope",
    "棉": "Cotton",
    "帆布": "Canvas",
    "牛津布": "Oxford Cloth",
    "无纺布": "Non-Woven Fabric",
    "草编": "Straw Woven",
    "拉菲草": "Straw Woven",
    "海草": "Straw Woven",
    "纸绳": "Straw Woven",
    "藤编": "Rattan",
    "仿藤编": "Rattan",
    "塑料": "Plastic",
    "硅胶": "Silicone",
    "不锈钢": "Stainless Steel",
    "铁": "Iron",
    "木": "Wood",
    "竹": "Bamboo",
}

# 与原型 _visual_category_family 一致：优先级高的 family 在同分时胜出
VISUAL_CATEGORY_ROLE_PRIORITY: tuple[str, ...] = (
    "packaging_bags",
    "musical_tools_accessories",
    "arts_crafts_stationery",
    "table_linen",
    "soft_home_textile",
    "home_storage_organization",
    "office_school_supplies",
    "tools_hardware",
    "automotive_accessories",
    "pet_supplies",
    "kitchen_dining",
    "garden_outdoor",
    "lighting_electrical_allowed",
    "party_festival",
    "beauty_personal_accessory",
    "apparel_accessories",
    "bags_cases",
    "jewelry_small_accessory",
    "toys_games",
    "baby_kids_safe_goods",
    "home_decor_wall",
)

VISUAL_CATEGORY_ROLE_LIBRARY: dict[str, dict[str, Any]] = {
    "musical_tools_accessories": {
        "keywords": ("guitar", "pick", "plectrum", "instrument accessory", "music accessory", "吉他", "拨片", "乐器"),
        "style": "clean musician-workbench accessory style, precise and practical",
        "lighting": "bright neutral commercial lighting that shows small structure clearly",
        "material": "contact points, punched area, clamp, hole, edge, and accessory details visible from the source",
        "background": "clean music desk, craft table, or neutral studio backdrop",
        "composition": "full product view, contact-point detail close-up, musician or desk-making scene, accessories or size layout",
        "roles": ("full product hero with the instrument accessory complete and unobstructed", "close-up of contact point, hole, clamp, pick edge, or other real functional detail", "realistic musician desk or DIY making scene without changing the product", "accessory, size, set, or operation layout when supported by source evidence"),
        "detail": "one large music-accessory hero poster + one circular magnifier inset for contact or hole detail + exactly 3 light labels: DIY Use, Detail View, Compact Tool.",
    },
    "arts_crafts_stationery": {
        "keywords": ("art", "craft", "stationery", "crayon", "marker", "pencil", "paint", "brush", "drawing", "school supplies", "文具", "蜡笔", "油画棒", "彩笔", "画笔"),
        "style": "bright craft-table marketplace style with clean color presentation",
        "lighting": "soft bright lighting that keeps colors clear and realistic",
        "material": "tips, caps, color marks, case, texture, and tool structure visible from the source",
        "background": "clean art desk, craft table, school desk, or neutral studio surface",
        "composition": "full product view, tip/color/detail close-up, creative use scene, color-count or set layout",
        "roles": ("full product or set hero with all source-supported pieces represented", "close-up of tip, color, texture, cap, case, or tool detail", "drawing, craft, desk, or school-use scene that keeps the same product identity", "color count, set contents, storage, or arrangement layout when supported"),
        "detail": "one large craft-table hero poster + one circular magnifier inset for tip or color detail + exactly 3 light labels: Creative Use, Color Set, Detail View.",
    },
    "tools_hardware": {
        "keywords": ("tool", "hardware", "repair", "installation", "punch", "cutter", "pliers", "clamp", "drill", "wrench", "工具", "五金", "打孔", "冲孔", "钳"),
        "style": "practical home improvement style, clear and useful",
        "lighting": "bright neutral commercial lighting that shows structure clearly",
        "material": "visible structure, handle, edge, joint, clamp, blade, hole, or functional part",
        "background": "clean workbench, garage, garden, or home repair setting",
        "composition": "full product view, functional detail close-up, realistic use scene, parts or scale layout",
        "roles": ("clear full product hero with tool body complete and unobstructed", "close-up of handle, blade, clamp, hole, edge, joint, or functional structure", "realistic workbench, repair, home, or garden-use scene", "parts, accessory, size, operation, or installation relationship if supported"),
        "detail": "one large practical-use hero poster + one circular magnifier inset for functional detail + exactly 3 light labels: Practical Design, Easy Handling, Home Use.",
    },
    "toys_games": {
        "keywords": ("toy", "game", "puzzle", "play", "building block", "doll", "model kit", "玩具", "游戏", "积木", "益智"),
        "style": "bright playful but marketplace-clean product style",
        "lighting": "clean bright lighting with realistic shadows and clear small parts",
        "material": "play structure, pieces, printed pattern, storage, and visible details from the source",
        "background": "clean tabletop, playroom shelf, or family activity surface",
        "composition": "full toy view, part/detail close-up, tabletop play scene, set or storage layout",
        "roles": ("full toy or game hero showing the sellable product clearly", "close-up of playable structure, pieces, texture, or printed detail", "tabletop or family entertainment scene without age or safety claims", "set quantity, included parts, storage, or layout relationship"),
        "detail": "one large toy/game hero poster + one circular magnifier inset for part detail + exactly 3 light labels: Tabletop Play, Detail View, Set Layout.",
    },
    "home_storage_organization": {
        "keywords": ("storage", "organizer", "holder", "rack", "hook", "hanger", "shelf", "basket", "divider", "收纳", "挂钩", "置物", "架", "篮"),
        "style": "organized practical home style, tidy and space-saving",
        "lighting": "clean bright marketplace lighting with realistic shadows",
        "material": "dividers, hook, opening, mounting area, handle, structure, and capacity relationship",
        "background": "tidy entryway, bathroom, kitchen, closet, laundry, or utility wall",
        "composition": "front product view, structure close-up, organized use scene, capacity or before-use relationship",
        "roles": ("clear product hero showing the organizer or storage item complete", "close-up of divider, hook, opening, handle, seam, or mounting structure", "organized kitchen, bathroom, closet, entryway, or utility-use scene", "capacity, set quantity, wall placement, or storage relationship"),
        "detail": "one large organized-home hero poster + one circular magnifier inset for structure detail + exactly 3 light labels: Organized Home, Space Saving, Detail View.",
    },
    "kitchen_dining": {
        "keywords": ("kitchen", "dining", "cookware", "utensil", "tray", "cup", "bowl", "plate", "server", "厨房", "餐具", "碗", "杯", "托盘"),
        "style": "bright clean kitchen and dining style",
        "lighting": "soft natural daylight or clean kitchen lighting",
        "material": "visible surface, rim, edge, grip, contact area, or dining-use detail without material claims",
        "background": "clean kitchen counter or dining table, minimal and uncluttered",
        "composition": "full product view, surface/detail close-up, in-use dining/kitchen scene, set or scale layout",
        "roles": ("clean product hero with full kitchen or dining item visible", "close-up of surface, rim, edge, handle, or contact area detail", "kitchen counter, meal prep, serving, or dining-use scene", "set arrangement, serving relationship, size, or storage layout"),
        "detail": "one large kitchen/dining hero poster + one circular magnifier inset for visible detail + exactly 3 light labels: Dining Use, Detail View, Easy Table Setting.",
    },
    "table_linen": {
        "keywords": ("tablecloth", "table cloth", "table runner", "placemat", "table linen", "kitchen linen", "桌布", "餐垫", "桌旗"),
        "style": "modern farmhouse or clean contemporary dining style",
        "lighting": "soft natural daylight with gentle highlights, bright but realistic",
        "material": "printed pattern/border clarity, edge, drape, and surface finish from verified source evidence",
        "background": "neutral dining room or clean tabletop setting with simple tasteful props",
        "composition": "centered product coverage view, close-up edge detail, lifestyle dining scene, draped-edge layout",
        "roles": ("full table cover hero on dining table", "close-up of printed border, visible surface, and draped edge", "warm wedding/holiday/family dining scene if festive, otherwise clean everyday dining", "rectangular coverage and table-edge layout"),
        "detail": "one large dining-table hero poster + one circular magnifier inset for printed border/draped edge + exactly 3 light labels: Printed Border, Draped Edges, Dining Table Decor.",
    },
    "soft_home_textile": {
        "keywords": ("curtain", "rug", "throw pillow", "cushion cover", "blanket", "textile", "fabric", "linen", "mat", "窗帘", "地毯", "靠垫", "抱枕", "毯"),
        "style": "cozy modern home textile style with soft lifestyle warmth",
        "lighting": "soft natural daylight with gentle highlights, bright but realistic",
        "material": "visible texture, seam, edge, weave, pile, pattern, and finish from source evidence",
        "background": "bright bedroom, living room, sofa, or home textile setting",
        "composition": "full product view, texture close-up, room placement, folded or scale view when supported",
        "roles": ("full textile product hero with shape and pattern preserved", "close-up of visible texture, seam, edge, or pattern", "realistic bedroom, sofa, living room, or floor-placement scene", "folded, draped, set, placement, or size relationship if supported"),
        "detail": "one large room-placement hero poster + one circular magnifier inset for visible edge texture + exactly 3 light labels: Soft Texture, Finished Edge, Home Decor.",
    },
    "home_decor_wall": {
        "keywords": ("wall decor", "wall art", "wall hanging", "sculpture", "statue", "figurine", "mirror", "poster", "sign", "decor", "墙饰", "壁饰", "挂饰", "雕塑", "摆件", "镜"),
        "style": "clean modern home decor style with gallery-like presentation",
        "lighting": "soft natural daylight with gentle highlights, bright but realistic",
        "material": "surface texture, shape, finish, hanging point, dimensional detail, and craftsmanship visible in source",
        "background": "neutral living room, bedroom, hallway, shelf, or wall setting",
        "composition": "front product view, texture/finish close-up, room placement, arrangement or scale view",
        "roles": ("front product hero showing the decor item clearly", "close-up of texture, finish, pattern, hanging point, or handmade detail", "realistic wall, shelf, hallway, bedroom, or living-room placement", "scale, combination, wall placement, shelf placement, or arrangement view"),
        "detail": "one large home-decor hero poster + one circular magnifier inset for texture/finish detail + exactly 3 light labels: Room Accent, Detail View, Easy Placement.",
    },
    "lighting_electrical_allowed": {
        "keywords": ("lamp", "light", "lighting", "lantern", "shade", "night light", "灯", "灯具", "照明"),
        "style": "clean home lighting presentation without certification or power claims",
        "lighting": "balanced room lighting that shows lamp shape without exaggerated glow",
        "material": "shade, switch, base, cable, mounting part, or visible structure only when present",
        "background": "bedside table, desk, shelf, or neutral room placement",
        "composition": "full product view, structure close-up, room placement scene, size or installation relationship",
        "roles": ("full lighting product hero with exact shape preserved", "close-up of shade, switch, base, mount, or visible structure", "indoor placement scene without adding power, certification, or safety claims", "size, installation, cable, base, or placement relationship if supported"),
        "detail": "one large room-lighting hero poster + one circular magnifier inset for structure detail + exactly 3 light labels: Home Lighting, Detail View, Easy Placement.",
    },
    "automotive_accessories": {
        "keywords": ("automotive", "car", "vehicle", "steering", "auto", "seat", "dashboard", "汽车", "车载", "方向盘", "车内"),
        "style": "clean functional automotive accessory style",
        "lighting": "bright neutral commercial lighting that shows structure clearly",
        "material": "mounting point, grip, anti-slip area, clasp, fit surface, and visible structure",
        "background": "clean car interior or neutral product backdrop",
        "composition": "full product view, detail close-up, car interior use scene, fit or placement view",
        "roles": ("full automotive accessory hero with product complete", "close-up of mounting point, grip, clasp, texture, or fit surface", "realistic car interior placement without inventing vehicle compatibility", "fit, installation position, placement, or size relationship if supported"),
        "detail": "one large car-placement hero poster + one circular magnifier inset for structure detail + exactly 3 light labels: Car Interior, Detail View, Clean Fit.",
    },
    "pet_supplies": {
        "keywords": ("pet", "dog", "cat", "grooming", "leash", "bowl", "feeder", "feeding", "slow feeder", "lick", "paw", "宠物", "狗", "猫", "碗", "喂食", "慢食", "梳", "牵引"),
        "style": "friendly practical pet-care lifestyle style",
        "lighting": "clean bright marketplace lighting with realistic shadows",
        "material": "visible grip, teeth, bowl edge, strap, latch, comfort-facing surface, or usable structure without material claims",
        "background": "clean home pet-care scene without showing distress or medical claims",
        "composition": "full product view, surface/detail close-up, calm home-use scene, size/use relationship",
        "roles": ("clear pet-supply product hero with exact product preserved", "close-up of grip, teeth, bowl rim, strap, latch, or visible structure", "calm home pet-care or pet living scene without medical or safety claims", "size, use relationship, set, or storage layout when supported"),
        "detail": "one large calm home-use hero poster + one circular magnifier inset for grip/detail + exactly 3 light labels: Home Use, Detail View, Compact Size.",
    },
    "garden_outdoor": {
        "keywords": ("garden", "outdoor", "patio", "yard", "plant", "watering", "sprayer", "soil", "camping", "花园", "园艺", "户外", "浇水", "露营"),
        "style": "clean outdoor-use style, practical and natural",
        "lighting": "bright natural daylight with realistic outdoor shadows",
        "material": "spray head, blade, stake, hook, waterproof-looking structure only when visible, or outdoor-use part",
        "background": "garden, patio, yard, balcony, or clean outdoor work surface",
        "composition": "full product view, functional detail close-up, outdoor use scene, coverage or installation layout",
        "roles": ("full garden or outdoor product hero", "close-up of blade, nozzle, stake, handle, clip, or functional detail", "realistic garden, patio, yard, balcony, or outdoor-use scene", "coverage, installation, set, storage, or size relationship"),
        "detail": "one large outdoor-use hero poster + one circular magnifier inset for functional detail + exactly 3 light labels: Outdoor Use, Detail View, Easy Setup.",
    },
    "party_festival": {
        "keywords": ("party", "wedding", "christmas", "halloween", "holiday", "festival", "birthday", "decoration", "派对", "婚庆", "节日", "圣诞", "万圣", "生日"),
        "style": "warm festive US/EU home style, tasteful and not exaggerated",
        "lighting": "soft warm lighting with clean product visibility",
        "material": "pattern, hanging point, printed detail, folded edge, and set contents visible from source",
        "background": "warm festive dining, wall, table, or home party setting, tasteful and not crowded",
        "composition": "full product view, pattern/detail close-up, tasteful festive use scene, set quantity or layout",
        "roles": ("full festive product hero with exact item and quantity preserved", "close-up of pattern, hanging point, print, surface, or edge detail", "tasteful holiday, birthday, wedding, party table, or wall scene", "set quantity, layout, arrangement, or installation relationship"),
        "detail": "one large festive-use hero poster + one circular magnifier inset for pattern/detail + exactly 3 light labels: Party Decor, Detail View, Easy Setup.",
    },
    "beauty_personal_accessory": {
        "keywords": ("beauty", "makeup", "cosmetic", "hair", "brush", "comb", "mirror", "nail", "personal care", "美妆", "化妆", "梳", "镜", "指甲", "头发"),
        "style": "clean vanity-table personal accessory style without efficacy claims",
        "lighting": "soft bright vanity lighting with realistic shadows",
        "material": "brush head, clip, mirror surface, storage, comb teeth, edge, or visible structure",
        "background": "vanity table, travel pouch, bathroom counter, or neutral studio surface",
        "composition": "full product view, visible detail close-up, vanity or travel scene, set or portability layout",
        "roles": ("full beauty or personal accessory hero", "close-up of brush head, clip, mirror, comb teeth, storage, or edge detail", "vanity, travel, bathroom counter, or desk-use scene without medical efficacy claims", "set, portability, size, storage, or multi-piece relationship"),
        "detail": "one large vanity-use hero poster + one circular magnifier inset for visible detail + exactly 3 light labels: Daily Use, Detail View, Portable Design.",
    },
    "apparel_accessories": {
        "keywords": ("apparel accessory", "belt", "hat", "scarf", "glove", "sock", "hair accessory", "wear", "fashion accessory", "服饰", "腰带", "帽", "围巾", "发饰"),
        "style": "clean fashion accessory marketplace style",
        "lighting": "soft bright studio or lifestyle lighting",
        "material": "buckle, stitching, edge, clasp, texture, opening, or wearable structure visible from source",
        "background": "neutral studio, closet, tabletop, or wearable lifestyle setting",
        "composition": "full product view, texture/detail close-up, wearable scene, size or color layout",
        "roles": ("full apparel accessory hero with shape and color preserved", "close-up of buckle, seam, edge, clasp, texture, or opening detail", "wearable or carrying lifestyle scene without model/body claims", "size, capacity, color, set, or storage relationship"),
        "detail": "one large fashion-accessory hero poster + one circular magnifier inset for detail + exactly 3 light labels: Everyday Wear, Detail View, Easy Match.",
    },
    "bags_cases": {
        "keywords": ("bag", "case", "pouch", "wallet", "backpack", "tote", "organizer bag", "storage case", "包", "袋", "箱", "盒", "收纳包"),
        "style": "clean travel and storage accessory style",
        "lighting": "soft bright studio or lifestyle lighting",
        "material": "zipper, compartment, handle, strap, edge, opening, and visible storage structure",
        "background": "desk, travel surface, closet, shelf, or neutral studio backdrop",
        "composition": "full bag/case view, zipper/detail close-up, travel or desk scene, capacity/interior layout",
        "roles": ("full bag or case hero with exact shape preserved", "close-up of zipper, compartment, handle, strap, edge, or opening", "travel, desk, closet, or storage-use scene", "capacity, interior, set, size, or organization layout"),
        "detail": "one large bag/case hero poster + one circular magnifier inset for zipper or compartment detail + exactly 3 light labels: Storage Use, Detail View, Easy Carry.",
    },
    "jewelry_small_accessory": {
        "keywords": ("jewelry", "earring", "necklace", "bracelet", "ring", "charm", "brooch", "keychain", "首饰", "耳环", "项链", "手链", "戒指", "钥匙扣"),
        "style": "clean small-accessory studio style, refined but not luxury-claiming",
        "lighting": "soft studio lighting with crisp edge highlights",
        "material": "clasp, edge, texture, pendant, chain, charm, or visible decorative detail",
        "background": "neutral tabletop, jewelry tray, hand-scale scene, or gift-box surface",
        "composition": "full accessory view, clasp/detail close-up, wearing or gift scene, scale or set layout",
        "roles": ("full jewelry or small accessory hero", "close-up of clasp, texture, pendant, chain, edge, or decorative detail", "wearing, tabletop, gift, or tray scene without luxury/brand claims", "scale, set, color variant, or gift-layout relationship"),
        "detail": "one large small-accessory hero poster + one circular magnifier inset for clasp/detail + exactly 3 light labels: Detail View, Gift Ready, Easy Match.",
    },
    "office_school_supplies": {
        "keywords": ("office", "school", "desk", "binder", "clip", "pen", "notebook", "label", "file", "办公", "学校", "笔", "本", "夹", "文件"),
        "style": "clean desk and school-supply marketplace style",
        "lighting": "bright neutral desk lighting with realistic shadows",
        "material": "clip, tip, binding, label area, edge, pages, or storage structure",
        "background": "office desk, study desk, shelf, or neutral studio surface",
        "composition": "full product view, functional detail close-up, office/school desk scene, quantity or storage layout",
        "roles": ("full office or school supply hero", "close-up of clip, tip, binding, label area, edge, or functional detail", "office desk, study desk, or school-use scene", "quantity, set, storage, page, or organization layout"),
        "detail": "one large desk-use hero poster + one circular magnifier inset for functional detail + exactly 3 light labels: Desk Use, Detail View, Organized Setup.",
    },
    "packaging_bags": {
        "keywords": ("packaging bag", "mailing bag", "poly mailer", "zip bag", "storage bag", "envelope", "packaging", "包装袋", "快递袋", "自封袋", "信封", "打包"),
        "style": "clean packing and shipping supply style",
        "lighting": "bright neutral commercial lighting that shows shape and opening clearly",
        "material": "seal, zipper, opening, thickness impression, texture, folded edge, and capacity relationship",
        "background": "clean packing table, shipping desk, storage shelf, or neutral studio surface",
        "composition": "full packaging product view, seal/detail close-up, packing-use scene, quantity/size/capacity layout",
        "roles": ("full packaging bag product hero; packaging-only is allowed because this is the product", "close-up of seal, zipper, opening, edge, texture, or thickness impression", "packing, shipping, storage, or mail-prep scene", "quantity, size, capacity, folded, or stacked layout"),
        "detail": "one large packing-supply hero poster + one circular magnifier inset for seal/opening detail + exactly 3 light labels: Packing Use, Detail View, Size Options.",
    },
    "baby_kids_safe_goods": {
        "keywords": ("baby", "kids", "child", "children", "toddler", "nursery", "婴儿", "儿童", "孩子", "母婴"),
        "style": "soft home family-use style without safety or medical claims",
        "lighting": "soft bright home lighting with clean product visibility",
        "material": "edge, structure, opening, accessory, and visible details without safety or age claims",
        "background": "clean family room, nursery shelf, play table, or neutral studio surface",
        "composition": "full product view, edge/detail close-up, family-use scene, size or set layout",
        "roles": ("full product hero with exact item and accessories preserved", "close-up of edge, structure, opening, accessory, or visible detail", "family home, nursery shelf, or table-use scene without safety/medical claims", "size, set, storage, or layout relationship"),
        "detail": "one large family-use hero poster + one circular magnifier inset for visible detail + exactly 3 light labels: Home Use, Detail View, Compact Size.",
    },
    "general_small_goods": {
        "keywords": (),
        "style": "clean modern marketplace style for US/EU shoppers",
        "lighting": "clean bright marketplace lighting with realistic shadows",
        "material": "real visible surface, finish, structure, and strongest detail from the reference image; do not name a material without verified evidence",
        "background": "neutral bright lifestyle background with minimal props",
        "composition": "full product view, real detail close-up, realistic use scene, scale or layout view",
        "roles": ("clear full product hero with the sellable item complete", "strongest real visible surface, edge, opening, or structure detail", "realistic category-matched lifestyle scene with the product prominent", "scale, set, installation, storage, or layout view when supported"),
        "detail": "one large category-matched hero poster + one circular magnifier inset for the strongest real detail + exactly 3 short factual English labels.",
    },
}


def has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def prompt_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_prompt_source_value(key: str, value: Any) -> Any:
    if key == "captured_fields" and isinstance(value, dict):
        return {
            str(item_key): item_value
            for item_key, item_value in value.items()
            if str(item_key) not in _CAPTAURED_FIELD_PROMPT_DENYLIST
        }
    return value


# ---------------------------------------------------------------------------
# 来源证据拼接（等价原版 _build_value_evidence / _format_prompt_attributes）
# ---------------------------------------------------------------------------

def source_attribute_pairs(raw: dict[str, Any]) -> dict[str, str]:
    """将来源结构化属性（dict/list 均可）拍平为 {名称: 值}。"""
    pairs: dict[str, str] = {}

    def collect(value: Any) -> list[tuple[str, str]]:
        if isinstance(value, dict):
            return [(str(name), str(item)) for name, item in value.items() if name and item not in (None, "")]
        if isinstance(value, list):
            result: list[tuple[str, str]] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("attribute_name") or item.get("attribute_name_cn") or item.get("key")
                item_value = item.get("value") or item.get("attribute_value")
                if name and item_value not in (None, ""):
                    result.append((str(name), str(item_value)))
            return result
        return []

    for key in ("source_attributes", "selected_attributes", "attributes", "variant_groups", "variant_combinations"):
        for name, item_value in collect(raw.get(key)):
            pairs.setdefault(name, item_value)
    captured = raw.get("captured_fields")
    if isinstance(captured, dict):
        for key in ("source_attributes", "selected_attributes", "attributes", "specs"):
            for name, item_value in collect(captured.get(key)):
                pairs.setdefault(name, item_value)
    return pairs


def _is_material_attribute_name(attr_name: str) -> bool:
    text = str(attr_name or "").lower()
    return bool(
        re.search(
            r"\bmaterial\b|\bfabric\s*(?:content|type|composition)?\b|\btextile\b|\bcomposition\b"
            r"|材质|材料|面料|成分|织物|纺织|里料|内里|质地|食品接触",
            text,
        )
    )


def clean_title_search_term(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"[^A-Za-z0-9&+ -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -,&+")
    if not text:
        return ""
    small_words = {"and", "or", "for", "with", "of", "to", "in", "on"}
    words = [word.upper() if word.isupper() and len(word) <= 4 else word.capitalize() for word in text.split()]
    return " ".join(word.lower() if idx and word.lower() in small_words else word for idx, word in enumerate(words))[:80]


def clean_verified_material_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    for source, translated in sorted(_VERIFIED_MATERIAL_TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
        if source in compact:
            return translated
    cleaned = clean_title_search_term(text)
    return cleaned or text[:40]


def trusted_material_evidence(raw: dict[str, Any]) -> str:
    """从来源结构化属性中提取可信材质证据（等价原版 _trusted_material_evidence）。"""
    for name, value in source_attribute_pairs(raw).items():
        if not _is_material_attribute_name(name):
            continue
        material = clean_verified_material_value(value)
        if material and material.lower() not in {"other", "others", "unknown", "not specified", "not applicable", "none"}:
            return material
    for key in ("source_attributes", "attributes", "selected_attributes"):
        value = raw.get(key)
        items: list[tuple[Any, Any]] = []
        if isinstance(value, dict):
            items = list(value.items())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("attribute_name") or item.get("key")
                    item_value = item.get("value") or item.get("attribute_value")
                    items.append((name, item_value))
        for name, item_value in items:
            if not _is_material_attribute_name(name):
                continue
            material = clean_verified_material_value(item_value)
            if material and material.lower() not in {"other", "others", "unknown", "not specified", "not applicable", "none"}:
                return material
    return ""


def is_material_attribute(item: dict[str, Any]) -> bool:
    name = " ".join(str(item.get(key) or "") for key in ("attribute_name_cn", "attribute_name_en", "name")).lower()
    return bool(
        re.search(
            r"\bmaterial\b|\bfabric\s*(?:content|type|composition)?\b|\btextile\b|\bcomposition\b"
            r"|材质|材料|面料|成分|织物|纺织|里料|内里|质地",
            name,
        )
    )


def prompt_safe_attributes(attributes: list[Any], *, trusted_material: str) -> list[Any]:
    safe: list[Any] = []
    for item in attributes:
        if not isinstance(item, dict):
            safe.append(item)
            continue
        if not is_material_attribute(item):
            safe.append(item)
            continue
        if trusted_material:
            safe.append(
                {
                    **item,
                    "value_name_en": trusted_material,
                    "value": trusted_material,
                    "source": "trusted_source_attribute",
                }
            )
    return safe


def format_prompt_attributes(attributes: list[Any], *, limit: int = 8) -> str:
    parts: list[str] = []
    for item in attributes[:limit]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("attribute_name_en") or item.get("attribute_name_cn") or item.get("name") or "").strip()
        value = str(item.get("value_name_en") or item.get("value_name_cn") or item.get("value") or "").strip()
        if name and value:
            parts.append(f"{name}: {value}")
        elif name:
            parts.append(name)
    return "; ".join(parts)


def build_value_evidence(raw: dict[str, Any], *, title: str, category: str, category_match: dict[str, Any], trusted_material: str = "") -> str:
    parts: list[str] = []
    for label, key in (
        ("source title", "title"),
        ("product name", "product_name"),
        ("source category", "source_category_path"),
        ("declared price", "declared_price"),
        ("source price", "price"),
    ):
        value = str(raw.get(key) or "").strip()
        if value and value not in parts:
            parts.append(f"{label}: {value[:180]}")
    attr_text = format_prompt_attributes(prompt_safe_attributes(category_match.get("resolved_attributes") or [], trusted_material=trusted_material))
    if attr_text:
        parts.append(f"category attributes: {attr_text}")
    for key in ("selected_attributes", "source_attributes", "variant_groups", "variant_combinations", "captured_fields"):
        value = raw.get(key)
        if value:
            text = prompt_text(_safe_prompt_source_value(key, value))
            if text:
                parts.append(f"{key}: {text[:260]}")
    if not parts:
        parts.append(f"title/category only: {title[:180]} | {category[:120]}")
    return " | ".join(parts)[:1200]


def build_category_match(raw: dict[str, Any], category: str) -> dict[str, Any]:
    """本项目无类目库，用来源数据构造最小 category_match 供视觉/证据函数使用。"""
    attributes = raw.get("source_attributes") or raw.get("selected_attributes") or []
    if not isinstance(attributes, list):
        attributes = []
    category = str(category or "").strip()
    return {
        "resolved_attributes": attributes,
        "required_attributes": [],
        "matched_terms": [],
        "generation_category": category,
        "category_path": category,
        "category_name": category,
        "used_for_generation": bool(category),
    }


# ---------------------------------------------------------------------------
# 视觉规划层（等价原版 _visual_prompt_plan 依赖链）
# ---------------------------------------------------------------------------

def visual_source_text(raw: dict[str, Any], *, title: str, category: str, category_path: str) -> str:
    parts = [title, category, category_path]
    for key in (
        "product_name",
        "source_category_path",
        "source_attributes",
        "selected_attributes",
        "attributes",
        "variant_groups",
        "variant_combinations",
        "captured_fields",
        "description",
        "desc",
    ):
        value = raw.get(key)
        if value:
            parts.append(prompt_text(_safe_prompt_source_value(key, value)))
    return " | ".join(str(part) for part in parts if str(part or "").strip()).lower()


def visual_traits(text: str) -> dict[str, bool]:
    return {
        "set_pack": bool(re.search(r"\b(set|pack|pcs|pieces|bundle)\b|\d+\s*(?:pcs|pieces|pack|set)", text, flags=re.IGNORECASE)),
        "red_festive": has_any(text, ("red", "gold", "wedding", "holiday", "festival", "christmas")),
        "wood": has_any(text, ("wood", "wooden", "bamboo")),
        "metal": has_any(text, ("metal", "iron", "steel", "aluminum")),
        "fabric": has_any(text, ("fabric", "cotton", "linen", "polyester", "woven", "cloth", "textile")),
        "rattan": has_any(text, ("rattan", "wicker")),
        "printed": has_any(text, ("print", "printed", "pattern", "border", "flat printing", "印花", "图案", "边框", "印刷")),
        "wall": has_any(text, ("wall",)),
        "table": has_any(text, ("table", "dining")),
        "storage": has_any(text, ("storage", "organizer", "holder", "收纳", "整理")),
        "handmade": has_any(text, ("handmade", "hand woven", "woven", "macrame", "手工", "编织")),
        "outdoor": has_any(text, ("outdoor", "garden", "patio", "yard", "户外", "花园")),
    }


def product_visual_identity(*, title: str, category_path: str, traits: dict[str, bool], raw: dict[str, Any], category_match: dict[str, Any], trusted_material: str) -> str:
    title_part = str(title or "").strip()[:120]
    attr_text = format_prompt_attributes(prompt_safe_attributes(category_match.get("resolved_attributes") or [], trusted_material=trusted_material), limit=4)
    trait_parts: list[str] = []
    if traits.get("red_festive"):
        trait_parts.append("festive red/gold look")
    if traits.get("printed"):
        trait_parts.append("visible printed pattern or border")
    if trusted_material:
        trait_parts.append(f"verified source material: {trusted_material}")
    if traits.get("set_pack"):
        trait_parts.append("source-supported set quantity")
    source_bits = "; ".join(part for part in (attr_text, ", ".join(trait_parts)) if part)
    if source_bits:
        return f"{title_part}; preserve source-supported details: {source_bits}"[:320]
    return f"{title_part}; preserve the exact visible product from the reference image and category {category_path}"[:320]


def visual_text_has_pet_category(category_path: str) -> bool:
    value = str(category_path or "").lower()
    return has_any(value, ("pet", "pet supplies", "dog", "cat", "宠物", "狗", "猫"))


def visual_text_has_pet_feeding_identity(text: str) -> bool:
    value = str(text or "").lower()
    pet_context = has_any(value, ("pet", "dog", "cat", "宠物", "狗", "猫"))
    feeding_context = has_any(
        value,
        (
            "pet bowl",
            "dog bowl",
            "cat bowl",
            "feeding bowl",
            "feeder bowl",
            "slow feeder",
            "lick bowl",
            "licking bowl",
            "licking ball",
            "yogurt licking",
            "宠物碗",
            "狗碗",
            "猫碗",
            "喂食",
            "慢食",
            "舔食",
        ),
    )
    return pet_context and feeding_context


def visual_role_plan(family: str) -> dict[str, Any]:
    return VISUAL_CATEGORY_ROLE_LIBRARY.get(family) or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]


def visual_category_family(category_path: str, text: str) -> str:
    haystack = f"{category_path} | {text}".lower()
    if visual_text_has_pet_category(category_path) or visual_text_has_pet_feeding_identity(haystack):
        return "pet_supplies"
    best_family = ""
    best_score = 0
    for rank, family in enumerate(VISUAL_CATEGORY_ROLE_PRIORITY):
        plan = VISUAL_CATEGORY_ROLE_LIBRARY.get(family) or {}
        keywords = tuple(str(keyword or "").lower() for keyword in (plan.get("keywords") or ()) if str(keyword or "").strip())
        score = sum(1 for keyword in keywords if keyword and keyword in haystack)
        if score > best_score:
            best_family = family
            best_score = score
        elif score == best_score and score > 0 and best_family:
            best_rank = VISUAL_CATEGORY_ROLE_PRIORITY.index(best_family)
            if rank < best_rank:
                best_family = family
    return best_family or "general_small_goods"


def visual_style_for_family(family: str, traits: dict[str, bool]) -> str:
    if traits.get("red_festive") or family == "party_festival":
        return "warm festive US/EU home style, tasteful and not exaggerated"
    return str(visual_role_plan(family).get("style") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["style"])


def lighting_plan_for_family(family: str) -> str:
    return str(visual_role_plan(family).get("lighting") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["lighting"])


def material_plan_for_family(family: str, traits: dict[str, bool], *, trusted_material: str) -> str:
    material_focus: list[str] = []
    if traits.get("printed"):
        material_focus.append("printed pattern/border clarity")
    if trusted_material:
        material_focus.append(f"verified {trusted_material} surface/finish")
    if material_focus:
        return "; ".join(material_focus)
    return str(visual_role_plan(family).get("material") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["material"])


def background_plan_for_family(family: str, traits: dict[str, bool]) -> str:
    if family == "party_festival" or traits.get("red_festive"):
        return "warm festive dining or home party setting, tasteful and not crowded"
    return str(visual_role_plan(family).get("background") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["background"])


def composition_plan_for_family(family: str) -> str:
    return str(visual_role_plan(family).get("composition") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["composition"])


def scene_plan_for_family(family: str, traits: dict[str, bool]) -> str:
    roles = list(visual_role_plan(family).get("roles") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["roles"])
    return "; ".join(f"{index} {role}" for index, role in enumerate(roles[:4], start=1)) + "."


def video_shot_plan_for_family(family: str, traits: dict[str, bool]) -> str:
    roles = list(visual_role_plan(family).get("roles") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["roles"])
    shot_roles = ("hero", "detail", "use_scene", "capacity_or_set")
    return "; ".join(
        f"{index} {shot_role}: {role}"
        for index, (shot_role, role) in enumerate(zip(shot_roles, roles[:4]), start=1)
        if str(role or "").strip()
    ) + "."


def detail_plan_for_family(family: str, traits: dict[str, bool], *, trusted_material: str) -> str:
    detail = str(visual_role_plan(family).get("detail") or VISUAL_CATEGORY_ROLE_LIBRARY["general_small_goods"]["detail"])
    if trusted_material:
        return detail.replace(" + exactly", f", verified {trusted_material} + exactly")
    return detail


def visual_prompt_plan(raw: dict[str, Any], *, title: str, category_path: str, category: str, category_match: dict[str, Any], trusted_material: str) -> dict[str, str]:
    text = visual_source_text(raw, title=title, category=category, category_path=category_path)
    family = visual_category_family(category_path, text)
    traits = visual_traits(text)
    product_identity = product_visual_identity(
        title=title,
        category_path=category_path,
        traits=traits,
        raw=raw,
        category_match=category_match,
        trusted_material=trusted_material,
    )
    return {
        "product_visual_identity": product_identity,
        "visual_family": family,
        "visual_style": visual_style_for_family(family, traits),
        "lighting_plan": lighting_plan_for_family(family),
        "material_plan": material_plan_for_family(family, traits, trusted_material=trusted_material),
        "background_plan": background_plan_for_family(family, traits),
        "composition_plan": composition_plan_for_family(family),
        "scene_plan": scene_plan_for_family(family, traits),
        "video_shot_plan": video_shot_plan_for_family(family, traits),
        "detail_plan": detail_plan_for_family(family, traits, trusted_material=trusted_material),
    }


# ---------------------------------------------------------------------------
# 提示词上下文总装（等价原版 _listing_prompt_context）
# ---------------------------------------------------------------------------

def listing_prompt_context(raw: dict[str, Any], *, title: str, category: str, category_match: dict[str, Any] | None = None) -> dict[str, str]:
    category_match = category_match or build_category_match(raw, category)
    trusted_material = trusted_material_evidence(raw)
    category_path = str(
        category_match.get("generation_category")
        or category_match.get("category_path")
        or category_match.get("category_name")
        or category
        or ""
    ).strip()
    resolved_attrs = category_match.get("resolved_attributes") if isinstance(category_match.get("resolved_attributes"), list) else []
    required_attrs = category_match.get("required_attributes") if isinstance(category_match.get("required_attributes"), list) else []
    prompt_attrs = prompt_safe_attributes(resolved_attrs or required_attrs, trusted_material=trusted_material)
    attr_text = format_prompt_attributes(prompt_attrs)
    matched_terms = category_match.get("matched_terms") if isinstance(category_match.get("matched_terms"), list) else []
    visual_plan = visual_prompt_plan(
        raw,
        title=title,
        category_path=category_path,
        category=category,
        category_match=category_match,
        trusted_material=trusted_material,
    )
    return {
        "category": str(category or "").strip(),
        "category_path": category_path,
        "required_attributes": attr_text,
        "matched_terms": ", ".join(str(term) for term in matched_terms[:12] if str(term).strip()),
        "value_evidence": build_value_evidence(raw, title=title, category=category, category_match=category_match, trusted_material=trusted_material),
        "verified_material_evidence": (
            f"Verified structured source attribute: {trusted_material}."
            if trusted_material
            else "None. Do not state any material, fabric composition, wood, metal, rattan, plastic, silicone, or similar material term."
        ),
        "trusted_material": trusted_material,
    } | visual_plan
