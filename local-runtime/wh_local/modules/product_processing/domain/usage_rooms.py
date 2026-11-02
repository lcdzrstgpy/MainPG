"""按产品判断「它真正会被用在哪里」。

背景/场景如果只按模板套（木台面、窗台、厨房台面），就会出现浴室地垫摆在厨房台面上
这种明显不对的画面。判定顺序：

1. 视觉识别给出的 ``usage_scene.place``（最准，直接描述这个 SKU 的真实位置）；
2. 视觉不可用时，按类目 + 标题关键词落到一个具体房间/位置；
3. 都判不出来返回空串，调用方沿用原来的模板场景，不硬猜。

返回的是英文短语，直接拼进生图提示词的 SCENE PLAN。
"""

from __future__ import annotations

# room_id -> (英文场景短语, 关键词)
USAGE_ROOMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "bathroom": (
        "on a real bathroom floor beside the shower, with tiled wall and a folded towel nearby",
        (
            "浴室", "卫浴", "卫生间", "马桶", "淋浴", "浴帘", "浴垫", "地垫", "毛巾", "浴巾",
            "bathroom", "shower", "toilet", "bath mat", "bath towel", "bathroom mat",
        ),
    ),
    "kitchen": (
        "on a real kitchen counter beside the sink and cutting board",
        (
            "厨房", "餐具", "锅", "砧板", "菜板", "保鲜", "水槽", "烘焙", "调味",
            "kitchen", "cookware", "cutting board", "sink", "baking",
        ),
    ),
    "bedroom": (
        "in a calm bedroom: on the bed or the bedside table, soft daylight from a window",
        (
            "卧室", "床品", "床单", "被", "枕", "床垫", "床", "bedroom", "bed", "pillow",
            "duvet", "mattress", "bed sheet",
        ),
    ),
    "laundry": (
        "on a real laundry counter beside the washer, with folded clothes and a basket",
        ("洗衣", "晾衣", "脏衣", "laundry", "drying rack", "washer"),
    ),
    "living_room": (
        "in a real living room: on the sofa or a low side table, window light and a rug",
        ("客厅", "沙发", "抱枕", "靠垫", "茶几", "地毯", "living room", "sofa", "cushion", "rug"),
    ),
    "desk": (
        "on a real home-office desk beside a laptop, notebook and a mug",
        (
            "书桌", "办公", "桌面", "文具", "笔记本", "键盘", "鼠标", "笔筒",
            "desk", "office", "stationery", "notebook", "keyboard", "mouse",
        ),
    ),
    "vanity": (
        "on a real bathroom vanity / dressing table next to everyday skincare bottles and a towel",
        ("化妆", "美妆", "护肤", "梳妆", "美甲", "剃刀", "makeup", "skincare", "vanity", "nail"),
    ),
    "entryway": (
        "in a real entryway: on the console beside the door, with keys and a woven basket",
        ("玄关", "门厅", "鞋柜", "门口", "entryway", "shoe rack", "hallway"),
    ),
    "nursery": (
        "in a bright nursery next to the crib, soft daylight and light wood",
        ("婴儿", "宝宝", "儿童", "母婴", "baby", "nursery", "kids", "toddler"),
    ),
    "pet": (
        "in a home pet corner: on the floor beside food bowls and a pet bed",
        ("宠物", "猫", "狗", "喂食", "牵引", "猫窝", "狗窝", "pet", "dog", "cat", "kennel"),
    ),
    "car": (
        "inside a real car: on the seat or the dashboard console area",
        ("车载", "汽车", "后备箱", "遮阳", "car", "auto", "trunk", "dashboard"),
    ),
    "patio": (
        "on a real outdoor patio table with potted plants and natural daylight",
        ("庭院", "露台", "户外", "露营", "阳台", "patio", "outdoor", "camping", "balcony"),
    ),
    "garden": (
        "in a real garden bed beside potted plants and a watering can",
        ("花园", "园艺", "花盆", "修枝", "育苗", "浇水", "garden", "planter", "pruner"),
    ),
    "gym": (
        "on a home-gym floor beside a yoga mat and a dumbbell",
        ("瑜伽", "健身", "运动", "哑铃", "拉力", "yoga", "fitness", "gym", "dumbbell"),
    ),
    "dining": (
        "on a real dining table set for a simple meal",
        ("餐垫", "餐桌", "杯垫", "dining", "placemat", "coaster"),
    ),
    "workshop": (
        "on a real workshop bench beside hand tools and offcuts",
        ("五金", "工具", "扳手", "螺丝刀", "批头", "tool", "wrench", "screwdriver"),
    ),
}


def detect_room_id(category_text: str = "", title: str = "") -> str:
    """按类目 + 标题关键词判断房间；判不出来返回空串（不硬猜）。"""

    haystack = f"{category_text} {title}".lower()
    best, best_hits = "", 0
    for room_id, (_place, keywords) in USAGE_ROOMS.items():
        hits = sum(1 for keyword in keywords if keyword.lower() in haystack)
        if hits > best_hits:
            best, best_hits = room_id, hits
    return best


def usage_place_text(room_id: str) -> str:
    entry = USAGE_ROOMS.get(str(room_id or "").strip().lower())
    return entry[0] if entry else ""


def resolve_usage_place(
    vision_place: str = "",
    *,
    category_text: str = "",
    title: str = "",
) -> str:
    """优先用视觉识别的位置，其次按品类关键词兜底；都没有返回空串。"""

    explicit = str(vision_place or "").strip()
    if len(explicit) >= 8:
        return explicit
    return usage_place_text(detect_room_id(category_text, title))
