/**
 * 英文商品词 → 中文品类词（静态词典，零成本零依赖）。
 *
 * 英文标题拿去打 category.match 远不如中文稳（实测磁吸手机壳被配到女士夹克）。
 * 这张表把标题主体里的商品名词翻成品类词，当一条**语义证据**进查询计划 ——
 * 介于「采集属性锚定」和「原文标题」之间。
 *
 * 词条只收自家卖的品类（箱包/化妆包/桌布/工具/服饰/手机配件），
 * 从真实标题词频挖出来人工过目；多义词宁可不收，收了按最长词组优先匹配。
 */

/** [英文词组, 中文品类词]。匹配时按英文长度从长到短，长词组压过短词组。 */
export const PRODUCT_NOUNS: Array<[string, string]> = [
  // 箱包
  ["cosmetic pouch", "化妆包"],
  ["cosmetic bag", "化妆包"],
  ["makeup pouch", "化妆包"],
  ["toiletry bag", "洗漱包"],
  ["toiletry pouch", "洗漱包"],
  ["tool storage bag", "工具包"],
  ["tool bag", "工具包"],
  ["shoulder bag", "单肩包"],
  ["tote bag", "手提包"],
  ["hobo bag", "单肩包"],
  ["bucket bag", "水桶包"],
  ["crossbody bag", "斜挎包"],
  ["sling bag", "斜挎包"],
  ["backpack", "双肩包"],
  ["duffel bag", "旅行包"],
  ["laptop sleeve", "电脑内胆包"],
  ["wallet", "钱包"],
  ["clutch", "手拿包"],
  ["tote", "手提包"],
  ["purse", "手提包"],
  ["pouch", "收纳包"],
  // 服饰
  ["sweatpants", "运动裤"],
  ["jogger", "运动裤"],
  ["trousers", "休闲裤"],
  ["pants", "裤子"],
  ["jeans", "牛仔裤"],
  ["shorts", "短裤"],
  ["skirt", "半身裙"],
  ["dress", "连衣裙"],
  ["hoodie", "卫衣"],
  ["cardigan", "开衫"],
  ["jacket", "外套"],
  ["coat", "大衣"],
  ["vest", "背心"],
  ["pajama", "睡衣"],
  ["swimsuit", "泳衣"],
  ["scarf", "围巾"],
  ["shawl", "披肩"],
  ["glove", "手套"],
  ["sock", "袜子"],
  ["belt", "腰带"],
  ["hat", "帽子"],
  ["cap", "帽子"],
  ["bra", "文胸"],
  ["wig", "假发"],
  // 家纺 / 家居
  ["tablecloth", "桌布"],
  ["placemat", "餐垫"],
  ["curtain", "窗帘"],
  ["blanket", "毯子"],
  ["throw blanket", "毯子"],
  ["bedding set", "四件套"],
  ["quilt", "被子"],
  ["duvet", "被芯"],
  ["pillowcase", "枕套"],
  ["pillow cover", "枕套"],
  ["pillow", "枕头"],
  ["sheet", "床单"],
  ["towel", "毛巾"],
  ["bath mat", "地垫"],
  ["rug", "地毯"],
  ["cushion cover", "抱枕套"],
  ["pillow sham", "抱枕套"],
  ["apron", "围裙"],
  ["curtain", "窗帘"],
  ["storage box", "收纳箱"],
  ["storage bag", "收纳袋"],
  ["organizer", "收纳盒"],
  ["hanger", "衣架"],
  // 手机 / 电子配件
  ["phone case", "手机壳"],
  ["tablet case", "平板保护套"],
  ["laptop case", "电脑包"],
  ["screen protector", "钢化膜"],
  ["charger", "充电器"],
  ["cable sleeve", "数据线保护套"],
  ["charging cable", "数据线"],
  ["cable", "数据线"],
  ["earbuds", "耳机"],
  ["headphone", "耳机"],
  ["earphone", "耳机"],
  ["keyboard", "键盘"],
  ["mouse", "鼠标"],
  ["phone stand", "手机支架"],
  ["car mount", "车载支架"],
  ["power bank", "充电宝"],
  ["speaker", "音箱"],
  ["smartwatch", "智能手表"],
  ["watch", "手表"],
  // 饰品 / 美妆工具
  ["hair clip", "发夹"],
  ["hairpin", "发簪"],
  ["necklace", "项链"],
  ["earring", "耳环"],
  ["bracelet", "手链"],
  ["bangle", "手镯"],
  ["ring", "戒指"],
  ["brooch", "胸针"],
  ["keychain", "钥匙扣"],
  ["sunglasses", "太阳镜"],
  ["glasses", "眼镜"],
  ["jewelry set", "饰品套装"],
  ["makeup brush", "化妆刷"],
  ["mirror", "镜子"],
  // 工具 / 汽车
  ["screwdriver", "螺丝刀"],
  ["knife sharpener", "磨刀器"],
  ["sharpener", "磨刀器"],
  ["plier", "钳子"],
  ["wrench", "扳手"],
  ["tool set", "工具套装"],
  ["tool kit", "工具套装"],
  ["tape measure", "卷尺"],
  ["flashlight", "手电筒"],
  ["gloves", "手套"],
  ["seat cover", "座椅套"],
  ["floor mat", "脚垫"],
  ["steering wheel cover", "方向盘套"],
  // 纸品 / 宠物
  ["tissue", "面巾纸"],
  ["paper liner", "烘焙纸杯"],
  ["notebook", "笔记本"],
  ["pet bowl", "宠物碗"],
  ["dog leash", "狗牵引绳"],
  ["pet toy", "宠物玩具"],
];

/** 标题主体段：截到第一个逗号/分号/竖线前，再去掉 ` for …` 用途说明。 */
function coreSegment(title: string): string {
  const raw = String(title ?? "").trim();
  if (!raw) return "";
  const head = raw.split(/[,，;；|]/)[0] || raw;
  return (head.split(/\s+for\s+/i)[0] || head).trim();
}

/**
 * 从标题主体里提品类词（中文）。
 *
 * 最长词组优先（`phone case` 压过 `case`、`suitcase` 不会被 `case` 命中——
 * 词边界兜住），返回去重后的品类词（最多 2 个，够查询用就行）。
 */
export function productNounTexts(title: string): string[] {
  const core = coreSegment(title);
  if (!core) return [];
  const lower = core.toLowerCase();
  const consumed: Array<[number, number]> = [];
  const out: string[] = [];
  const overlaps = (start: number, end: number) =>
    consumed.some(([from, to]) => start < to && end > from);
  // 长词组先匹配并占坑，短词组只捡剩下的
  const sorted = [...PRODUCT_NOUNS].sort((a, b) => b[0].length - a[0].length);
  for (const [en, zh] of sorted) {
    const index = lower.indexOf(en);
    if (index < 0) continue;
    // ASCII 词边界：前面/后面不能紧挨着字母数字（`cot` 不能命中 `cotton`）
    const before = index > 0 ? lower[index - 1] : "";
    const after = lower[index + en.length] ?? "";
    const isAscii = /^[\x20-\x7e]+$/.test(en);
    if (isAscii && (/[a-z0-9]/.test(before) || /[a-z0-9]/.test(after))) continue;
    const end = index + en.length;
    if (overlaps(index, end)) continue;
    consumed.push([index, end]);
    if (!out.includes(zh)) out.push(zh);
    if (out.length >= 2) break;
  }
  return out;
}
