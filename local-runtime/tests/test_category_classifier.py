from __future__ import annotations

import time

import pytest

from wh_local.modules.product_processing.domain.category_classifier import (
    CATEGORY_LABELS,
    classify_title,
    classify_titles,
    normalize_title,
)
from wh_local.modules.product_processing.domain.category_image_presets import (
    CATEGORY_IMAGE_PROMPTS,
)

CLEAR_TITLES: tuple[tuple[str, str], ...] = (
    # home kitchen
    ("Bamboo Cutting Board with Juice Groove", "home_kitchen"),
    ("Foldable Fabric Closet Storage Bin", "home_kitchen"),
    ("Washed Linen Duvet Cover Set", "home_kitchen"),
    # apparel
    ("Men's Cotton Hoodie Oversized", "apparel_accessories"),
    ("Women's Leather Crossbody Bag", "apparel_accessories"),
    ("Polarized Sunglasses for Adults", "apparel_accessories"),
    # pet
    ("Durable Rubber Dog Chew Toy", "pet_supplies"),
    ("Adjustable Cat Collar with Bell", "pet_supplies"),
    ("Washable Pet Bed for Small Dogs", "pet_supplies"),
    # office
    ("Desktop Pen Holder with 3 Compartments", "office_stationery"),
    ("A5 Hardcover Notebook", "office_stationery"),
    ("Heavy Duty Stapler", "office_stationery"),
    # sports
    ("Non-Slip Yoga Mat 6mm", "sports_outdoor"),
    ("Resistance Band Set with Handles", "sports_outdoor"),
    ("Portable Camping Hook", "sports_outdoor"),
    # auto
    ("Car Windshield Sunshade", "auto_accessories"),
    ("Seat Gap Filler Organizer", "auto_accessories"),
    ("Microfiber Car Wash Towel", "auto_accessories"),
    # tools
    ("Precision Screwdriver Set with Case", "hand_tools"),
    ("Adjustable Wrench 10 Inch", "hand_tools"),
    ("Self-Adhesive Wall Hook Heavy Duty", "hand_tools"),
    # passive tech
    ("Shockproof Phone Case for iPhone", "passive_tech"),
    ("Laptop Sleeve 14 Inch", "passive_tech"),
    ("Cable Organizer Pouch", "passive_tech"),
    # beauty tools
    ("Makeup Brush Set with Case", "beauty_tools"),
    ("Glass Nail File", "beauty_tools"),
    ("Manual Eyebrow Razor", "beauty_tools"),
    # adult games
    ("Adult Board Game for Game Night", "adult_games"),
    ("1000 Piece Jigsaw Puzzle", "adult_games"),
    ("Playing Cards Deck", "adult_games"),
    # garden
    ("Terracotta Plant Pot with Drainage", "garden_tools"),
    ("Garden Pruning Shears", "garden_tools"),
    ("Seed Starter Tray 12 Cells", "garden_tools"),
    # general fallback
    ("Stainless Steel Keychain", "general"),
    ("Miniature Collectible Figurine", "general"),
    ("Multi-Purpose Silicone Grip Pad", "general"),
)

AMBIGUOUS_TITLES: tuple[tuple[str, str], ...] = (
    ("Pet Bowl Stainless Steel", "pet_supplies"),
    ("Ceramic Soup Bowl Set", "home_kitchen"),
    ("Yoga Mat Non-Slip", "sports_outdoor"),
    ("Car Floor Mat", "auto_accessories"),
    ("Pet Mat Washable", "pet_supplies"),
    ("Woven Placemat Set", "home_kitchen"),
    ("Phone Case with Card Holder", "passive_tech"),
    ("Glasses Case Hard Shell", "apparel_accessories"),
    ("Pet Brush for Shedding", "pet_supplies"),
    ("Makeup Brush Set", "beauty_tools"),
    ("Dish Cleaning Brush", "home_kitchen"),
    ("Laptop Sleeve 13 Inch", "passive_tech"),
    ("Cup Sleeve Neoprene", "home_kitchen"),
    ("Cable Organizer Bag", "passive_tech"),
    ("Desk Organizer with Drawers", "office_stationery"),
    ("Closet Organizer Fabric", "home_kitchen"),
    ("Car Seat Gap Organizer", "auto_accessories"),
    ("Wall Hook Heavy Duty", "hand_tools"),
    ("Decorative Coat Hook", "home_kitchen"),
    ("Sports Water Bottle", "sports_outdoor"),
    ("Insulated Travel Mug", "home_kitchen"),
    ("Foundation Brush", "beauty_tools"),
    ("Eyeshadow Brush", "beauty_tools"),
    ("Data Cable Organizer", "passive_tech"),
    ("Pet Backpack for Small Dogs", "pet_supplies"),
    ("Kitchen Scissors Heavy Duty", "home_kitchen"),
    ("Car Phone Holder", "auto_accessories"),
    ("Garden Gloves", "garden_tools"),
    ("Gym Gloves", "sports_outdoor"),
    ("Work Gloves", "hand_tools"),
    ("Hair Scissors", "beauty_tools"),
)

