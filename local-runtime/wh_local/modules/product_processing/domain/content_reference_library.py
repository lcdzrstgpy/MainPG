"""Category-aware content references for product titles and generated images.

This module is deliberately separate from category resolution.  It consumes the
category and attributes already present on a product draft, returns a bounded
content-only hint, and never mutates the input or performs I/O/provider calls.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class CategoryProfile:
    profile_id: str
    aliases: tuple[str, ...]
    title_priorities: tuple[str, ...]
    visual_focus: str
    scene_roles: tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class AttributeModule:
    module_id: str
    aliases: tuple[str, ...]
    title_note: str
    image_note: str


@dataclass(frozen=True, slots=True)
class ContentReference:
    kind: str
    profile_id: str
    variant_id: str
    text: str

    @property
    def reference_id(self) -> str:
        return f"{self.profile_id}/{self.variant_id}"


TITLE_ARRANGEMENTS: tuple[str, ...] = (
    "Lead with the exact product type, then the strongest verified differentiator, then supporting size, count, or use facts.",
    "Lead with the exact product type and verified construction, then size or capacity, then a supported use context.",
    "Lead with the exact product type, then verified form or style, then compatibility or intended use, then real quantity.",
    "Lead with the exact product type and the clearest verified selection attribute, then secondary physical facts.",
    "Lead with the exact product type, then a verified performance-relevant feature, then a physical specification.",
    "Lead with the exact product type, then verified material or finish, then form factor and real pack contents.",
    "Lead with the exact product type and model or fit when present, then verified function and size or count.",
    "Lead with the exact product type, then two complementary verified attributes, ending with a supported use context.",
)


VISUAL_TREATMENTS: tuple[tuple[str, str, str], ...] = (
    ("clean catalog realism", "soft diffused studio light", "quiet neutral surface"),
    ("bright everyday realism", "large natural window light", "light lived-in setting"),
    ("material-led close realism", "soft side light with controlled highlights", "simple tactile surface"),
    ("functional demonstration realism", "even directional light", "credible use environment"),
    ("refined minimal editorial realism", "gentle key and rim light", "muted tonal background"),
    ("fresh airy commercial realism", "high-key diffused light", "subtle category-relevant background"),
    ("warm practical lifestyle realism", "warm natural side light", "uncluttered home or work context"),
    ("precise detail-forward realism", "controlled macro-friendly light", "plain contrasting surface"),
)


_SCENE_ORDERS: tuple[tuple[int, int, int, int], ...] = (
    (0, 1, 2, 3),
    (0, 2, 1, 3),
    (1, 0, 3, 2),
    (2, 0, 1, 3),
)


ATTRIBUTE_MODULES: tuple[AttributeModule, ...] = (
    AttributeModule(
        "material",
        ("material", "fabric", "composition", "材质", "面料", "成分"),
        "Place a verified material or construction fact near the product type when it helps distinguish the item.",
        "Let the lighting reveal the verified surface, weave, grain, gloss, or finish without changing it.",
    ),
    AttributeModule(
        "size",
        ("size", "dimension", "length", "width", "height", "diameter", "尺寸", "长", "宽", "高", "直径"),
        "Use a verified size or fit fact as a shopper selection cue, keeping its original unit and meaning.",
        "Use honest scale context or an angle that makes the verified dimensions visually understandable.",
    ),
    AttributeModule(
        "capacity",
        ("capacity", "volume", "容量", "容积"),
        "Use verified capacity after the product type when capacity is a primary purchase decision.",
        "Show the product's real capacity through its form or a credible use context without inventing contents.",
    ),
    AttributeModule(
        "quantity",
        ("quantity", "count", "pieces", "piece count", "pack", "set", "数量", "件数", "套装", "包装数量"),
        "State the real piece or pack count once when the source explicitly supports it.",
        "Keep the source-supported item count clear and consistent in every scene.",
    ),
    AttributeModule(
        "compatibility",
        ("compatible", "compatibility", "model", "fit", "适用", "兼容", "型号"),
        "Keep verified model, fit, or compatibility information close to the exact product type.",
        "Use a credible compatibility context only when the matching device or object is source-supported.",
    ),
    AttributeModule(
        "color-pattern",
        ("color", "colour", "pattern", "print", "finish", "颜色", "色系", "图案", "印花", "表面处理"),
        "Use the verified color, pattern, or finish only when it distinguishes the selected variant.",
        "Preserve the exact source color and pattern while choosing a background that keeps them legible.",
    ),
    AttributeModule(
        "construction",
        ("closure", "fastener", "mount", "handle", "shape", "结构", "闭合", "扣", "安装", "手柄", "形状"),
        "Surface one verified construction, closure, mounting, or shape detail when it defines how the item is chosen.",
        "Include a clear detail view of the verified closure, joint, handle, mount, or shape feature.",
    ),
    AttributeModule(
        "power-control",
        ("power", "voltage", "watt", "battery", "control", "功率", "电压", "电池", "控制方式"),
        "Use verified power or control information only when it is essential to product selection.",
        "Show real controls, ports, or power-related hardware clearly without adding indicators or interfaces.",
    ),
    AttributeModule(
        "care",
        ("care", "wash", "cleaning", "maintenance", "护理", "清洗", "保养"),
        "Include a verified care-related differentiator only when the source explicitly states it.",
        "Use a clean maintenance context only when supported; do not invent tools, steps, or durability claims.",
    ),
    AttributeModule(
        "variant",
        ("variant", "style", "规格", "款式", "变体"),
        "Keep the selected variant's verified style or specification distinct without listing unavailable options.",
        "Represent only the selected variant and preserve its exact visible specification.",
    ),
)


_PROFILE_ROWS: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...], str, tuple[str, str, str, str]], ...
] = (
    (
        "musical-tools-accessories",
        ("musical instruments", "instrument accessories", "music accessories", "乐器", "乐器配件", "音乐器材"),
        ("exact instrument or accessory type", "verified instrument fit or tuning", "real material or mechanism", "verified size or included parts"),
        "Preserve the instrument or accessory geometry, strings, keys, joints, connectors, finish, and included pieces.",
        ("complete product hero", "working-part or connector detail", "credible practice or performance context", "fit, scale, or included-parts view"),
    ),
    (
        "tools-hardware",
        ("tools hardware", "tools & hardware", "hand tools", "hardware accessories", "工具五金", "手工具", "五金配件"),
        ("exact tool or hardware type", "verified working end or mechanism", "real material or dimensions", "supported task or fit"),
        "Show the real working geometry, grip, fasteners, connection points, finish, and included pieces.",
        ("complete tool or hardware hero", "working-end or mechanism detail", "credible task context", "size, set, or compatibility view"),
    ),
    (
        "home-storage-organization",
        ("home storage", "storage & organization", "storage and organization", "storage organizer", "家居收纳", "收纳整理", "收纳用品"),
        ("exact organizer or container type", "verified compartment or closure", "real dimensions or capacity", "verified material or set count"),
        "Keep the organizer's real shape, compartments, handles, closure, surface, capacity cues, and selected variant.",
        ("complete organizer hero", "compartment, handle, or closure detail", "credible shelf, drawer, or room context", "capacity, folded, or scale view"),
    ),
    (
        "table-linen",
        ("table linen", "tablecloth", "table runner", "placemat", "桌布", "桌旗", "餐垫", "餐桌布艺"),
        ("exact table-linen type", "verified shape or dimensions", "real fabric, weave, or edge finish", "verified pattern or piece count"),
        "Preserve the textile's exact shape, drape, pattern, weave, edge treatment, and source-supported count.",
        ("complete spread view", "weave, print, or edge detail", "credible dining-table context", "folded, size, or set-content view"),
    ),
    (
        "soft-home-textile",
        ("home textile", "soft furnishings", "cushion cover", "throw blanket", "curtain", "家纺", "抱枕套", "盖毯", "窗帘"),
        ("exact home-textile type", "verified dimensions or fit", "real fabric or weave", "verified pattern, closure, or set count"),
        "Keep the textile's real drape, weave, pattern, seams, closure, color, and selected variant accurate.",
        ("complete textile hero", "weave, seam, or closure detail", "credible sofa, bed, or window context", "folded, reverse, or scale view"),
    ),
    (
        "lighting-electrical",
        ("lighting electrical", "lighting & electrical", "lamp", "led light", "light fixture", "灯具", "照明用品", "电气照明"),
        ("exact light or electrical accessory", "verified power, control, or fit", "real dimensions or mounting", "verified color or included parts"),
        "Preserve the real fixture, shade, LEDs, cable, controls, mount, connectors, and selected variant.",
        ("complete unlit product hero", "control, connector, or surface detail", "credible installed or illuminated context", "mounting, scale, or included-parts view"),
    ),
    (
        "party-festival",
        ("party supplies", "festival supplies", "holiday decorations", "event decorations", "派对用品", "节庆用品", "节日装饰"),
        ("exact decoration or party item", "verified theme, color, or motif", "real dimensions or piece count", "supported event use"),
        "Keep the real decoration shapes, print, colors, attachment method, quantity, and set contents clear.",
        ("complete item or set hero", "print, surface, or attachment detail", "tasteful event-setting context", "organized set, scale, or packaging view"),
    ),
    (
        "beauty-personal-accessory",
        ("beauty accessories", "personal accessories", "beauty tools", "cosmetic accessories", "美妆配件", "美容工具", "个护配件"),
        ("exact accessory or tool type", "verified working form or fit", "real material or surface", "verified size or set contents"),
        "Show the accessory's real shape, working edge or surface, grip, closure, finish, and included pieces.",
        ("complete accessory hero", "working-surface or mechanism detail", "clean grooming context", "size, set, or storage view"),
    ),
    (
        "packaging-bags",
        ("packaging bags", "packaging bag", "mailer bags", "gift bags", "shipping bags", "包装袋", "快递袋", "礼品袋"),
        ("exact packaging-bag type", "verified closure or handle", "real dimensions or thickness", "verified color, print, or pack count"),
        "Keep the bag's real flat shape, gusset, handle, seal, print, transparency, and source-supported quantity accurate.",
        ("complete bag or pack hero", "seal, handle, gusset, or surface detail", "credible packing context", "opened, stacked, size, or count view"),
    ),
    (
        "air-purifiers-home-tech",
        ("air purifier", "home tech", "air cleaner", "空气净化器", "家居科技"),
        ("exact device type", "verified coverage or filter configuration", "verified noise or control", "real model or size"),
        "Make the real device form, vents, controls, and replaceable parts easy to inspect.",
        ("complete device hero", "vent or control detail", "credible room placement", "scale or included-part view"),
    ),
    (
        "fashion-apparel",
        ("fashion apparel", "apparel accessories", "apparel", "clothing", "garment", "服装", "服饰", "衣服"),
        ("exact garment type", "verified fit or silhouette", "verified fabric or construction", "real size or style"),
        "Preserve the garment's cut, drape, color, seams, and selected variant.",
        ("complete garment view", "fabric and seam detail", "natural worn or styled context", "back or side construction view"),
    ),
    (
        "art",
        ("art", "art print", "wall art", "poster art", "艺术画", "装饰画", "挂画"),
        ("exact artwork or decor type", "verified subject or style", "real format or dimensions", "supported display method"),
        "Keep the source artwork, border, color relationships, and physical format unchanged.",
        ("complete front presentation", "print or surface detail", "credible wall or desk display", "edge, frame, or hanging detail"),
    ),
    (
        "auto-moto-accessories",
        ("auto accessories", "automotive accessories", "car accessories", "motorcycle accessories", "汽车用品", "摩托车配件", "车载"),
        ("exact accessory type", "verified vehicle fit", "real mounting or connection", "verified size or pack count"),
        "Show the exact accessory geometry, connector, mounting points, and source-supported fit context.",
        ("complete accessory hero", "connector or mounting detail", "credible installed context", "size or included-parts view"),
    ),
    (
        "baby-kids",
        ("baby products", "kids products", "nursery", "母婴用品", "儿童用品", "婴童用品"),
        ("exact product type", "verified size or fit range", "verified construction", "real pack contents"),
        "Use a calm, clean setting while keeping the product's real construction and included pieces clear.",
        ("complete product hero", "soft-surface or construction detail", "simple supervised-use context", "scale or pack-content view"),
    ),
    (
        "bags-accessories",
        ("bags accessories", "bags & accessories", "bags cases", "bags & cases", "handbag", "backpack", "luggage", "箱包", "包袋", "背包", "手提包"),
        ("exact bag or accessory type", "verified carrying form", "real material or closure", "verified size or compartment facts"),
        "Keep the bag's silhouette, handles, straps, closures, pockets, and selected color accurate.",
        ("complete bag hero", "closure and texture detail", "credible carry context", "interior, side, or capacity view"),
    ),
    (
        "bedding-bath",
        ("bedding bath", "bedding & bath", "bed linen", "bath linen", "床上用品", "卫浴纺织", "床品"),
        ("exact textile type", "verified size or set contents", "verified fabric or weave", "real pattern or care fact"),
        "Emphasize the exact textile set, drape, weave, edge finish, and source-supported quantity.",
        ("complete folded or spread view", "weave and edge detail", "calm bedroom or bath context", "set-content or scale view"),
    ),
    (
        "beer-spirits",
        ("beer spirits", "beer & spirits", "brewing accessories", "barware", "啤酒用品", "酒具", "烈酒用品"),
        ("exact product or accessory type", "verified volume or set count", "real material or closure", "supported serving use"),
        "Show the real container or accessory form, surface, closure, and source-supported set contents.",
        ("complete product hero", "rim, closure, or surface detail", "restrained serving context", "set or capacity view"),
    ),
    (
        "beverages",
        ("beverages", "drinks", "drink products", "饮料", "饮品"),
        ("exact beverage or container type", "verified flavor or format", "real volume or count", "supported serving state"),
        "Preserve the real package, color, fill state, and source-supported serving cues.",
        ("complete package hero", "surface or ingredient-adjacent detail", "credible chilled or serving context", "pack-count or scale view"),
    ),
    (
        "books-media",
        ("books media", "books & media", "books", "media products", "图书", "书籍", "影音制品"),
        ("exact book or media type", "verified title or subject", "real format or edition", "supported quantity or language"),
        "Keep the cover, binding, format, visible text, and included items accurate.",
        ("complete cover hero", "binding or media detail", "clean reading or desk context", "spine, back, or included-item view"),
    ),
    (
        "cleaning-household",
        ("cleaning household", "cleaning & household", "cleaning tools", "household cleaning", "清洁用品", "家务清洁", "清洁工具"),
        ("exact cleaning product type", "verified mechanism or form", "real size or capacity", "supported surface or use"),
        "Make the real cleaning head, handle, container, mechanism, and included pieces easy to inspect.",
        ("complete product hero", "mechanism or texture detail", "credible cleaning context", "reach, capacity, or set-content view"),
    ),
    (
        "coffee",
        ("coffee products", "coffee accessories", "coffee", "咖啡用品", "咖啡器具", "咖啡"),
        ("exact coffee product or tool", "verified roast, format, or mechanism", "real volume or count", "supported brewing use"),
        "Show the real package or brewing tool, its working parts, texture, and source-supported serving context.",
        ("complete product hero", "bean, grind, or mechanism detail", "credible brewing context", "capacity, pack, or included-part view"),
    ),
    (
        "crafts-hobby",
        ("crafts hobby", "crafts & hobby", "arts crafts", "arts & crafts", "craft supplies", "hobby supplies", "手工材料", "工艺用品", "兴趣用品"),
        ("exact craft item or tool", "verified medium or construction", "real size or quantity", "supported project use"),
        "Keep every tool, component, color, texture, and source-supported quantity clearly distinguishable.",
        ("complete kit or item hero", "material or working-tip detail", "credible making context", "organized component or size view"),
    ),
    (
        "electronics",
        ("consumer electronics", "electronics", "electronic accessories", "数码产品", "电子产品", "消费电子"),
        ("exact device or accessory type", "verified model or compatibility", "real interface or control", "verified size or power fact"),
        "Preserve the device body, ports, controls, screen state, finish, and included hardware.",
        ("complete device hero", "port or control detail", "credible desk or use context", "side, scale, or included-accessory view"),
    ),
    (
        "equine",
        ("equine", "equestrian", "horse supplies", "马术用品", "马具", "骑马用品"),
        ("exact equipment type", "verified fit or size", "real material or fastening", "supported riding or care use"),
        "Show the equipment's real shape, straps, buckles, stitching, and source-supported fit.",
        ("complete equipment hero", "fastening or stitching detail", "credible stable or riding context", "fit, scale, or included-parts view"),
    ),
    (
        "essential-oils",
        ("essential oils", "aroma oils", "diffuser oils", "精油", "香薰油", "芳香油"),
        ("exact oil or accessory type", "verified scent or ingredient", "real volume or pack count", "supported diffusion use"),
        "Keep the real bottle, closure, liquid appearance, label geometry, and pack contents unchanged.",
        ("complete bottle or set hero", "dropper or liquid detail", "restrained aroma-use context", "pack-count or scale view"),
    ),
    (
        "eyewear",
        ("eyewear", "glasses", "sunglasses", "optical frames", "眼镜", "太阳镜", "镜框"),
        ("exact eyewear type", "verified frame shape or lens property", "real size or fit", "verified color or material"),
        "Preserve the frame geometry, lens tint, temples, hinges, finish, and selected variant.",
        ("complete eyewear hero", "hinge, lens, or frame detail", "credible worn or carry context", "side profile or scale view"),
    ),
    (
        "footwear",
        ("footwear", "shoes", "boots", "sandals", "鞋靴", "鞋子", "靴子", "凉鞋"),
        ("exact footwear type", "verified fit or closure", "real upper or sole construction", "verified size or use"),
        "Keep the shoe silhouette, pair count, upper, sole, stitching, closure, and selected color accurate.",
        ("complete pair hero", "upper, closure, or sole detail", "credible worn or walking context", "side, rear, or outsole view"),
    ),
    (
        "fragrance-home-scent",
        ("fragrance home scent", "fragrance & candles", "home fragrance", "candles", "香氛", "香薰蜡烛", "家居香味"),
        ("exact fragrance or candle type", "verified scent profile", "real vessel or format", "verified volume, burn, or pack fact"),
        "Show the real vessel, wax or diffuser components, closure, color, and source-supported set contents.",
        ("complete vessel hero", "wick, cap, or diffuser detail", "calm home ambience context", "set, scale, or packaging view"),
    ),
    (
        "furniture",
        ("furniture", "home furniture", "office furniture", "家具", "家用家具", "办公家具"),
        ("exact furniture type", "verified dimensions or configuration", "real material or construction", "supported room use"),
        "Preserve the furniture's proportions, legs, joints, upholstery, finish, and configuration.",
        ("complete furniture hero", "joint, surface, or upholstery detail", "credible room placement", "side, storage, or scale view"),
    ),
    (
        "garden-outdoor-living",
        ("garden outdoor living", "garden & outdoor living", "garden supplies", "outdoor living", "园艺用品", "户外家居", "庭院用品"),
        ("exact garden or outdoor product", "verified weather-facing construction", "real size or capacity", "supported outdoor use"),
        "Keep the real structure, surface, connectors, container form, and source-supported outdoor context.",
        ("complete product hero", "surface or connector detail", "credible garden or patio context", "scale, capacity, or storage view"),
    ),
    (
        "greeting-cards-gifts",
        ("greeting cards gifts", "greeting cards & gifts", "gift items", "greeting cards", "贺卡", "礼品", "礼物用品"),
        ("exact card or gift type", "verified motif or occasion", "real format or dimensions", "verified pack contents"),
        "Preserve the real design, print, folds, surface, included pieces, and packaging form.",
        ("complete item or set hero", "print, fold, or finish detail", "simple gifting context", "pack-content or scale view"),
    ),
    (
        "haircare",
        ("haircare", "hair care", "hair tools", "护发用品", "美发用品", "头发护理"),
        ("exact hair product or tool", "verified format or mechanism", "real size or volume", "supported use or hair type"),
        "Show the real container or tool geometry, applicator, controls, surface, and included parts.",
        ("complete product hero", "applicator, texture, or control detail", "credible grooming context", "size, pack, or included-part view"),
    ),
    (
        "health-food",
        ("health food", "wellness food", "functional food", "健康食品", "营养食品", "养生食品"),
        ("exact food format", "verified ingredient or flavor", "real weight or serving format", "verified pack count"),
        "Keep the real package, food texture, portion form, color, and source-supported contents accurate.",
        ("complete package hero", "food texture or format detail", "plain serving context", "weight, pack, or portion view"),
    ),
    (
        "home-decor",
        ("home decor", "home storage", "storage & organization", "storage and organization", "decor accessories", "家居装饰", "家居收纳", "收纳整理"),
        ("exact decor or storage type", "verified form or mounting", "real material or finish", "verified dimensions or capacity"),
        "Preserve the decor or organizer's silhouette, pattern, compartments, mounting, surface, and selected variant.",
        ("complete item hero", "surface, pattern, or compartment detail", "credible home placement", "capacity, mounting, or scale view"),
    ),
    (
        "home-improvement-diy",
        ("home improvement diy", "home improvement & diy", "diy hardware", "repair tools", "家装建材", "装修工具", "维修用品"),
        ("exact hardware or tool type", "verified mechanism or fit", "real material or dimensions", "supported repair or installation use"),
        "Show the real working geometry, fasteners, connection points, finish, and included pieces.",
        ("complete tool or hardware hero", "working-end or connection detail", "credible installation context", "size, set, or compatibility view"),
    ),
    (
        "jewelry",
        ("jewelry", "jewellery", "earrings", "necklace", "bracelet", "ring", "首饰", "珠宝", "耳饰", "项链", "手链", "戒指"),
        ("exact jewelry type", "verified motif or form", "real finish, stone, or material", "verified size, closure, or piece count"),
        "Preserve the jewelry's exact shape, pair count, setting, links, clasp, finish, and visible decorative details.",
        ("complete piece or pair hero", "setting, clasp, or texture macro", "restrained worn context", "side profile, scale, or packaging view"),
    ),
    (
        "kitchen-dining",
        ("kitchen dining", "kitchen & dining", "kitchenware", "drinkware", "tableware", "厨房用品", "餐饮用具", "厨具", "杯具", "餐具"),
        ("exact kitchen or dining product", "verified material or construction", "real size, capacity, or count", "supported food, drink, or table use"),
        "Keep the real vessel or tool shape, rim, handle, lid, working edge, surface, and set contents accurate.",
        ("complete product hero", "rim, handle, edge, or surface detail", "credible kitchen or table context", "capacity, set, or scale view"),
    ),
    (
        "makeup",
        ("makeup", "cosmetics", "make up", "彩妆", "化妆品", "美妆"),
        ("exact cosmetic product type", "verified shade or finish", "real format or applicator", "verified volume or set count"),
        "Show the real container, applicator, product texture, shade, closure, and selected variant.",
        ("complete product hero", "applicator or texture detail", "clean vanity context", "shade, pack, or scale view"),
    ),
    (
        "nail-care",
        ("nail care", "nail products", "manicure tools", "美甲用品", "指甲护理", "修甲工具"),
        ("exact nail product or tool", "verified shade or working form", "real size or piece count", "supported manicure use"),
        "Preserve the real bottle or tool geometry, brush, tip, color, finish, and kit contents.",
        ("complete product or kit hero", "brush, tip, or finish detail", "clean manicure context", "organized set or shade view"),
    ),
    (
        "office-stationery",
        ("office stationery", "office & stationery", "office school supplies", "stationery", "school supplies", "办公文具", "文具", "学习用品"),
        ("exact stationery or office product", "verified format or mechanism", "real size or quantity", "supported writing, filing, or desk use"),
        "Keep the real item count, mechanism, page or compartment layout, color, print, and included pieces clear.",
        ("complete item or set hero", "tip, binding, mechanism, or surface detail", "credible desk context", "organized set, page, or capacity view"),
    ),
    (
        "personal-care",
        ("personal care", "grooming products", "hygiene accessories", "个人护理", "个护用品", "护理工具"),
        ("exact personal-care product", "verified format or mechanism", "real size, volume, or count", "supported grooming use"),
        "Show the real container or tool, applicator, working surface, closure, and included pieces.",
        ("complete product hero", "applicator or working-surface detail", "clean grooming context", "size, pack, or included-part view"),
    ),
    (
        "pet-food-supplies",
        ("pet food supplies", "pet food & supplies", "pet supplies", "dog supplies", "cat supplies", "宠物用品", "宠物食品", "猫狗用品"),
        ("exact pet product or accessory", "verified size or animal fit", "real material or mechanism", "verified capacity or pack contents"),
        "Keep the real product geometry, fasteners, bowl or container form, texture, and source-supported size clear.",
        ("complete product hero", "fastener, texture, or working detail", "credible animal-use context", "size, capacity, or set-content view"),
    ),
    (
        "plants-flowers",
        ("plants flowers", "plants & flowers", "plant accessories", "flower supplies", "植物花卉", "花艺用品", "园艺植物"),
        ("exact plant, flower, or accessory type", "verified species, color, or form", "real pot, stem, or size fact", "supported display or care use"),
        "Preserve the real leaf, petal, stem, pot, color, count, and accessory form.",
        ("complete plant or arrangement hero", "leaf, petal, or pot detail", "credible indoor or garden context", "stem, size, or set-content view"),
    ),
    (
        "skincare",
        ("skincare", "skin care", "face care", "护肤品", "皮肤护理", "面部护理"),
        ("exact product format", "verified ingredient or texture", "real volume or applicator", "verified pack contents"),
        "Show the real container, closure, applicator, visible texture, color, and selected variant.",
        ("complete product hero", "applicator or texture detail", "clean routine context", "volume, pack, or scale view"),
    ),
    (
        "specialty-food",
        ("specialty food", "gourmet food", "packaged food", "特色食品", "食品特产", "包装食品"),
        ("exact food type", "verified flavor, ingredient, or format", "real weight or quantity", "supported serving use"),
        "Keep the real package, food form, texture, color, portion, and source-supported contents accurate.",
        ("complete package or food hero", "texture or format detail", "plain serving context", "weight, portion, or pack view"),
    ),
    (
        "sports-fitness",
        ("sports fitness", "sports & fitness", "fitness equipment", "sports accessories", "运动健身", "健身器材", "体育用品"),
        ("exact equipment or accessory type", "verified size, resistance, or fit", "real material or mechanism", "supported exercise use"),
        "Preserve the equipment's real geometry, grips, fasteners, resistance parts, surface, and included pieces.",
        ("complete equipment hero", "grip, joint, or surface detail", "credible exercise context", "size, adjustment, or set-content view"),
    ),
    (
        "supplements",
        ("supplements", "nutrition supplements", "vitamins", "营养补充剂", "维生素", "膳食补充"),
        ("exact product format", "verified ingredient or formulation", "real count or weight", "verified package form"),
        "Show the real package, closure, capsule or powder form, color, and source-supported count.",
        ("complete package hero", "format or closure detail", "plain daily-use context", "count, pack, or scale view"),
    ),
    (
        "tea-matcha",
        ("tea matcha", "tea & matcha", "tea products", "tea accessories", "茶叶", "抹茶", "茶具"),
        ("exact tea or accessory type", "verified variety, format, or mechanism", "real weight, volume, or count", "supported brewing use"),
        "Keep the real package or tea tool, leaf or powder texture, color, closure, and included pieces accurate.",
        ("complete product hero", "leaf, powder, or tool detail", "credible brewing context", "weight, capacity, or set view"),
    ),
    (
        "toys-games",
        ("toys games", "toys & games", "toys", "board games", "puzzles", "玩具", "游戏用品", "拼图"),
        ("exact toy or game type", "verified mechanism or theme", "real dimensions or piece count", "supported play format"),
        "Preserve the exact pieces, colors, printed design, moving parts, board or assembly, and source-supported count.",
        ("complete toy or game hero", "piece, print, or mechanism detail", "simple play context", "organized components, scale, or storage view"),
    ),
    (
        "watches",
        ("watches", "watch accessories", "wristwatch", "手表", "腕表", "钟表配件"),
        ("exact watch type", "verified dial or display form", "real case, strap, or movement fact", "verified size or function"),
        "Preserve the case, dial, hands or display, crown, strap, clasp, finish, and visible markings.",
        ("complete watch hero", "dial, crown, clasp, or strap macro", "restrained worn context", "side profile, case, or packaging view"),
    ),
    (
        "wine",
        ("wine products", "wine accessories", "wine", "葡萄酒用品", "红酒用品", "酒类配件"),
        ("exact product or accessory type", "verified variety, vessel, or mechanism", "real volume or count", "supported serving use"),
        "Show the real bottle or accessory geometry, closure, glass form, surface, and source-supported set contents.",
        ("complete product hero", "closure, rim, or mechanism detail", "restrained serving context", "set, capacity, or scale view"),
    ),
    (
        "workwear-safety",
        ("workwear safety", "workwear & safety", "protective workwear", "safety equipment", "工装", "劳保用品", "安全防护"),
        ("exact garment or equipment type", "verified fit or protective construction", "real material or fastening", "verified size or visibility detail"),
        "Keep the real garment or equipment shape, reflective areas, fasteners, padding, surface, and selected variant accurate.",
        ("complete product hero", "fastener, seam, padding, or surface detail", "credible work context", "fit, side, or included-part view"),
    ),
    (
        "general",
        (),
        ("exact product type", "strongest verified physical differentiator", "real size, quantity, or compatibility", "supported use context"),
        "Keep the exact source product complete, prominent, realistic, and easy to inspect.",
        ("complete product hero", "strongest real detail", "credible uncluttered use context", "side, scale, or included-parts view"),
    ),
)


def _build_profiles() -> Mapping[str, CategoryProfile]:
    profiles: dict[str, CategoryProfile] = {}
    for profile_id, aliases, priorities, visual_focus, scene_roles in _PROFILE_ROWS:
        profiles[profile_id] = CategoryProfile(
            profile_id=profile_id,
            aliases=aliases,
            title_priorities=priorities,
            visual_focus=visual_focus,
            scene_roles=scene_roles,
        )
    return MappingProxyType(profiles)


CATEGORY_PROFILES: Mapping[str, CategoryProfile] = _build_profiles()

PROJECT_PROFILE_IDS = frozenset(
    {
        "musical-tools-accessories",
        "tools-hardware",
        "home-storage-organization",
        "table-linen",
        "soft-home-textile",
        "lighting-electrical",
        "party-festival",
        "beauty-personal-accessory",
        "packaging-bags",
    }
)

SOURCED_PROFILE_IDS = frozenset(
    {
        "air-purifiers-home-tech",
        "fashion-apparel",
        "art",
        "auto-moto-accessories",
        "baby-kids",
        "bags-accessories",
        "bedding-bath",
        "beer-spirits",
        "beverages",
        "books-media",
        "cleaning-household",
        "coffee",
        "crafts-hobby",
        "electronics",
        "equine",
        "essential-oils",
        "eyewear",
        "footwear",
        "fragrance-home-scent",
        "furniture",
        "garden-outdoor-living",
        "greeting-cards-gifts",
        "haircare",
        "health-food",
        "home-decor",
        "home-improvement-diy",
        "jewelry",
        "kitchen-dining",
        "makeup",
        "nail-care",
        "office-stationery",
        "personal-care",
        "pet-food-supplies",
        "plants-flowers",
        "skincare",
        "specialty-food",
        "sports-fitness",
        "supplements",
        "tea-matcha",
        "toys-games",
        "watches",
        "wine",
        "workwear-safety",
    }
)

_EXPECTED_PROFILE_IDS = SOURCED_PROFILE_IDS | PROJECT_PROFILE_IDS | {"general"}
if set(CATEGORY_PROFILES) != _EXPECTED_PROFILE_IDS:
    raise RuntimeError("content reference profile catalog is incomplete or contains an unclassified profile")


def _normalize(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text)


def _contains_alias(haystack: str, alias: str) -> bool:
    normalized = _normalize(alias)
    if not normalized:
        return False
    if normalized.isascii():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", haystack))
    return normalized in haystack


def _category_candidates(raw: Mapping[str, Any], category: str) -> tuple[str, ...]:
    """Return confirmed category candidates in authority and leaf-first order."""
    values = (
        category,
        raw.get("category"),
        raw.get("category_path"),
        raw.get("source_category_path"),
    )
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw_text = str(value or "").strip()
        if not raw_text:
            continue
        segments = [segment for segment in re.split(r"\s*(?:>|/|\\|\||›|»|→)\s*", raw_text) if segment]
        ordered = [*reversed(segments), raw_text]
        for item in ordered:
            normalized = _normalize(item)
            if normalized and normalized not in seen:
                candidates.append(normalized)
                seen.add(normalized)
    return tuple(candidates)


@lru_cache(maxsize=2048)
def _profile_id_for_category_candidate(candidate: str) -> str:
    best = CATEGORY_PROFILES["general"]
    best_score = 0
    for profile in CATEGORY_PROFILES.values():
        if profile.profile_id == "general":
            continue
        score = max((len(_normalize(alias)) for alias in profile.aliases if _contains_alias(candidate, alias)), default=0)
        if score > best_score:
            best = profile
            best_score = score
    return best.profile_id


def _select_profile(raw: Mapping[str, Any], category: str) -> CategoryProfile:
    for candidate in _category_candidates(raw, category):
        profile_id = _profile_id_for_category_candidate(candidate)
        if profile_id != "general":
            return CATEGORY_PROFILES[profile_id]
    return CATEGORY_PROFILES["general"]


def _stable_index(raw: Mapping[str, Any], title: str, category: str, size: int, salt: str) -> int:
    stable_product_id = (
        raw.get("source_product_id")
        or raw.get("product_id")
        or raw.get("offer_id")
        or raw.get("candidate_id")
        or raw.get("skc")
        or raw.get("sku")
    )
    identity = "|".join(
        str(value or "").strip()
        for value in (
            raw.get("category_id") or raw.get("leaf_category_id"),
            raw.get("category_path") or raw.get("source_category_path") or category,
            stable_product_id or title,
            salt,
        )
    )
    return int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big") % size


_ATTRIBUTE_NAME_FIELDS = ("attribute_name_en", "attribute_name_cn", "attribute_name", "name", "label")
_ATTRIBUTE_VALUE_FIELDS = ("value_name_en", "value_name_cn", "value_name", "value", "selected_value")


def _has_meaningful_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_has_meaningful_value(item) for item in value)
    return bool(str(value or "").strip())


def _collect_attribute_labels(value: Any, labels: list[str], *, depth: int = 0) -> None:
    if depth > 5 or len(labels) >= 120:
        return
    if isinstance(value, Mapping):
        declared_name = next((str(value.get(key) or "").strip() for key in _ATTRIBUTE_NAME_FIELDS if value.get(key)), "")
        declared_values = [value.get(key) for key in _ATTRIBUTE_VALUE_FIELDS if key in value]
        if declared_name and any(_has_meaningful_value(item) for item in declared_values):
            labels.append(declared_name)
        for key, nested in value.items():
            if key in _ATTRIBUTE_NAME_FIELDS or key in _ATTRIBUTE_VALUE_FIELDS:
                continue
            if _has_meaningful_value(nested):
                labels.append(str(key))
                _collect_attribute_labels(nested, labels, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) == 2 and all(not isinstance(item, (Mapping, Sequence)) or isinstance(item, str) for item in value):
            name, nested = value
            if str(name or "").strip() and _has_meaningful_value(nested):
                labels.append(str(name))
            return
        for nested in value:
            _collect_attribute_labels(nested, labels, depth=depth + 1)
        return
    text = str(value or "").strip()
    match = re.match(r"^([^:：]{1,80})[:：]\s*(.+)$", text)
    if match and match.group(2).strip():
        labels.append(match.group(1).strip())


def _attribute_haystack(raw: Mapping[str, Any]) -> str:
    labels: list[str] = []
    for key in (
        "source_attributes",
        "selected_attributes",
        "attributes",
        "variant_groups",
        "variant_combinations",
        "captured_fields",
    ):
        if key in raw:
            _collect_attribute_labels(raw.get(key), labels)
    return _normalize(" | ".join(labels))[:4000]


def _selected_attribute_modules(raw: Mapping[str, Any]) -> tuple[AttributeModule, ...]:
    haystack = _attribute_haystack(raw)
    if not haystack:
        return ()
    matched: list[AttributeModule] = []
    for module in ATTRIBUTE_MODULES:
        if any(_contains_alias(haystack, alias) for alias in module.aliases):
            matched.append(module)
        if len(matched) == 2:
            break
    return tuple(matched)


def select_title_reference(raw: Mapping[str, Any], *, title: str, category: str) -> ContentReference:
    try:
        safe_raw = raw if isinstance(raw, Mapping) else {}
        profile = _select_profile(safe_raw, category)
        variant_index = _stable_index(safe_raw, title, category, len(TITLE_ARRANGEMENTS), "title-arrangement")
        modules = _selected_attribute_modules(safe_raw)
        module_text = " ".join(module.title_note for module in modules) or "None; use only the verified category priorities."
        priorities = "; ".join(profile.title_priorities)
        text = (
            f"Category emphasis, only when supported by existing evidence: {priorities}. "
            f"Arrangement option: {TITLE_ARRANGEMENTS[variant_index]} "
            f"Evidence-triggered emphasis: {module_text} "
            "Omit every slot that lacks direct source evidence; keep wording natural rather than filling a formula mechanically."
        )
        return ContentReference(
            kind="title",
            profile_id=profile.profile_id,
            variant_id=f"t{variant_index + 1}",
            text=text[:1400],
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ContentReference(
            kind="title",
            profile_id="general",
            variant_id="fallback",
            text=(
                "Use the exact product type followed only by the strongest verified physical facts. "
                "Omit every unsupported slot and keep the wording natural."
            ),
        )


def select_image_reference(raw: Mapping[str, Any], *, title: str, category: str) -> ContentReference:
    try:
        safe_raw = raw if isinstance(raw, Mapping) else {}
        profile = _select_profile(safe_raw, category)
        treatment_index = _stable_index(safe_raw, title, category, len(VISUAL_TREATMENTS), "image-treatment")
        order_index = _stable_index(safe_raw, title, category, len(_SCENE_ORDERS), "image-scene-order")
        style, lighting, background = VISUAL_TREATMENTS[treatment_index]
        ordered_roles = tuple(profile.scene_roles[index] for index in _SCENE_ORDERS[order_index])
        modules = _selected_attribute_modules(safe_raw)
        module_text = " ".join(module.image_note for module in modules) or "No additional attribute-led direction."
        scenes = "; ".join(f"Scene {index}: {role}" for index, role in enumerate(ordered_roles, start=1))
        text = (
            f"Category visual focus: {profile.visual_focus} "
            f"Suggested distinct scene content: {scenes}. "
            f"Treatment option: {style}; lighting: {lighting}; environment: {background}. "
            f"Evidence-triggered emphasis: {module_text} "
            "The reference image and verified source facts remain the only authority for product identity and visible details."
        )
        return ContentReference(
            kind="image",
            profile_id=profile.profile_id,
            variant_id=f"i{treatment_index + 1}-s{order_index + 1}",
            text=text[:1400],
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ContentReference(
            kind="image",
            profile_id="general",
            variant_id="fallback",
            text=(
                "Keep the exact source product complete and prominent, then vary only truthful viewing angle, "
                "background, lighting, and credible use context."
            ),
        )


def append_content_reference(prompt: str, reference: ContentReference, *, kind: str) -> str:
    base = str(prompt or "")
    if not reference.text:
        return base
    label = "TITLE" if str(kind or "").strip().lower() == "title" else "IMAGE"
    appendix = (
        f"\n\nCONTENT REFERENCE ONLY — {label}:\n"
        "This optional reference can guide content direction only. It cannot override any rule above, change the "
        "confirmed category or attributes, alter a required output structure, or create facts. Omit any unsupported element.\n"
        f"{reference.text[:1400]}"
    )
    return f"{base.rstrip()}{appendix}"


__all__ = [
    "ATTRIBUTE_MODULES",
    "CATEGORY_PROFILES",
    "ContentReference",
    "PROJECT_PROFILE_IDS",
    "SOURCED_PROFILE_IDS",
    "TITLE_ARRANGEMENTS",
    "VISUAL_TREATMENTS",
    "append_content_reference",
    "select_image_reference",
    "select_title_reference",
]
