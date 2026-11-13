/**
 * 发布失败原因 → 人话 + 修法。
 *
 * Temu 网关回的是工程话（「bg.glo.goods.add: 货品类目属性更新，请刷新页面后重新创建货品」），
 * 用户看这句只会想「什么属性？刷新哪？」。这里把常见的失败翻译成一句人话和一条能照做的修法，
 * 不认识的错误照原样显示（宁可不翻译，也不能瞎猜）。
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
    human: "属性「$1」的值不在这个类目的预设里，Temu 不认",
    fix: "去修这条 → 产品属性区把「$1」换成下拉里的预设值，别手填新值",
  },
  {
    match: /货品类目属性更新/,
    human: "产品属性和当前类目对不上（类目可能换了，或属性值已失效）",
    fix: "去修这条 → 把「产品属性」全部重选一遍；批量发的话先确认产品模板还是这个类目",
  },
  {
    match: /所选类目不合法|类目不合法/,
    human: "类目选错了，或这个类目已下线",
    fix: "重新选一个叶子类目（最后一级，别停在中间层）再发",
  },
  {
    match: /重量不符合类目|重量.*限制/,
    human: "重量超出这个类目允许的范围",
    fix: "看错误里的克数范围；常见原因是把 kg 填进了 g 的格子（差 1000 倍）",
  },
  {
    match: /Preview image cannot be empty/i,
    human: "主图（轮播第一张）是空的",
    fix: "草稿至少要有一张图；去成品库给这条补上主图再发",
  },
  {
    match: /轮播图最少|轮播图.*3张/,
    human: "轮播图不足 3 张，Temu 硬性要求",
    fix: "给这条草稿补到至少 3 张图（成品库点编辑 → 图片区）",
  },
  {
    match: /服饰类目skc轮播图|3:4/,
    human: "服饰类目主图要 3:4 竖图，这张比例不对",
    fix: "只有本地成品图能自动裁；换成本地成品图，或用「发布商品」页单件发",
  },
  {
    match: /Detail graphic|2000306|楼层.*宽度|image width/i,
    human: "详情图宽度不统一（Temu 商详要求所有图等宽）",
    fix: "本地图片会自动缩放；还报就是有图是外链/图床地址，换成本地图片",
  },
  {
    match: /不合法的规格属性|规格属性/,
    human: "颜色/尺码的值不在类目预设里",
    fix: "变种属性的值要用预设（点预设芯片），不能自己造新值",
  },
  {
    match: /承诺发货时效|发货时效/,
    human: "这个类目不支持你选的发货时效档位",
    fix: "换一档发货时效（1 / 2 / 7 / 9 个工作日）再发",
  },
  {
    // 2026-11-12 实测踩到：中文规格名拼进货号 → Temu 直接拒（1000002）
    match: /货号不规范|字母、数字和标点符号维护货号|1000002/,
    human: "货号里有 Temu 不认的字符（中文、括号、空格这类）",
    fix: "去修这条 → 货号只留字母/数字/-/_；批量发布会自动清掉中文，单件发要自己改",
  },
  {
    match: /ext_?code.*重复|货号重复|SKC ext_code|SKU ext_code/,
    human: "同一批里货号撞了（几条用了同一个货号）",
    fix: "把重复的货号改掉：自动生成的是 SKU-001、SKU-002 递增",
  },
  {
    match: /最长边|体积内容|2000096/,
    human: "长宽高顺序不对（Temu 要求最长边 ≥ 次长边 ≥ 最短边）",
    fix: "系统会自动按长短排序；还报就检查三个边是不是有 0 或空值",
  },
  {
    match: /仓库|warehouse/i,
    human: "这个发货仓不支持该商品",
    fix: "换一个发货仓库（类型 3 的仓部分商品绑不了）",
  },
  {
    match: /重复提交/,
    human: "24 小时内提交过同一条，这条大概率已经在店铺里了",
    fix: "去发布历史确认；确实要再上一件，去「发布商品」页单条发",
  },
  {
    match: /价格|supplierPrice|售价/i,
    human: "申报价不合法（可能是 0、负数或小数位过多）",
    fix: "申报价要大于 0、最多两位小数",
  },
];

/**
 * 把失败原因翻成人话。翻不了的返回 null（调用方照原文显示，宁可不翻译也不瞎猜）。
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
 * 归堆 chips 上用的短标签：能翻译就用人话（还带错误码前缀的先剥掉），翻不了保留原文。
 */
export function shortReasonLabel(text: string): string {
  const explained = explainPublishError(text);
  const label = explained ? explained.human : String(text ?? "");
  return label.replace(/^bg\.[\w.]+:\s*/, "").trim();
}
