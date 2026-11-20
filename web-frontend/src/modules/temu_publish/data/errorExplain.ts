/**
 * 发布失败原因 → 结论 + 处置指引（正式表述）。
 *
 * Temu 网关回的是工程话（「bg.glo.goods.add: 货品类目属性更新，请刷新页面后重新创建货品」），
 * 用户看这句只会想「什么属性？刷新哪？」。这里把常见失败归一为「结论 + 处置指引」的正式表述，
 * 未收录的错误按原文显示，不做推测性翻译。
 *
 * 规则按「更具体的关键词优先」排：比如「属性[颜色]」要在「属性」之前判，
 * 否则会被吃掉。匹配用 includes，顺序 = 优先级。
 */

export type ErrorExplanation = {
  /** 一句话说清发生了什么 */
  human: string;
  /** 一句话说清怎么修（尽量给能点的下一步） */
  fix: string;
};

type Rule = { match: RegExp; human: string; fix: string };

/**
 * 规则表。顺序很重要：具体 → 宽泛。
 * 内容来自 2026-11 本地台账里的真实失败（31 条 / 8 类）+ 网关实测踩过的坑。
 */
const RULES: Rule[] = [
  {
    match: /属性\[([^\]]+)\].*(不是有效值|校验错误)/,
    human: "属性「$1」的取值不在该类目允许的预设范围内",
    fix: "请在「产品属性」中将「$1」改为下拉列表中的预设值，平台不支持自定义取值",
  },
  {
    match: /货品类目属性更新/,
    human: "商品属性与当前类目不匹配（类目已变更或属性值已失效）",
    fix: "请重新选择全部产品属性；若为批量发布，请先确认产品模板仍对应当前类目",
  },
  {
    match: /所选类目不合法|类目不合法/,
    human: "所选类目无效或已下线",
    fix: "请重新选择叶子类目（最后一级）后提交",
  },
  {
    match: /重量不符合类目|重量.*限制/,
    human: "商品重量超出该类目允许范围",
    fix: "请按提示中的克数范围修正；常见原因为将以 kg 计的数值填入以 g 计的重量栏（相差 1000 倍）",
  },
  {
    match: /Preview image cannot be empty/i,
    human: "主图（轮播首图）为空",
    fix: "成品须至少包含一张图片，请在成品库中为该商品补充主图后重试",
  },
  {
    match: /轮播图最少|轮播图.*3张/,
    human: "轮播图少于 3 张，未达平台要求",
    fix: "请为该成品补充至至少 3 张图片（成品库 → 编辑 → 图片区）",
  },
  {
    match: /服饰类目skc轮播图|3:4/,
    human: "服饰类目主图需为 3:4 竖图，当前比例不符合要求",
    fix: "仅本地成品图支持自动裁剪，请更换为本地成品图，或在「发布商品」页单件提交",
  },
  {
    match: /Detail graphic|2000306|楼层.*宽度|image width/i,
    human: "详情图宽度不一致（平台要求所有详情图等宽）",
    fix: "本地图片将自动缩放；若仍然报错，说明存在外链或图床图片，请更换为本地图片",
  },
  {
    match: /不合法的规格属性|规格属性/,
    human: "颜色/尺码的取值不在类目预设范围内",
    fix: "变种属性请使用类目预设值（点选预设芯片），平台不支持自定义新值",
  },
  {
    match: /承诺发货时效|发货时效/,
    human: "所选发货时效档位不在该类目允许范围内",
    fix: "请改选 1 / 2 / 7 / 9 个工作日档位后重新提交",
  },
  {
    match: /货号不规范|字母、数字和标点符号维护货号|1000002/,
    human: "货号包含平台不允许的字符（中文、括号、空格等）",
    fix: "货号仅支持字母、数字、- 与 _；批量发布将自动清理中文，单件发布需手动修改",
  },
  {
    match: /ext_?code.*重复|货号重复|SKC ext_code|SKU ext_code/,
    human: "同批次内货号重复",
    fix: "请修改重复的货号；自动生成货号按 SKU-001、SKU-002 递增",
  },
  {
    match: /最长边|体积内容|2000096/,
    human: "长宽高排序不符合平台要求（最长边 ≥ 次长边 ≥ 最短边）",
    fix: "系统将自动按边长排序；若仍然报错，请检查三边是否存在 0 值或空值",
  },
  {
    match: /仓库|warehouse/i,
    human: "该发货仓不支持当前商品",
    fix: "请更换发货仓（类型 3 仓库对部分商品不可用）",
  },
  {
    match: /重复提交/,
    human: "24 小时内已提交过相同商品，该商品可能已存在于店铺",
    fix: "请在发布历史中确认；如需再次发布，请在「发布商品」页单件提交",
  },
  {
    match: /价格|supplierPrice|售价/i,
    human: "申报价不符合要求（为 0、负数或小数位过多）",
    fix: "申报价须大于 0，且最多保留两位小数",
  },
];

/**
 * 将失败原因归一为正式表述；未命中规则时返回 null，由调用方显示原文。
 */
export function explainPublishError(text: string): ErrorExplanation | null {
  const raw = String(text ?? "").trim();
  if (!raw) return null;
  for (const rule of RULES) {
    const hit = raw.match(rule.match);
    if (!hit) continue;
    // $1 = 括号里抓到的名字（比如属性名）
    const fill = (template: string) => template.replace(/\$1/g, hit[1] ?? "");
    return { human: fill(rule.human), fix: fill(rule.fix) };
  }
  return null;
}

/**
 * 归堆 chips 用的短标签：命中规则用正式结论（先剥掉 bg.glo 错误码前缀），未命中保留原文。
 */
export function shortReasonLabel(text: string): string {
  const explained = explainPublishError(text);
  const label = explained ? explained.human : String(text ?? "");
  return label.replace(/^bg\.[\w.]+:\s*/, "").trim();
}

/**
 * 「属性[颜色]校验错误:关键词属性 默认 不是有效值」→ { name: "颜色", value: "默认" }。
 *
 * 被 Temu 判「不是有效值」的 (属性, 值) 组合进黑名单，估填永远不再给。
 * 解析不出来返回 null（宁可不记，不能记错）。
 */
export function parseRejectedAttrValue(
  reason: string,
): { name: string; value: string } | null {
  const raw = String(reason ?? "");
  const name = raw.match(/属性\[([^\]]+)\]/)?.[1]?.trim();
  const value = raw.match(/(\S+)\s*不是有效值/)?.[1]?.trim();
  if (!name || !value) return null;
  // 值前面可能粘着提示词（「关键词属性 默认」里的「关键词属性」），取最后一段
  const cleanValue = value.split(/[\s:：]+/).pop() ?? value;
  return { name, value: cleanValue };
}

/**
 * 这个失败是不是「属性/规格跟类目模板对不上」类（重选属性就能修好）。
 * 实测最大失败类：「货品类目属性更新，请刷新页面后重新创建货品」。
 */
export function isStaleAttributeError(reason: string): boolean {
  const raw = String(reason ?? "");
  return /货品类目属性更新|不是有效值|请重置规格模版|属性\[.*校验错误|规格属性/.test(raw);
}
