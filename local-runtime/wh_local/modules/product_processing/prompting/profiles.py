"""品类档案：只给默认值，不写画面句子。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryProfile:
    key: str
    label: str
    patterns: tuple[str, ...] | str
    size_class: str          # tiny / small / medium / large
    rigidity: str            # rigid / soft / liquid / granular
    usage: str               # worn / worn_pet / held / hung / placed / installed / consumed / headwear
    human_policy: str        # none / hand / body_crop / product_only
    scale_anchor: bool
    placement: tuple[str, ...]
    prop_policy: str         # none / minimal / seasonal
    domain: str              # home / outdoor / kitchen / office / apparel / bath / kids / workshop


PROFILES: tuple[CategoryProfile, ...] = (
    CategoryProfile(
        "small_jewelry", "珠宝/胸针",
        (r"brooch|lapel pin|earring|necklace|pendant|bracelet|jewelry charm|charm bracelet|charm necklace|(?:jewelry|wedding|engagement) ring|"
         r"胸针|徽章|领针|耳环|耳钉|项链|吊坠|手链|戒指"),
        "small", "rigid", "placed", "hand", True,
        ("velvet tray", "open book", "gift box", "coat lapel"), "minimal", "apparel",
    ),
    CategoryProfile(
        "pet_walking", "宠物牵引",
        (r"leash|\blead\b|traction rope|牵引|遛狗|狗链|牵绳"),
        "medium", "soft", "held", "hand", False,
        ("outdoor path",), "seasonal", "outdoor",
    ),
    CategoryProfile(
        "pet_wearable", "宠物服饰",
        (r"pet.*(vest|dress|costume|coat|jacket|hoodie|sweater)|dog.*(vest|coat|harness)|"
         r"(?:dog|cat|pet) harness|猫咪.*(衣|服)|狗狗.*(衣|服)|宠物.*(衣|服)"),
        "medium", "soft", "worn_pet", "product_only", False,
        ("pet",), "minimal", "pet",
    ),
    CategoryProfile(
        "pet_toy", "宠物玩具",
        (r"pet.*toy|dog.*toy|cat.*toy|磨牙|逗猫|逗狗|宠物玩具"),
        "small", "soft", "placed", "none", False,
        ("pet",), "minimal", "pet",
    ),
    CategoryProfile(
        "headwear", "头饰",
        (r"\bhat\b|\bcap\b|headband|headpiece|headwear|tiara|头饰|帽子|发箍|头箍"),
        "small", "soft", "headwear", "product_only", True,
        ("tabletop",), "minimal", "apparel",
    ),
    CategoryProfile(
        "apparel", "服装",
        (r"\bdress\b|shirt|jacket|coat|hoodie|sweater|pants|skirt|apparel|clothing|"
         r"服装|连衣裙|外套|卫衣|裤子|衬衣"),
        "medium", "soft", "worn", "body_crop", False,
        ("bedroom", "wardrobe"), "minimal", "apparel",
    ),
    CategoryProfile(
        "shoes", "鞋",
        (r"shoes?|sneakers?|boots?|sandals?|鞋"),
        "medium", "rigid", "worn", "body_crop", False,
        ("floor",), "minimal", "apparel",
    ),
    CategoryProfile(
        "bags", "包",
        (r"\bbag\b|backpack|tote|handbag|purse|箱包|背包|托特"),
        "medium", "soft", "held", "hand", False,
        ("hook", "table"), "minimal", "apparel",
    ),
    CategoryProfile(
        "electronics", "3C",
        (r"keyboard|mouse|laptop|phone|headphone|charger|cable|usb|"
         r"电脑|键盘|鼠标|耳机|充电|数据线|显示器"),
        "small", "rigid", "placed", "hand", True,
        ("desk",), "minimal", "office",
    ),
    CategoryProfile(
        "kitchen_tool", "厨房工具",
        (r"juicer|squeezer|grater|peeler|utensil|whisk|masher|"
         r"榨汁|削皮|研磨|刨丝|厨房工具"),
        "medium", "rigid", "held", "hand", True,
        ("kitchen counter",), "minimal", "kitchen",
    ),
    CategoryProfile(
        "home_storage", "收纳/置物",
        (r"storage rack|organizer|hanging rack|shelf rack|spice rack|"
         r"置物架|收纳架|挂架|沥水架"),
        "medium", "rigid", "installed", "none", False,
        ("under-cabinet",), "minimal", "kitchen",
    ),
    CategoryProfile(
        "food", "食品",
        (r"coffee|\btea\b|snack|food|bottle|\bjar\b|basket|colander|strainer|"
         r"食品|咖啡|茶|零食|调味|果篮|菜篮|沥水"),
        "medium", "rigid", "consumed", "hand", False,
        ("dining table", "kitchen counter"), "minimal", "kitchen",
    ),
    CategoryProfile(
        "pet_grooming", "宠物美容",
        (r"nail clipper|grooming|\bbrush\b|\bcomb\b|clipper|指甲剪|剃毛|梳子"),
        "small", "rigid", "held", "hand", True,
        ("tabletop",), "minimal", "bath",
    ),
    CategoryProfile(
        "car_accessory", "车载配件",
        (r"seatbelt|car seat|tether|vehicle|车载|安全带"),
        "medium", "soft", "installed", "none", False,
        ("car seat",), "minimal", "car",
    ),
    CategoryProfile(
        "hair_accessory", "发饰/手工材料",
        (r"hair clip|barrette|hair accessory|craft supplies|pre-cut shapes|"
         r"发夹|发饰|手工材料|配件材料"),
        "small", "rigid", "placed", "hand", True,
        ("dressing table",), "minimal", "apparel",
    ),
    CategoryProfile(
        "car_decor", "车载摆件",
        (r"car dashboard|dashboard ornament|car ornament|car decor|"
         r"车载|仪表台|汽车摆件"),
        "small", "rigid", "installed", "none", False,
        ("dashboard",), "minimal", "car",
    ),
    CategoryProfile(
        "coaster", "杯垫/餐垫",
        (r"cup coaster|coaster|placemat|trivet|table pad|"
         r"杯垫|餐垫|隔热垫"),
        "small", "soft", "placed", "hand", False,
        ("coffee table",), "seasonal", "living",
    ),
    CategoryProfile(
        "home_decor", "家居装饰",
        (r"vase|ornament|decoration|decor|figurine|wreath|garland|摆件|装饰|花瓶|挂饰"),
        "medium", "rigid", "placed", "none", False,
        ("sideboard", "mantel", "shelf"), "seasonal", "home",
    ),
    CategoryProfile(
        "soft_goods", "布艺软装",
        (r"curtain|tablecloth|towel|blanket|cushion|pillow|\bmat\b|\brug\b|"
         r"窗帘|桌布|毛巾|毯|靠垫|地垫"),
        "medium", "soft", "placed", "none", False,
        ("sofa", "bed", "table"), "minimal", "home",
    ),
    CategoryProfile(
        "tools", "工具",
        (r"drill|wrench|hammer|screwdriver|tool|工具|电钻|扳手|螺丝刀"),
        "medium", "rigid", "held", "hand", True,
        ("workbench",), "minimal", "workshop",
    ),
)

FALLBACK = CategoryProfile(
    "general", "通用", (), "medium", "rigid", "placed", "none", False,
    ("table",), "minimal", "home",
)


def match_profile(title: str, category: str = "") -> CategoryProfile:
    """按标题/类目匹配档案；顺序即优先级（牵引要先于宠物服饰）。"""

    text = f"{title or ''} {category or ''}"
    for profile in PROFILES:
        patterns = (profile.patterns,) if isinstance(profile.patterns, str) else profile.patterns
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return profile
    return FALLBACK
