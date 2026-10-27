"""Rule-based product-title classifier.

This module is intentionally deterministic and offline:

* no AI calls, no network I/O, no billing;
* pure CPU string matching (microseconds per title);
* safe to run once per batch before the image pipeline starts.

The classifier returns a category id plus confidence.  Low-confidence or
ambiguous titles deliberately fall back to ``general`` so the caller can keep
the default image prompt or ask a human to review them.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

CATEGORY_LABELS: dict[str, str] = {
    "home_kitchen": "家居与厨房",
    "apparel_accessories": "服装与配饰",
    "pet_supplies": "宠物用品",
    "office_stationery": "办公与文具",
    "sports_outdoor": "运动户外",
    "auto_accessories": "汽车用品",
    "hand_tools": "工具与五金",
    "passive_tech": "纯物理配件",
    "beauty_tools": "美妆个护工具",
    "adult_games": "成人玩具游戏",
    "garden_tools": "园艺庭院工具",
    "general": "未命中兜底",
}

STRONG_WEIGHT = 4
CONTEXT_WEIGHT = 3
NOUN_WEIGHT = 2
EXCLUDED_SCORE = 0

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass(frozen=True)
class CategoryDecision:
    """Result of classifying one product title."""

    category_id: str
    label: str
    confidence: float
    score: int
    runner_up: str = ""
    runner_up_score: int = 0
    matched: tuple[str, ...] = ()
    excluded: bool = False
    exclusion_reason: str = ""
    source: str = "rule"


def _singularize(token: str) -> str:
    """Best-effort singular form for English product nouns."""

    if len(token) <= 3:
        return token
    irregular = {"buses": "bus", "gases": "gas", "lenses": "lens"}
    if token in irregular:
        return irregular[token]
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("ses"):
        return token[:-1]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def normalize_title(title: str) -> str:
    """Normalize a title for matching.

    Lowercase, NFKC, strip possessives and punctuation, collapse whitespace
    and singularize simple English plurals.  Chinese characters are preserved.
    """

    text = unicodedata.normalize("NFKC", str(title or "")).lower()
    text = text.replace("’", "'")
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"[_\-/]+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    tokens = [_singularize(token) for token in text.split()]
    return " ".join(tokens)


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _has_phrase(normalized_text: str, normalized_phrase: str) -> bool:
    if not normalized_phrase:
        return False
    if _has_cjk(normalized_phrase):
        return normalized_phrase in normalized_text
    return f" {normalized_phrase} " in f" {normalized_text} "


# ---------------------------------------------------------------------------
# Strong compounds: specific product phrases.  Weight 4 each.
# ---------------------------------------------------------------------------

_RAW_STRONG_RULES: tuple[tuple[str, str], ...] = (
    # Home & kitchen
    ("cutting board", "home_kitchen"),
    ("chopping board", "home_kitchen"),
    ("chef knife", "home_kitchen"),
    ("kitchen knife", "home_kitchen"),
    ("bread knife", "home_kitchen"),
    ("knife block", "home_kitchen"),
    ("bakeware", "home_kitchen"),
    ("baking pan", "home_kitchen"),
    ("baking sheet", "home_kitchen"),
    ("cake mold", "home_kitchen"),
    ("manual whisk", "home_kitchen"),
    ("spatula", "home_kitchen"),
    ("slotted spoon", "home_kitchen"),
    ("dinnerware", "home_kitchen"),
    ("dinner plate", "home_kitchen"),
    ("plate set", "home_kitchen"),
    ("soup bowl", "home_kitchen"),
    ("ceramic bowl", "home_kitchen"),
    ("mixing bowl", "home_kitchen"),
    ("bowl set", "home_kitchen"),
    ("coffee mug", "home_kitchen"),
    ("mug set", "home_kitchen"),
    ("storage bin", "home_kitchen"),
    ("storage bag", "home_kitchen"),
    ("closet organizer", "home_kitchen"),
    ("wardrobe organizer", "home_kitchen"),
    ("fabric storage", "home_kitchen"),
    ("spice jar", "home_kitchen"),
    ("airtight jar", "home_kitchen"),
    ("duvet cover", "home_kitchen"),
    ("sheet set", "home_kitchen"),
    ("pillowcase", "home_kitchen"),
    ("cushion cover", "home_kitchen"),
    ("table runner", "home_kitchen"),
    ("tablecloth", "home_kitchen"),
    ("placemat", "home_kitchen"),
    ("picture frame", "home_kitchen"),
    ("flower vase", "home_kitchen"),
    ("cooking pot", "home_kitchen"),
    ("stock pot", "home_kitchen"),
    ("travel mug", "home_kitchen"),
    ("insulated mug", "home_kitchen"),
    ("food storage container", "home_kitchen"),
    ("food container", "home_kitchen"),
    ("lunch box", "home_kitchen"),
    ("water bottle", "home_kitchen"),
    ("cup sleeve", "home_kitchen"),
    ("dish brush", "home_kitchen"),
    ("cleaning brush", "home_kitchen"),
    ("coat hook", "home_kitchen"),
    ("kitchen scissors", "home_kitchen"),
    ("bath mat", "home_kitchen"),
    # Apparel & accessories
    ("t shirt", "apparel_accessories"),
    ("tee shirt", "apparel_accessories"),
    ("hoodie", "apparel_accessories"),
    ("sweatshirt", "apparel_accessories"),
    ("jacket", "apparel_accessories"),
    ("coat", "apparel_accessories"),
    ("winter coat", "apparel_accessories"),
    ("rain coat", "apparel_accessories"),
    ("trench coat", "apparel_accessories"),
    ("gloves", "apparel_accessories"),
    ("hat", "apparel_accessories"),
    ("pants", "apparel_accessories"),
    ("trousers", "apparel_accessories"),
    ("dress", "apparel_accessories"),
    ("skirt", "apparel_accessories"),
    ("socks", "apparel_accessories"),
    ("shoes", "apparel_accessories"),
    ("boots", "apparel_accessories"),
    ("sneakers", "apparel_accessories"),
    ("sandals", "apparel_accessories"),
    ("backpack", "apparel_accessories"),
    ("handbag", "apparel_accessories"),
    ("tote bag", "apparel_accessories"),
    ("crossbody bag", "apparel_accessories"),
    ("shoulder bag", "apparel_accessories"),
    ("wallet", "apparel_accessories"),
    ("card holder", "apparel_accessories"),
    ("sunglasses", "apparel_accessories"),
    ("eyeglasses", "apparel_accessories"),
    ("glasses case", "apparel_accessories"),
    ("scarf", "apparel_accessories"),
    ("silk scarf", "apparel_accessories"),
    ("sun hat", "apparel_accessories"),
    ("bucket hat", "apparel_accessories"),
    ("baseball cap", "apparel_accessories"),
    ("beanie", "apparel_accessories"),
    ("fashion gloves", "apparel_accessories"),
    ("winter gloves", "apparel_accessories"),
    ("leather gloves", "apparel_accessories"),
    ("belt", "apparel_accessories"),
    ("necklace", "apparel_accessories"),
    ("earring", "apparel_accessories"),
    ("bracelet", "apparel_accessories"),
    ("jewelry ring", "apparel_accessories"),
    ("statement ring", "apparel_accessories"),
    ("hair accessory", "apparel_accessories"),
    # Pet supplies
    ("pet toy", "pet_supplies"),
    ("dog toy", "pet_supplies"),
    ("cat toy", "pet_supplies"),
    ("chew toy", "pet_supplies"),
    ("squeaky toy", "pet_supplies"),
    ("dog collar", "pet_supplies"),
    ("cat collar", "pet_supplies"),
    ("pet collar", "pet_supplies"),
    ("dog leash", "pet_supplies"),
    ("cat leash", "pet_supplies"),
    ("pet leash", "pet_supplies"),
    ("leash", "pet_supplies"),
    ("dog harness", "pet_supplies"),
    ("cat harness", "pet_supplies"),
    ("pet harness", "pet_supplies"),
    ("pet clothes", "pet_supplies"),
    ("dog clothes", "pet_supplies"),
    ("cat clothes", "pet_supplies"),
    ("pet bed", "pet_supplies"),
    ("dog bed", "pet_supplies"),
    ("cat bed", "pet_supplies"),
    ("pet mat", "pet_supplies"),
    ("cat scratcher", "pet_supplies"),
    ("scratching post", "pet_supplies"),
    ("pet bowl", "pet_supplies"),
    ("dog bowl", "pet_supplies"),
    ("cat bowl", "pet_supplies"),
    ("pet brush", "pet_supplies"),
    ("dog brush", "pet_supplies"),
    ("cat brush", "pet_supplies"),
    ("grooming brush", "pet_supplies"),
    ("pet nail clipper", "pet_supplies"),
    ("dog nail clipper", "pet_supplies"),
    ("cat nail clipper", "pet_supplies"),
    ("poop bag", "pet_supplies"),
    ("pet carrier", "pet_supplies"),
    ("pet backpack", "pet_supplies"),
    ("dog backpack", "pet_supplies"),
    ("cat backpack", "pet_supplies"),
    ("pet treat pouch", "pet_supplies"),
    ("treat pouch", "pet_supplies"),
    ("pet grooming", "pet_supplies"),
    ("pet hair remover", "pet_supplies"),
    ("litter box", "pet_supplies"),
    # Office & stationery
    ("pen holder", "office_stationery"),
    ("pencil holder", "office_stationery"),
    ("desk organizer", "office_stationery"),
    ("file folder", "office_stationery"),
    ("binder", "office_stationery"),
    ("notebook", "office_stationery"),
    ("journal", "office_stationery"),
    ("sticky note", "office_stationery"),
    ("memo pad", "office_stationery"),
    ("desk shelf", "office_stationery"),
    ("calculator", "office_stationery"),
    ("stapler", "office_stationery"),
    ("tape dispenser", "office_stationery"),
    ("hole punch", "office_stationery"),
    ("scissors", "office_stationery"),
    ("ruler", "office_stationery"),
    ("bookmark", "office_stationery"),
    ("whiteboard", "office_stationery"),
    ("pencil case", "office_stationery"),
    ("pen case", "office_stationery"),
    ("ink cartridge", "office_stationery"),
    ("desk pad", "office_stationery"),
    ("monitor stand", "office_stationery"),
    ("desk stand", "office_stationery"),
    ("document tray", "office_stationery"),
    ("paper tray", "office_stationery"),
    ("clipboard", "office_stationery"),
    ("badge lanyard", "office_stationery"),
    # Sports & outdoor
    ("yoga mat", "sports_outdoor"),
    ("exercise mat", "sports_outdoor"),
    ("gym mat", "sports_outdoor"),
    ("fitness mat", "sports_outdoor"),
    ("resistance band", "sports_outdoor"),
    ("exercise band", "sports_outdoor"),
    ("stretch band", "sports_outdoor"),
    ("jump rope", "sports_outdoor"),
    ("skipping rope", "sports_outdoor"),
    ("gym gloves", "sports_outdoor"),
    ("workout gloves", "sports_outdoor"),
    ("gym shoes", "sports_outdoor"),
    ("wrist wrap", "sports_outdoor"),
    ("knee support", "sports_outdoor"),
    ("knee brace", "sports_outdoor"),
    ("camping hook", "sports_outdoor"),
    ("camping rope", "sports_outdoor"),
    ("paracord", "sports_outdoor"),
    ("portable cutlery", "sports_outdoor"),
    ("camping cutlery", "sports_outdoor"),
    ("sports water bottle", "sports_outdoor"),
    ("sports bottle", "sports_outdoor"),
    ("gym bottle", "sports_outdoor"),
    ("fishing tackle", "sports_outdoor"),
    ("fish hook", "sports_outdoor"),
    ("fishing lure", "sports_outdoor"),
    ("carabiner", "sports_outdoor"),
    ("climbing carabiner", "sports_outdoor"),
    ("gym bag", "sports_outdoor"),
    ("duffel bag", "sports_outdoor"),
    ("yoga block", "sports_outdoor"),
    ("foam roller", "sports_outdoor"),
    ("massage roller", "sports_outdoor"),
    ("hand gripper", "sports_outdoor"),
    ("push up bar", "sports_outdoor"),
    ("ab roller", "sports_outdoor"),
    ("life jacket", "sports_outdoor"),
    # Auto accessories
    ("sunshade", "auto_accessories"),
    ("windshield sunshade", "auto_accessories"),
    ("car sunshade", "auto_accessories"),
    ("car hook", "auto_accessories"),
    ("seat gap organizer", "auto_accessories"),
    ("seat gap filler", "auto_accessories"),
    ("car floor mat", "auto_accessories"),
    ("floor mat", "auto_accessories"),
    ("trunk mat", "auto_accessories"),
    ("cargo mat", "auto_accessories"),
    ("car wash towel", "auto_accessories"),
    ("steering wheel cover", "auto_accessories"),
    ("car trash can", "auto_accessories"),
    ("phone mount", "auto_accessories"),
    ("car phone holder", "auto_accessories"),
    ("car phone mount", "auto_accessories"),
    ("window shade", "auto_accessories"),
    ("car storage bag", "auto_accessories"),
    ("car organizer", "auto_accessories"),
    ("headrest hook", "auto_accessories"),
    ("seat hook", "auto_accessories"),
    ("car cleaning brush", "auto_accessories"),
    ("wheel brush", "auto_accessories"),
    ("tire brush", "auto_accessories"),
    # Hand tools & hardware
    ("screwdriver", "hand_tools"),
    ("screwdriver set", "hand_tools"),
    ("precision screwdriver", "hand_tools"),
    ("wrench", "hand_tools"),
    ("adjustable wrench", "hand_tools"),
    ("combination wrench", "hand_tools"),
    ("tape measure", "hand_tools"),
    ("measuring tape", "hand_tools"),
    ("spirit level", "hand_tools"),
    ("wall hook", "hand_tools"),
    ("wall anchor", "hand_tools"),
    ("work gloves", "hand_tools"),
    ("tool set", "hand_tools"),
    ("hand tool set", "hand_tools"),
    ("pliers", "hand_tools"),
    ("hammer", "hand_tools"),
    ("hand saw", "hand_tools"),
    ("utility knife", "hand_tools"),
    ("hex key", "hand_tools"),
    ("allen wrench", "hand_tools"),
    ("socket set", "hand_tools"),
    ("zip tie", "hand_tools"),
    ("cable tie", "hand_tools"),
    ("clamp", "hand_tools"),
    ("vise", "hand_tools"),
    ("chisel", "hand_tools"),
    ("file", "hand_tools"),
    ("rasp", "hand_tools"),
    ("hacksaw", "hand_tools"),
    ("putty knife", "hand_tools"),
    ("scraper", "hand_tools"),
    ("crowbar", "hand_tools"),
    ("pry bar", "hand_tools"),
    # Passive tech accessories
    ("phone case", "passive_tech"),
    ("tablet case", "passive_tech"),
    ("ipad case", "passive_tech"),
    ("earbuds case", "passive_tech"),
    ("airpods case", "passive_tech"),
    ("laptop sleeve", "passive_tech"),
    ("tablet sleeve", "passive_tech"),
    ("macbook sleeve", "passive_tech"),
    ("cable organizer", "passive_tech"),
    ("cable pouch", "passive_tech"),
    ("cable management", "passive_tech"),
    ("cord organizer", "passive_tech"),
    ("keyboard cover", "passive_tech"),
    ("keyboard skin", "passive_tech"),
    ("screen cleaner", "passive_tech"),
    ("screen cleaning cloth", "passive_tech"),
    ("phone lanyard", "passive_tech"),
    ("phone strap", "passive_tech"),
    ("phone ring", "passive_tech"),
    ("phone grip", "passive_tech"),
    ("phone wallet", "passive_tech"),
    ("laptop backpack", "passive_tech"),
    ("phone card holder", "passive_tech"),
    ("camera bag", "passive_tech"),
    ("camera pouch", "passive_tech"),
    ("mouse pad", "passive_tech"),
    ("laptop stand", "passive_tech"),
    ("tablet stand", "passive_tech"),
    ("phone stand", "passive_tech"),
    ("desk phone stand", "passive_tech"),
    # Beauty & personal-care tools
    ("makeup brush", "beauty_tools"),
    ("cosmetic brush", "beauty_tools"),
    ("foundation brush", "beauty_tools"),
    ("eyeshadow brush", "beauty_tools"),
    ("blush brush", "beauty_tools"),
    ("concealer brush", "beauty_tools"),
    ("powder puff", "beauty_tools"),
    ("beauty blender", "beauty_tools"),
    ("makeup sponge", "beauty_tools"),
    ("face towel", "beauty_tools"),
    ("cleansing cloth", "beauty_tools"),
    ("nail file", "beauty_tools"),
    ("glass nail file", "beauty_tools"),
    ("nail buffer", "beauty_tools"),
    ("cuticle pusher", "beauty_tools"),
    ("eyebrow razor", "beauty_tools"),
    ("dermaplaning razor", "beauty_tools"),
    ("hair roller", "beauty_tools"),
    ("curling roller", "beauty_tools"),
    ("hair brush", "beauty_tools"),
    ("hair clip", "beauty_tools"),
    ("claw clip", "beauty_tools"),
    ("makeup bag", "beauty_tools"),
    ("cosmetic bag", "beauty_tools"),
    ("eyelash curler", "beauty_tools"),
    ("eyebrow tweezers", "beauty_tools"),
    ("manual razor", "beauty_tools"),
    ("safety razor", "beauty_tools"),
    ("manual toothbrush", "beauty_tools"),
    ("dental floss", "beauty_tools"),
    ("tongue scraper", "beauty_tools"),
    ("shower cap", "beauty_tools"),
    ("hair towel", "beauty_tools"),
    ("hair scissors", "beauty_tools"),
    # Adult games & puzzles
    ("board game", "adult_games"),
    ("card game", "adult_games"),
    ("playing cards", "adult_games"),
    ("poker cards", "adult_games"),
    ("deck of cards", "adult_games"),
    ("jigsaw puzzle", "adult_games"),
    ("adult puzzle", "adult_games"),
    ("3d puzzle", "adult_games"),
    ("dice set", "adult_games"),
    ("chess set", "adult_games"),
    ("checkers", "adult_games"),
    ("tarot cards", "adult_games"),
    ("fidget toy", "adult_games"),
    ("fidget spinner", "adult_games"),
    ("stress ball", "adult_games"),
    ("squeeze toy", "adult_games"),
    ("brain teaser", "adult_games"),
    ("party game", "adult_games"),
    ("trivia game", "adult_games"),
    ("strategy game", "adult_games"),
    # Garden & patio tools
    ("plant pot", "garden_tools"),
    ("flower pot", "garden_tools"),
    ("terracotta pot", "garden_tools"),
    ("ceramic pot", "garden_tools"),
    ("plastic planter", "garden_tools"),
    ("garden trowel", "garden_tools"),
    ("hand trowel", "garden_tools"),
    ("pruning shears", "garden_tools"),
    ("pruning scissors", "garden_tools"),
    ("garden scissors", "garden_tools"),
    ("seed starter tray", "garden_tools"),
    ("seed tray", "garden_tools"),
    ("germination tray", "garden_tools"),
    ("garden fence", "garden_tools"),
    ("small garden fence", "garden_tools"),
    ("watering can", "garden_tools"),
    ("garden gloves", "garden_tools"),
    ("cultivator", "garden_tools"),
    ("hand cultivator", "garden_tools"),
    ("garden spray bottle", "garden_tools"),
    ("plant stand", "garden_tools"),
    ("small plant stand", "garden_tools"),
    ("plant saucer", "garden_tools"),
    ("potting mat", "garden_tools"),
    ("soil scoop", "garden_tools"),
    ("bulb planter", "garden_tools"),
    ("garden weeder", "garden_tools"),
    ("garden fork", "garden_tools"),
    ("hand rake", "garden_tools"),
    ("garden twine", "garden_tools"),
    ("plant label", "garden_tools"),
    ("seedling pot", "garden_tools"),
)

# ---------------------------------------------------------------------------
# 中文标题关键词：1688 来源标题基本都是中文，而上面的英文规则一条都命中不了，
# 结果每条链接都落进 general 兜底，品类场景自然千篇一律。这里补一份中文强规则，
# 只放 2 字以上的具体名词，避免「包」「杯」「灯」这类单字误伤（例如「包装」「包邮」）。
# ---------------------------------------------------------------------------
_RAW_ZH_STRONG_RULES: tuple[tuple[str, str], ...] = (
    # 家居与厨房
    ("收纳盒", "home_kitchen"),
    ("收纳架", "home_kitchen"),
    ("置物架", "home_kitchen"),
    ("收纳袋", "home_kitchen"),
    ("保鲜盒", "home_kitchen"),
    ("保温杯", "home_kitchen"),
    ("马克杯", "home_kitchen"),
    ("咖啡杯", "home_kitchen"),
    ("水杯", "home_kitchen"),
    ("茶杯", "home_kitchen"),
    ("餐具", "home_kitchen"),
    ("碗碟", "home_kitchen"),
    ("筷子", "home_kitchen"),
    ("砧板", "home_kitchen"),
    ("菜板", "home_kitchen"),
    ("锅具", "home_kitchen"),
    ("炒锅", "home_kitchen"),
    ("厨房", "home_kitchen"),
    ("四件套", "home_kitchen"),
    ("床单", "home_kitchen"),
    ("被套", "home_kitchen"),
    ("枕头", "home_kitchen"),
    ("抱枕", "home_kitchen"),
    ("靠垫", "home_kitchen"),
    ("地毯", "home_kitchen"),
    ("地垫", "home_kitchen"),
    ("桌布", "home_kitchen"),
    ("窗帘", "home_kitchen"),
    ("衣架", "home_kitchen"),
    ("垃圾桶", "home_kitchen"),
    ("香薰", "home_kitchen"),
    ("台灯", "home_kitchen"),
    ("调味罐", "home_kitchen"),
    # 服装与配饰（钥匙扣/挂件这一类必须命中，否则只能吃 general）
    ("钥匙扣", "apparel_accessories"),
    ("钥匙链", "apparel_accessories"),
    ("钥匙圈", "apparel_accessories"),
    ("钥匙挂饰", "apparel_accessories"),
    ("包挂", "apparel_accessories"),
    ("挂件", "apparel_accessories"),
    ("挂饰", "apparel_accessories"),
    ("项链", "apparel_accessories"),
    ("耳环", "apparel_accessories"),
    ("耳钉", "apparel_accessories"),
    ("手链", "apparel_accessories"),
    ("手镯", "apparel_accessories"),
    ("戒指", "apparel_accessories"),
    ("发夹", "apparel_accessories"),
    ("发饰", "apparel_accessories"),
    ("头饰", "apparel_accessories"),
    ("帽子", "apparel_accessories"),
    ("围巾", "apparel_accessories"),
    ("腰带", "apparel_accessories"),
    ("皮带", "apparel_accessories"),
    ("双肩包", "apparel_accessories"),
    ("书包", "apparel_accessories"),
    ("背包", "apparel_accessories"),
    ("手提包", "apparel_accessories"),
    ("斜挎包", "apparel_accessories"),
    ("钱包", "apparel_accessories"),
    ("卡包", "apparel_accessories"),
    ("墨镜", "apparel_accessories"),
    ("眼镜", "apparel_accessories"),
    ("手套", "apparel_accessories"),
    ("袜子", "apparel_accessories"),
    ("内衣", "apparel_accessories"),
    ("文胸", "apparel_accessories"),
    ("泳衣", "apparel_accessories"),
    ("睡衣", "apparel_accessories"),
    ("t恤", "apparel_accessories"),
    ("衬衫", "apparel_accessories"),
    ("卫衣", "apparel_accessories"),
    ("外套", "apparel_accessories"),
    ("羽绒服", "apparel_accessories"),
    ("连衣裙", "apparel_accessories"),
    ("半身裙", "apparel_accessories"),
    ("牛仔裤", "apparel_accessories"),
    ("运动鞋", "apparel_accessories"),
    ("拖鞋", "apparel_accessories"),
    ("凉鞋", "apparel_accessories"),
    ("靴子", "apparel_accessories"),
    ("高跟鞋", "apparel_accessories"),
    # 宠物
    ("宠物", "pet_supplies"),
    ("猫窝", "pet_supplies"),
    ("狗窝", "pet_supplies"),
    ("猫抓板", "pet_supplies"),
    ("牵引绳", "pet_supplies"),
    ("狗绳", "pet_supplies"),
    ("猫砂", "pet_supplies"),
    ("宠物碗", "pet_supplies"),
    ("宠物垫", "pet_supplies"),
    ("宠物玩具", "pet_supplies"),
    # 办公与文具
    ("笔筒", "office_stationery"),
    ("笔袋", "office_stationery"),
    ("文具盒", "office_stationery"),
    ("文具袋", "office_stationery"),
    ("文件夹", "office_stationery"),
    ("资料册", "office_stationery"),
    ("订书机", "office_stationery"),
    ("便利贴", "office_stationery"),
    ("马克笔", "office_stationery"),
    ("中性笔", "office_stationery"),
    ("铅笔", "office_stationery"),
    ("橡皮", "office_stationery"),
    ("削笔器", "office_stationery"),
    ("桌面收纳", "office_stationery"),
    # 运动户外
    ("瑜伽", "sports_outdoor"),
    ("健身", "sports_outdoor"),
    ("哑铃", "sports_outdoor"),
    ("跳绳", "sports_outdoor"),
    ("露营", "sports_outdoor"),
    ("帐篷", "sports_outdoor"),
    ("登山", "sports_outdoor"),
    ("骑行", "sports_outdoor"),
    ("泳镜", "sports_outdoor"),
    ("羽毛球", "sports_outdoor"),
    ("乒乓球", "sports_outdoor"),
    ("护膝", "sports_outdoor"),
    ("运动水壶", "sports_outdoor"),
    # 汽车用品
    ("车载", "auto_accessories"),
    ("车用", "auto_accessories"),
    ("行车记录仪", "auto_accessories"),
    ("方向盘套", "auto_accessories"),
    ("汽车脚垫", "auto_accessories"),
    ("车贴", "auto_accessories"),
    # 工具与五金
    ("螺丝刀", "hand_tools"),
    ("扳手", "hand_tools"),
    ("钳子", "hand_tools"),
    ("锤子", "hand_tools"),
    ("电钻", "hand_tools"),
    ("钻头", "hand_tools"),
    ("卷尺", "hand_tools"),
    ("工具箱", "hand_tools"),
    ("五金", "hand_tools"),
    # 纯物理配件
    ("手机壳", "passive_tech"),
    ("手机支架", "passive_tech"),
    ("数据线", "passive_tech"),
    ("充电线", "passive_tech"),
    ("充电器", "passive_tech"),
    ("转接头", "passive_tech"),
    ("读卡器", "passive_tech"),
    ("鼠标垫", "passive_tech"),
    ("键盘膜", "passive_tech"),
    ("平板支架", "passive_tech"),
    # 美妆个护
    ("化妆刷", "beauty_tools"),
    ("美妆蛋", "beauty_tools"),
    ("面膜", "beauty_tools"),
    ("美甲", "beauty_tools"),
    ("指甲刀", "beauty_tools"),
    ("卷发棒", "beauty_tools"),
    ("直发器", "beauty_tools"),
    ("剃须刀", "beauty_tools"),
    ("洗脸巾", "beauty_tools"),
    ("口红", "beauty_tools"),
    # 成人玩具
    ("成人用品", "adult_games"),
    ("情趣", "adult_games"),
    ("按摩棒", "adult_games"),
    # 园艺庭院
    ("园艺", "garden_tools"),
    ("花盆", "garden_tools"),
    ("花架", "garden_tools"),
    ("洒水壶", "garden_tools"),
    ("喷壶", "garden_tools"),
    ("育苗", "garden_tools"),
)

# ---------------------------------------------------------------------------
# Context words: domain indicators.  Weight 3 each.
# ---------------------------------------------------------------------------

_RAW_CONTEXT_RULES: tuple[tuple[str, str], ...] = (
    ("kitchen", "home_kitchen"),
    ("cookware", "home_kitchen"),
    ("dining", "home_kitchen"),
    ("dinnerware", "home_kitchen"),
    ("bedding", "home_kitchen"),
    ("home decor", "home_kitchen"),
    ("closet", "home_kitchen"),
    ("wardrobe", "home_kitchen"),
    ("curtain", "home_kitchen"),
    ("tablecloth", "home_kitchen"),
    ("placemat", "home_kitchen"),
    ("men", "apparel_accessories"),
    ("women", "apparel_accessories"),
    ("unisex", "apparel_accessories"),
    ("apparel", "apparel_accessories"),
    ("clothing", "apparel_accessories"),
    ("fashion", "apparel_accessories"),
    ("jewelry", "apparel_accessories"),
    ("pet", "pet_supplies"),
    ("dog", "pet_supplies"),
    ("cat", "pet_supplies"),
    ("puppy", "pet_supplies"),
    ("kitten", "pet_supplies"),
    ("desk", "office_stationery"),
    ("office", "office_stationery"),
    ("stationery", "office_stationery"),
    ("study", "office_stationery"),
    ("yoga", "sports_outdoor"),
    ("sports", "sports_outdoor"),
    ("gym", "sports_outdoor"),
    ("fitness", "sports_outdoor"),
    ("workout", "sports_outdoor"),
    ("camping", "sports_outdoor"),
    ("hiking", "sports_outdoor"),
    ("fishing", "sports_outdoor"),
    ("outdoor", "sports_outdoor"),
    ("car", "auto_accessories"),
    ("auto", "auto_accessories"),
    ("automotive", "auto_accessories"),
    ("vehicle", "auto_accessories"),
    ("truck", "auto_accessories"),
    ("dashboard", "auto_accessories"),
    ("trunk", "auto_accessories"),
    ("seat", "auto_accessories"),
    ("tool", "hand_tools"),
    ("hardware", "hand_tools"),
    ("workshop", "hand_tools"),
    ("diy", "hand_tools"),
    ("phone", "passive_tech"),
    ("smartphone", "passive_tech"),
    ("tablet", "passive_tech"),
    ("laptop", "passive_tech"),
    ("keyboard", "passive_tech"),
    ("cable", "passive_tech"),
    ("makeup", "beauty_tools"),
    ("cosmetic", "beauty_tools"),
    ("beauty", "beauty_tools"),
    ("nail", "beauty_tools"),
    ("hair", "beauty_tools"),
    ("game", "adult_games"),
    ("puzzle", "adult_games"),
    ("dice", "adult_games"),
    ("chess", "adult_games"),
    ("garden", "garden_tools"),
    ("plant", "garden_tools"),
    ("gardening", "garden_tools"),
    ("planter", "garden_tools"),
    ("soil", "garden_tools"),
    ("seed", "garden_tools"),
)

# ---------------------------------------------------------------------------
# Unambiguous product nouns.  Weight 2 each.
# ---------------------------------------------------------------------------

_RAW_NOUN_RULES: tuple[tuple[str, str], ...] = (
    ("bakeware", "home_kitchen"),
    ("cookware", "home_kitchen"),
    ("dinnerware", "home_kitchen"),
    ("bedding", "home_kitchen"),
    ("duvet", "home_kitchen"),
    ("pillowcase", "home_kitchen"),
    ("tablecloth", "home_kitchen"),
    ("vase", "home_kitchen"),
    ("mug", "home_kitchen"),
    ("teapot", "home_kitchen"),
    ("kettle", "home_kitchen"),
    ("hoodie", "apparel_accessories"),
    ("sweatshirt", "apparel_accessories"),
    ("jacket", "apparel_accessories"),
    ("dress", "apparel_accessories"),
    ("skirt", "apparel_accessories"),
    ("socks", "apparel_accessories"),
    ("shoes", "apparel_accessories"),
    ("boots", "apparel_accessories"),
    ("sneakers", "apparel_accessories"),
    ("sandals", "apparel_accessories"),
    ("backpack", "apparel_accessories"),
    ("handbag", "apparel_accessories"),
    ("wallet", "apparel_accessories"),
    ("sunglasses", "apparel_accessories"),
    ("scarf", "apparel_accessories"),
    ("beanie", "apparel_accessories"),
    ("necklace", "apparel_accessories"),
    ("earring", "apparel_accessories"),
    ("bracelet", "apparel_accessories"),
    ("leash", "pet_supplies"),
    ("harness", "pet_supplies"),
    ("scratcher", "pet_supplies"),
    ("notebook", "office_stationery"),
    ("folder", "office_stationery"),
    ("binder", "office_stationery"),
    ("stapler", "office_stationery"),
    ("calculator", "office_stationery"),
    ("scissors", "office_stationery"),
    ("ruler", "office_stationery"),
    ("whiteboard", "office_stationery"),
    ("bookmark", "office_stationery"),
    ("clipboard", "office_stationery"),
    ("carabiner", "sports_outdoor"),
    ("paracord", "sports_outdoor"),
    ("sunshade", "auto_accessories"),
    ("screwdriver", "hand_tools"),
    ("wrench", "hand_tools"),
    ("pliers", "hand_tools"),
    ("hammer", "hand_tools"),
    ("chisel", "hand_tools"),
    ("clamp", "hand_tools"),
    ("vise", "hand_tools"),
    ("crowbar", "hand_tools"),
    ("mousepad", "passive_tech"),
    ("comb", "beauty_tools"),
    ("tweezers", "beauty_tools"),
    ("pumice", "beauty_tools"),
    ("loofah", "beauty_tools"),
    ("dice", "adult_games"),
    ("chess", "adult_games"),
    ("puzzle", "adult_games"),
    ("planter", "garden_tools"),
    ("trowel", "garden_tools"),
    ("pruner", "garden_tools"),
    ("cultivator", "garden_tools"),
    ("weeder", "garden_tools"),
)

# ---------------------------------------------------------------------------
# Exclusions: products the user explicitly wants out of this library.
# ---------------------------------------------------------------------------

_RAW_EXCLUDED_RULES: tuple[tuple[str, str], ...] = (
    ("coffee machine", "大家电/带电厨房"),
    ("espresso machine", "大家电/带电厨房"),
    ("electric oven", "大家电/带电厨房"),
    ("convection oven", "大家电/带电厨房"),
    ("toaster oven", "大家电/带电厨房"),
    ("air fryer", "大家电/带电厨房"),
    ("rice cooker", "大家电/带电厨房"),
    ("microwave", "大家电/带电厨房"),
    ("refrigerator", "大家电/带电厨房"),
    ("washing machine", "大家电/带电厨房"),
    ("dishwasher", "大家电/带电厨房"),
    ("electric kettle", "大家电/带电厨房"),
    ("power bank", "带电/电池产品"),
    ("bluetooth speaker", "带电/电池产品"),
    ("wireless speaker", "带电/电池产品"),
    ("charger", "带电/电池产品"),
    ("charging cable", "带电/电池产品"),
    ("usb cable", "带电/电池产品"),
    ("data cable", "带电/电池产品"),
    ("electric toothbrush", "带电/电池产品"),
    ("curling iron", "带电/电池产品"),
    ("hair dryer", "带电/电池产品"),
    ("drill", "带电/电池产品"),
    ("power saw", "带电/电池产品"),
    ("circular saw", "带电/电池产品"),
    ("electric saw", "带电/电池产品"),
    ("dash cam", "带电/电池产品"),
    ("car charger", "带电/电池产品"),
    ("tpms", "带电/电池产品"),
    ("gps", "带电/电池产品"),
    ("smartwatch", "带电/电池产品"),
    ("drone", "带电/电池产品"),
    ("dog food", "食品/宠物食品"),
    ("cat food", "食品/宠物食品"),
    ("pet food", "食品/宠物食品"),
    ("dog treat", "食品/宠物食品"),
    ("cat treat", "食品/宠物食品"),
    ("pet treat", "食品/宠物食品"),
    ("kids toy", "母婴/儿童"),
    ("baby toy", "母婴/儿童"),
    ("children toy", "母婴/儿童"),
    ("building block", "母婴/儿童"),
    ("plush toy", "母婴/儿童"),
    ("tent", "大件/户外家具"),
    ("folding chair", "大件/户外家具"),
    ("treadmill", "大件/户外家具"),
    ("large trellis", "大件/户外家具"),
    ("outdoor furniture", "大件/户外家具"),
    ("lawn mower", "大件/户外家具"),
    ("large tool box", "大件/户外家具"),
    ("magnetic", "磁吸/磁性"),
    ("magsafe", "磁吸/磁性"),
    ("solid gold", "贵金属/医疗宣称"),
    ("pure gold", "贵金属/医疗宣称"),
    ("24k gold", "贵金属/医疗宣称"),
    ("platinum jewelry", "贵金属/医疗宣称"),
    ("medical grade", "贵金属/医疗宣称"),
    ("medical-grade", "贵金属/医疗宣称"),
    ("acne treatment", "贵金属/医疗宣称"),
    ("medical treatment", "贵金属/医疗宣称"),
    ("sterile", "贵金属/医疗宣称"),
    ("pesticide", "化学/危险品"),
    ("insecticide", "化学/危险品"),
    ("fertilizer", "化学/危险品"),
    ("flammable", "化学/危险品"),
    ("strong acid", "化学/危险品"),
    ("strong alkali", "化学/危险品"),
    ("skincare", "美妆仅限工具类"),
    ("serum", "美妆仅限工具类"),
    ("moisturizer", "美妆仅限工具类"),
    ("lipstick", "美妆仅限工具类"),
    ("mascara", "美妆仅限工具类"),
    ("foundation", "美妆仅限工具类"),
    ("eyeshadow", "美妆仅限工具类"),
    ("blush", "美妆仅限工具类"),
    ("concealer", "美妆仅限工具类"),
    ("nail polish", "美妆仅限工具类"),
    ("护肤", "美妆仅限工具类"),
    ("精华", "美妆仅限工具类"),
    ("面霜", "美妆仅限工具类"),
    ("口红", "美妆仅限工具类"),
    ("睫毛膏", "美妆仅限工具类"),
    ("粉底", "美妆仅限工具类"),
    ("眼影", "美妆仅限工具类"),
    ("腮红", "美妆仅限工具类"),
    ("遮瑕", "美妆仅限工具类"),
    ("指甲油", "美妆仅限工具类"),
    ("零食", "食品/宠物食品"),
    ("糖果", "食品/宠物食品"),
    ("巧克力", "食品/宠物食品"),
    ("咖啡豆", "食品/宠物食品"),
    ("茶叶", "食品/宠物食品"),
    ("维生素", "食品/宠物食品"),
    ("保健品", "食品/宠物食品"),
)

_RAW_EXCLUSION_EXCEPTIONS: dict[str, tuple[str, ...]] = {
    "charger": ("charger organizer", "charger pouch", "充电器收纳"),
    "data cable": ("data cable organizer", "data cable pouch", "数据线收纳"),
    "foundation": ("foundation brush", "粉底刷"),
    "eyeshadow": ("eyeshadow brush", "眼影刷"),
    "blush": ("blush brush", "腮红刷"),
    "concealer": ("concealer brush", "遮瑕刷"),
}

# Pre-normalize every rule once at import time.  Matching then only compares
# already-normalized strings, which keeps batch classification fast.
_STRONG_RULES = tuple(
    (normalize_title(phrase), category)
    for phrase, category in (*_RAW_STRONG_RULES, *_RAW_ZH_STRONG_RULES)
)
_CONTEXT_RULES = tuple((normalize_title(phrase), category) for phrase, category in _RAW_CONTEXT_RULES)
_NOUN_RULES = tuple((normalize_title(phrase), category) for phrase, category in _RAW_NOUN_RULES)
_EXCLUDED_RULES = tuple((normalize_title(phrase), reason) for phrase, reason in _RAW_EXCLUDED_RULES)
_EXCLUSION_EXCEPTIONS = {
    normalize_title(phrase): tuple(normalize_title(item) for item in exceptions)
    for phrase, exceptions in _RAW_EXCLUSION_EXCEPTIONS.items()
}


def _general_decision(
    *,
    confidence: float,
    score: int,
    runner_up: str = "",
    runner_up_score: int = 0,
    matched: tuple[str, ...] = (),
) -> CategoryDecision:
    return CategoryDecision(
        category_id="general",
        label=CATEGORY_LABELS["general"],
        confidence=confidence,
        score=score,
        runner_up=runner_up,
        runner_up_score=runner_up_score,
        matched=matched,
    )


def _exclusion_reason(normalized: str) -> str:
    for phrase, reason in _EXCLUDED_RULES:
        if not _has_phrase(normalized, phrase):
            continue
        exceptions = _EXCLUSION_EXCEPTIONS.get(phrase, ())
        if any(_has_phrase(normalized, exception) for exception in exceptions):
            continue
        return reason
    return ""


def _score_rules(
    normalized: str,
    rules: tuple[tuple[str, str], ...],
    weight: int,
    scores: dict[str, int],
    matched: dict[str, list[str]],
) -> None:
    for phrase, category in rules:
        if _has_phrase(normalized, phrase):
            # Longer phrases are more specific than single generic nouns.
            bonus = min(2, max(0, len(phrase.split()) - 1))
            scores[category] += weight + bonus
            matched[category].append(phrase)


def classify_title(title: str) -> CategoryDecision:
    """Classify one title.  Pure function, safe to call from any thread."""

    normalized = normalize_title(title)
    if not normalized:
        return _general_decision(confidence=0.0, score=EXCLUDED_SCORE)

    exclusion_reason = _exclusion_reason(normalized)
    if exclusion_reason:
        return CategoryDecision(
            category_id="general",
            label=CATEGORY_LABELS["general"],
            confidence=1.0,
            score=EXCLUDED_SCORE,
            excluded=True,
            exclusion_reason=exclusion_reason,
        )

    scores: dict[str, int] = defaultdict(int)
    matched: dict[str, list[str]] = defaultdict(list)
    _score_rules(normalized, _STRONG_RULES, STRONG_WEIGHT, scores, matched)
    _score_rules(normalized, _CONTEXT_RULES, CONTEXT_WEIGHT, scores, matched)
    _score_rules(normalized, _NOUN_RULES, NOUN_WEIGHT, scores, matched)

    if not scores:
        return _general_decision(confidence=0.2, score=0)

    ranked = sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            -max((len(phrase) for phrase in matched[item[0]]), default=0),
            item[0],
        ),
    )
    best_id, best_score = ranked[0]
    second_id, second_score = ranked[1] if len(ranked) > 1 else ("", 0)
    margin = best_score - second_score
    best_longest = max((len(phrase) for phrase in matched[best_id]), default=0)
    second_longest = max((len(phrase) for phrase in matched[second_id]), default=0) if second_id else 0

    # Require a clear winner.  A one-point lead is still allowed when the best
    # category matched a clearly longer, more specific phrase (for example
    # "work gloves" vs the generic apparel noun "gloves").  Ambiguous titles
    # intentionally fall back to General so the caller can keep the default or
    # ask for a manual review.
    specificity_win = (
        margin == 1
        and best_score >= 5
        and best_longest >= second_longest + 2
    )
    if best_score < 4 or (margin < 2 and not specificity_win):
        return _general_decision(
            confidence=0.3,
            score=best_score,
            runner_up=best_id,
            runner_up_score=best_score,
            matched=tuple(dict.fromkeys(matched[best_id])),
        )

    if specificity_win:
        confidence = 0.7
    elif best_score >= 8 and margin >= 4:
        confidence = 0.95
    elif best_score >= 6 and margin >= 3:
        confidence = 0.9
    else:
        confidence = 0.8

    return CategoryDecision(
        category_id=best_id,
        label=CATEGORY_LABELS[best_id],
        confidence=confidence,
        score=best_score,
        runner_up=second_id,
        runner_up_score=second_score,
        matched=tuple(dict.fromkeys(matched[best_id])),
    )


def classify_titles(titles: Iterable[str]) -> dict[str, CategoryDecision]:
    """Classify a batch of titles, de-duplicating identical normalized titles."""

    cache: dict[str, CategoryDecision] = {}
    result: dict[str, CategoryDecision] = {}
    for title in titles:
        key = normalize_title(title)
        decision = cache.get(key)
        if decision is None:
            decision = classify_title(title)
            cache[key] = decision
        result[title] = decision
    return result
