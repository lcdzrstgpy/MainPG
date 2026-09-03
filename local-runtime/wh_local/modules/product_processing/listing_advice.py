"""Short TEMU listing advice backed by the user's category qualification table.

The table is deliberately represented as data instead of prompt prose so the
model can explain a match but cannot invent a more permissive risk level.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any


@dataclass(frozen=True)
class ListingRule:
    number: int
    primary: str
    secondary: str
    level: str
    direction: str
    listing_requirement: str
    prohibited: str
    documents: str
    keywords: tuple[str, ...]


RULES: tuple[ListingRule, ...] = (
    ListingRule(1, "办公用品", "文具用品、学校教育用品", "A-优先", "无资质直接做", "标题避免‘儿童、无毒、可食用、益智教育’等敏感词", "儿童文具、颜料胶水、磁性或带电办公用品", "CPC/CPSIA、SDS/材质报告，带电产品另需 FCC/电池安全资料", ("office", "stationery", "pen", "pencil", "notebook", "folder", "stapler", "办公", "文具")),
    ListingRule(2, "宠物用品", "宠物服饰、宠物玩具、宠物日用品", "A-优先", "适合铺货，非药品、非电产品", "标题和页面不得宣传治疗疾病、药效或电子功能", "宠物食品、零食、药品、驱虫、医疗及电子项圈", "入口食品需 FDA 等食品合规；电子产品需 FCC/电池认证", ("pet", "dog", "cat", "leash", "collar", "宠物", "猫", "狗")),
    ListingRule(3, "家居/厨房用品", "收纳、厨房工具、餐具用品", "A-优先", "核心优先类目", "不得宣传 food grade、BPA free 等无法证明的属性", "食品直接接触材料、保鲜盒、硅胶厨具、锅具、玻璃用品", "食品接触材料、微波炉/电器类需对应 FDA/FCC/电池/电气安全资料", ("kitchen", "storage", "organizer", "bowl", "plate", "spoon", "home", "厨房", "收纳", "餐具")),
    ListingRule(4, "家居装饰", "装饰品、五金工具、灯饰配件", "A-优先", "可做挂墙和五金类", "优先选择不依赖安装安全功能的装饰和五金", "刀具、打火机、明火、磁铁、强承重或防跌落产品", "FCC、电子安全、明火/阻燃或承重资料（视产品而定）", ("decor", "ornament", "wall hanging", "frame", "vase", "家居装饰", "装饰品")),
    ListingRule(5, "手机和配件", "手机壳、支架、充电周边", "A-优先", "核心优先类目", "无资质时优先手机壳、支架、卡扣、收纳等无电配件", "充电器、无线充、移动电源、蓝牙及带电产品", "FCC、电子安全、电池 UN38.3/MSDS", ("phone case", "mobile case", "phone stand", "手机壳", "手机支架", "手机配件")),
    ListingRule(6, "庭院、户外和花园", "园艺工具、户外装饰品", "A-优先", "核心优先类目", "不得宣传驱虫、杀虫、阻燃、防虫网等功能性用途", "花盆、园艺工具、非化学非电装饰优先；避开农药、驱虫剂和燃气类", "FCC/电气安全、电池 UN38.3/MSDS；化学品需 EPA 等", ("garden", "outdoor", "planter", "flower pot", "yard", "庭院", "园艺", "花盆")),
    ListingRule(7, "服装、鞋靴和珠宝", "女装、男装、鞋靴、珠宝配件", "A-优先", "成人普通款优先", "成人普通服装、鞋、帽、围巾、腰带、发饰和首饰可做", "儿童服装、婴幼儿鞋帽、带强磁/金属尖锐件产品", "儿童产品需 CPC/CPSIA、阻燃/材质等；珠宝需视材质合规", ("clothing", "dress", "shirt", "shoe", "jewelry", "necklace", "服装", "鞋", "珠宝")),
    ListingRule(8, "艺术品、手工艺品和缝纫", "手工材料、DIY工具、艺术工艺品", "A-优先", "核心优先类目", "成人画材和手工材料不得宣传无毒或儿童适用", "儿童 DIY、颜料、胶水、磁铁、缝纫针及尖锐工具", "CPC/CPSIA、SDS/材质报告或相应安全测试", ("craft", "sewing", "diy", "art supply", "手工艺", "缝纫", "画材")),
    ListingRule(9, "乐器", "乐器配件、乐器周边", "B-可做", "配件方向可做", "弦、拨片、支架、清洁布、收纳包等无电配件优先", "电子琴、功放、无线、蓝牙、麦克风等电子产品", "FCC、电子安全、电池 UN38.3/MSDS", ("instrument", "guitar", "violin", "piano", "music stand", "乐器", "吉他")),
    ListingRule(10, "汽车用品", "汽车配件、清洁用品", "B-可做", "内部装饰和普通配件可做", "不影响驾驶视线、不承重、不涉及安全系统的普通配件", "车灯、充电器、电子配件、儿童安全座椅、方向盘等安全关键件", "DOT/FMVSS、FCC、电子安全或儿童乘员保护资料", ("car", "auto", "vehicle", "seat cover", "car pendant", "汽车", "车载")),
    ListingRule(11, "运动与户外用品", "健身用品、露营装备", "B-可做", "普通运动和休闲配件可做", "不宣传专业救援、防护或承重能力", "头盔、救生衣、攀岩、承重绳索、专业防护品", "CPSC/ASTM/ANSI 等专业安全或 FCC/电气安全资料", ("sport", "fitness", "camping", "yoga", "运动", "健身", "露营")),
    ListingRule(12, "Handmade Products（手工制品）", "手工饰品、定制礼品、DIY 制品", "B-可做", "普通手工品可做", "必须手工属性真实，避免仿牌和受监管材料", "手工食品、化妆品、儿童用品及含液体、粉末、强功效宣称的产品", "视产品而定；涉及儿童/FDA/化学品时提供相应合规资料", ("handmade", "handcrafted", "craft", "pendant", "keychain", "gourd", "natural wood", "手工", "手作", "定制礼品")),
    ListingRule(13, "COD 和遥控器", "遥控器、遥控配件", "C-不建议", "仅建议无 IR/RF 收发的纯外壳配件", "只有硅胶套、收纳盒、展示架等无电附件风险较低", "遥控器、遥控芯片、蓝牙、红外、射频模块", "FCC（适用）及电子安全资料", ("remote control", "controller", "遥控器", "遥控")),
    ListingRule(14, "工业和科学", "工业耗材、实验防护", "C-谨慎", "适合普通工具和收纳", "避开医疗、实验室诊断、防护等级和精密工业功能宣称", "激光、化学品、个人防护、实验室或精密测量设备", "FDA/OSHA/NIOSH、SDS 等相应资料", ("industrial", "laboratory", "scientific", "工业", "实验室")),
    ListingRule(15, "美容和个人护理", "美容工具、个人护理用品", "C-谨慎", "工具型产品可做，产品本体不建议", "仅无液体工具、发饰、美甲工具、化妆包等较安全", "护肤、液体、粉末、膏霜、香水、仪器及功效宣称", "FDA/MoCRA、成分与安全报告、FCC/电气安全（如适用）", ("beauty", "cosmetic", "makeup", "skincare", "美容", "美妆", "护肤")),
    ListingRule(16, "视频游戏", "游戏周边、游戏配件", "C-谨慎", "改装配件、无电配件谨慎做", "配件图片不得出现受保护角色、商标或游戏画面", "游戏机、主机、无线手柄、带电改装产品", "版权/品牌授权、FCC、电气安全", ("gaming", "video game", "console", "game controller", "游戏", "主机")),
    ListingRule(17, "收藏品和工艺品", "收藏模型、纪念品、艺术收藏", "C-谨慎", "普通展示与非 IP 工艺品可做", "避免古董、珍稀、官方授权等无法证明的描述", "品牌/IP/明星周边、仿品、受保护材料或来源不明收藏品", "来源/真伪/成色证明、CITES（适用）、品牌授权", ("collectible", "souvenir", "figurine", "model", "收藏", "纪念品")),
    ListingRule(18, "图书", "纸质图书、教育读物", "C-谨慎", "无版权供应链不做", "需有出版社、书号和正规来源，避免盗版", "扫描件、网盘资源、盗版印刷和来源不明进口出版物", "版权/出版发行/进货证明", ("book", "publication", "textbook", "图书", "书籍")),
    ListingRule(19, "各色美食", "零食、饮料、食品原料", "D-高门槛", "无资质不做", "不建议为流量错放类目；无食品资质不要进入", "食品、饮料、调味品、保健食品和维生素", "FDA 食品设施/标签/供应链等适用资料", ("food", "snack", "drink", "edible", "vitamin", "食品", "零食", "饮料")),
    ListingRule(20, "健康和家居用品", "健康辅助用品、生活用品", "D-高门槛", "无资质不建议进入", "标题和图片不得出现 medical、therapy、pain relief、sterilize 等医疗功效词", "医疗器械、血压计、体温计、按摩理疗、消毒杀菌产品", "FDA 注册/列名/510(k)（适用）、EPA", ("medical", "therapy", "pain relief", "sterilize", "blood pressure", "医疗", "理疗", "消毒")),
    ListingRule(21, "电影和电视", "影视周边、影视收藏", "D-高门槛", "无授权不做", "非授权影视商品侵权风险高", "DVD、蓝光、影视周边、授权海报", "版权或品牌授权、正版发行证明", ("movie", "television", "dvd", "film poster", "电影", "影视")),
    ListingRule(22, "玩具与游戏", "益智玩具、儿童玩具、桌游", "D-高门槛", "无资质不建议进入", "仅写成人用途不能规避实际儿童属性；设计和包装年龄必须一致", "儿童玩具、磁性玩具、弹射玩具和带电玩具", "CPC、第三方测试、ASTM F963/CPSIA", ("toy", "kids", "children", "puzzle", "board game", "玩具", "儿童")),
    ListingRule(23, "母婴用品", "婴童用品、育儿用品、安全用品", "D-高门槛", "无资质不建议进入", "母婴类审核严格，不建议新店试错", "奶瓶奶嘴、婴儿玩具、推车、床品和安全座椅", "CPC/CPSIA、食品接触测试及相应婴童标准", ("baby", "infant", "toddler", "stroller", "母婴", "婴儿")),
    ListingRule(24, "家电", "厨房小家电、生活电器", "D-高门槛", "无资质仅做被动配件", "选择无电配件且主图不得呈现通电功能", "插电或电池家电、加热类、制冷类和厨房电器", "FCC（适用）、电气安全、能效、UN38.3/MSDS", ("appliance", "heater", "electric kettle", "vacuum", "家电", "电器")),
    ListingRule(25, "电子", "电子配件、数码设备、智能产品", "D-高门槛", "无资质只做无电无无线配件", "非电子配件不得填写功率、电压、蓝牙版本等属性", "耳机、音箱、摄像头、遥控器、蓝牙/Wi-Fi 设备和移动电源", "FCC、SDOC/认证、电气安全、UN38.3/MSDS", ("electronic", "bluetooth", "wifi", "charger", "battery", "电子", "充电", "蓝牙")),
    ListingRule(26, "礼品卡", "数字礼品卡、实体礼品卡", "E-专项准入", "无专项权限不做", "普通卖家不要上架卡密、充值或兑换码", "实体/电子礼品卡、充值卡、兑换码", "平台专项准入、发行方授权及合法来源", ("gift card", "voucher", "redeem code", "礼品卡", "充值卡", "兑换码")),
    ListingRule(27, "软件", "软件授权、数字产品", "E-专项准入", "无专项权限不做", "禁止破解、共享账号及来源不明密钥", "软件授权码、订阅、数字下载和安装介质", "软件版权/分销授权、平台数字商品准入", ("software", "license key", "digital download", "subscription", "软件", "授权码")),
)


RED_FLAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("儿童/婴幼儿用途", ("child", "children", "kid", "kids", "baby", "infant", "toddler", "儿童", "婴儿", "母婴")),
    ("入口食品或宠物食品", ("food", "edible", "snack", "drink", "vitamin", "pet food", "食品", "零食", "饮料", "保健品")),
    ("带电、无线或电池", ("electric", "electronic", "battery", "rechargeable", "bluetooth", "wi-fi", "wifi", "wireless", "charger", "voltage", "电池", "充电", "蓝牙", "无线", "电压")),
    ("液体、膏体或粉末", ("liquid", "cream", "lotion", "serum", "powder", "oil", "液体", "膏", "粉末", "精华", "精油")),
    ("医疗、治疗或消杀宣称", ("medical", "therapy", "treatment", "pain relief", "sterilize", "disinfect", "antibacterial", "医疗", "治疗", "止痛", "消毒", "杀菌")),
    ("食品接触/明火/阻燃宣称", ("food grade", "bpa free", "microwave", "gas", "flame", "fireproof", "食品级", "阻燃", "明火", "燃气")),
    ("救生、防护或承重用途", ("lifesaving", "helmet", "climbing", "load bearing", "protective", "safety harness", "救生", "头盔", "攀岩", "承重", "防护")),
    ("品牌、IP 或官方授权", ("official", "licensed", "celebrity", "disney", "marvel", "官方", "授权", "明星", "品牌同款")),
    ("卡密、订阅或数字下载", ("gift card", "activation code", "license key", "subscription", "digital download", "卡密", "激活码", "订阅", "数字下载")),
)


def _normalized(*values: str) -> str:
    return " ".join(str(value or "").strip().lower() for value in values)


def _contains(text: str, term: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 .+/'-]*", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def rank_listing_rules(title: str, description: str = "", category_path: str = "", *, limit: int = 3) -> list[ListingRule]:
    text = _normalized(category_path, title, description)
    category = _normalized(category_path)
    ranked: list[tuple[int, int, ListingRule]] = []
    for rule in RULES:
        score = 0
        if category and (_contains(category, rule.primary.lower()) or _contains(rule.primary.lower(), category)):
            score += 40
        for keyword in rule.keywords:
            if _contains(text, keyword):
                score += 8 if _contains(category, keyword) else 3
        for fragment in re.split(r"[、，,/（）()\s]+", f"{rule.primary} {rule.secondary}"):
            if len(fragment) >= 2 and _contains(text, fragment):
                score += 4
        ranked.append((score, -rule.number, rule))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[: max(1, limit)]]


def detect_red_flags(title: str, description: str = "", category_path: str = "") -> list[str]:
    text = _normalized(category_path, title, description)
    return [label for label, terms in RED_FLAGS if any(_contains(text, term) for term in terms)]


def prepare_listing_context(title: str, description: str = "", category_path: str = "") -> dict[str, Any]:
    candidates = rank_listing_rules(title, description, category_path)
    return {
        "title": str(title or "").strip(),
        "description": str(description or "").strip(),
        "category_path": str(category_path or "").strip(),
        "candidates": candidates,
        "red_flags": detect_red_flags(title, description, category_path),
    }


def deterministic_listing_advice(context: dict[str, Any], *, notice: str = "") -> dict[str, Any]:
    rule: ListingRule = context["candidates"][0]
    flags = list(context.get("red_flags") or [])
    warning = rule.listing_requirement
    if flags:
        warning = f"检测到{('、'.join(flags[:3]))}；{warning}"
    return {
        "level": rule.level,
        "action": rule.direction,
        "recommended_category": f"{rule.primary} → {rule.secondary}",
        "reason": f"按表格第 {rule.number} 类规则匹配。{rule.direction}。",
        "warning": warning,
        "required_documents": [rule.documents] if rule.documents else [],
        "matched_rule_number": rule.number,
        "matched_rule": rule.primary,
        "source": "rules",
        "notice": notice,
    }


def build_listing_advice_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    rule_payload = [
        {
            "number": rule.number,
            "primary": rule.primary,
            "secondary": rule.secondary,
            "level": rule.level,
            "direction": rule.direction,
            "listing_requirement": rule.listing_requirement,
            "prohibited": rule.prohibited,
            "documents": rule.documents,
        }
        for rule in context["candidates"]
    ]
    payload = {
        "product": {
            "title": context["title"],
            "description": context["description"][:6000],
            "current_category": context["category_path"],
        },
        "detected_red_flags": context["red_flags"],
        "candidate_rules": rule_payload,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是 TEMU 商品上架预审助手。只能根据给定表格候选规则判断，不得降低规则中的风险等级。"
                "选择最匹配的规则，用简短中文输出纯 JSON，不要 Markdown。字段必须为："
                "matched_rule_number（整数）、reason（不超过45字）、warning（不超过65字）、"
                "required_documents（最多2条短句，可为空数组）。建议仅供预检，不能声称保证审核通过。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def merge_ai_listing_advice(context: dict[str, Any], content: str) -> dict[str, Any]:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI advice was not JSON")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI advice JSON must be an object")
    candidates: dict[int, ListingRule] = {rule.number: rule for rule in context["candidates"]}
    try:
        number = int(parsed.get("matched_rule_number"))
    except (TypeError, ValueError) as exc:
        raise ValueError("AI advice omitted matched rule") from exc
    rule = candidates.get(number)
    if rule is None:
        raise ValueError("AI advice selected a rule outside the candidates")

    def short_text(value: Any, limit: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]

    documents = parsed.get("required_documents")
    normalized_documents = []
    if isinstance(documents, list):
        normalized_documents = [short_text(item, 80) for item in documents if short_text(item, 80)][:2]
    return {
        "level": rule.level,
        "action": rule.direction,
        "recommended_category": f"{rule.primary} → {rule.secondary}",
        "reason": short_text(parsed.get("reason"), 90) or f"按表格第 {rule.number} 类规则匹配。",
        "warning": short_text(parsed.get("warning"), 130) or rule.listing_requirement,
        "required_documents": normalized_documents,
        "matched_rule_number": rule.number,
        "matched_rule": rule.primary,
        "source": "ai+rules",
        "notice": "",
    }


def rule_as_dict(rule: ListingRule) -> dict[str, Any]:
    """Small public helper useful to diagnostics and tests."""
    return asdict(rule)