EXCLUDED_TITLES: tuple[str, ...] = (
    "Coffee Machine with Grinder",
    "Air Fryer 5L",
    "Power Bank 20000mAh",
    "Bluetooth Speaker Waterproof",
    "Electric Toothbrush",
    "Cordless Drill Set",
    "Dash Cam 4K",
    "Dog Food Chicken Flavor",
    "Cat Treats Salmon",
    "Kids Building Blocks",
    "Baby Plush Toy",
    "Magnetic Phone Case",
    "MagSafe Charger",
    "Large Outdoor Tent",
    "Lawn Mower Electric",
)


@pytest.mark.parametrize(("title", "expected"), CLEAR_TITLES)
def test_classify_clear_titles(title: str, expected: str) -> None:
    decision = classify_title(title)
    assert decision.excluded is False
    assert decision.category_id == expected, decision
    assert decision.label == CATEGORY_LABELS[expected]


@pytest.mark.parametrize(("title", "expected"), AMBIGUOUS_TITLES)
def test_classify_ambiguous_titles(title: str, expected: str) -> None:
    decision = classify_title(title)
    assert decision.excluded is False
    assert decision.category_id == expected, decision


@pytest.mark.parametrize("title", EXCLUDED_TITLES)
def test_excluded_titles_are_flagged(title: str) -> None:
    decision = classify_title(title)
    assert decision.excluded is True
    assert decision.category_id == "general"
    assert decision.exclusion_reason


# 1688 来源标题是中文：必须能命中品类，否则每条都落到 general，
# 品类场景提示词就永远用不上。
CHINESE_TITLES: tuple[tuple[str, str], ...] = (
    ("新款爱心蝴蝶结镂空钥匙扣DIY拼豆配饰少女心书包挂件送闺蜜礼物", "apparel_accessories"),
    ("新款卡通猫咪硅胶笔筒文具收纳袋熊猫学生用品文具盒硅胶笔袋", "office_stationery"),
    ("厨房多功能收纳盒 保鲜盒 塑料", "home_kitchen"),
    ("全自动猫砂盆 宠物用品 猫窝", "pet_supplies"),
    ("露营折叠椅 户外便携 登山", "sports_outdoor"),
    ("多功能螺丝刀套装 五金工具 工具箱", "hand_tools"),
    ("车载手机支架 汽车用品", "auto_accessories"),
    ("化妆刷套装 美妆蛋 面膜", "beauty_tools"),
    ("园艺花盆 洒水壶 育苗", "garden_tools"),
)


@pytest.mark.parametrize(("title", "expected"), CHINESE_TITLES)
def test_chinese_titles_hit_their_category(title: str, expected: str) -> None:
    decision = classify_title(title)
    assert decision.excluded is False
    assert decision.category_id == expected, decision
    assert decision.confidence >= 0.7


def test_empty_title_falls_back_to_general() -> None:
    decision = classify_title("")
    assert decision.category_id == "general"
    assert decision.excluded is False
    assert decision.confidence == 0.0


def test_classify_titles_deduplicates_by_normalized_title() -> None:
    decisions = classify_titles(["Yoga Mat", "yoga mat", "Yoga Mat 6mm"])
    assert len(decisions) == 3
    assert decisions["Yoga Mat"].category_id == "sports_outdoor"
    assert decisions["yoga mat"].category_id == "sports_outdoor"
    assert decisions["Yoga Mat 6mm"].category_id == "sports_outdoor"


def test_normalize_title_handles_punctuation_and_plurals() -> None:
    assert normalize_title("Men's Phone-Cases / Covers") == "men phone case cover"
    assert normalize_title("宠物碗（不锈钢）") == "宠物碗 不锈钢"


def test_classification_is_fast_for_large_batches() -> None:
    titles = [title for title, _ in CLEAR_TITLES] * 30
    started = time.perf_counter()
    classify_titles(titles)
    elapsed = time.perf_counter() - started
    # Pure CPU matching should be far below this generous bound.
    assert elapsed < 1.0


def test_category_image_prompts_are_single_line_and_within_budget() -> None:
    assert len(CATEGORY_IMAGE_PROMPTS) == 12
    for category_id, prompt in CATEGORY_IMAGE_PROMPTS.items():
        assert "\n" not in prompt, category_id
        assert "{" not in prompt and "}" not in prompt, category_id
        assert "```" not in prompt, category_id
        assert len(prompt.split()) <= 110, (category_id, len(prompt.split()))
