/**
 * 标题 → 类目匹配的查询词与结果整理。
 *
 * Temu 的 `category.match` 对「整条标题」和「只取前几个词」都不稳：整条标题会把
 * 逗号后的卖点/用途描述当成商品（实测匹配出儿童安全座椅、维生素B6）；只取前 4~6
 * 个词又会丢掉商品名 —— POD 的标题开头全是颜色和花纹（"Sage Green Hydrangea Daisy
 * Print Natural Rubber…"），前 6 个词只能匹配到毯子、抱枕，而真正的主体 "tablecloth"
 * 在后面。
 *
 * 所以查询词按「信息量」取两段：
 *   1. 主体：截到第一个逗号前，再去掉 ` for …` 用途说明（商品名基本都在 for 前面）
 *   2. 兜底：整段逗号前的标题（主体太短时才用得上，比如 "Phone Case for iPhone"）
 * 实测（桌布 / 化妆包 / 假发 / 手机壳 / 毯子）主体段的 top1 全部命中正确类目。
 */

const HEAD_CUT = /[,，;；|]/;
const FOR_CUT = /\s+for\s+/i;
/** 网关对超长查询会返回 SYSTEM_EXCEPTION，统一截到 120 字符。 */
const MAX_QUERY_CHARS = 120;

/** 把标题拆成 1~2 个用于匹配的查询（第一个是最可信的）。 */
export function categorySearchTexts(title: string): string[] {
  const raw = String(title ?? "").trim();
  if (!raw) return [];

  // 中文标题本身就是短句，直接整句匹配
  if (/[\u4e00-\u9fa5]/.test(raw)) return [raw.slice(0, MAX_QUERY_CHARS)];

  const head = (raw.split(HEAD_CUT)[0] || raw).trim();
  const core = (head.split(FOR_CUT)[0] || head).trim();
  const queries: string[] = [];
  const push = (text: string) => {
    const capped = text.slice(0, MAX_QUERY_CHARS).trim();
    if (capped.length >= 3 && !queries.includes(capped)) queries.push(capped);
  };
  const coreWords = core.split(/\s+/).filter(Boolean).length;
  if (coreWords >= 2 || head === core) push(core);
  // 主体太短（"Case for iPhone 15" 只剩一个词）时改用整段逗号前标题：
  // 主体够长就只查一次 —— 多一次请求就多 1~2 秒，长标题的尾巴还会把结果带偏。
  if (head !== core && coreWords < 3) push(head);
  if (!queries.length && head) push(head);
  return queries;
}

/** 按类目 id 路径去重，保留先后顺序。 */
export function dedupeCategoryPaths<T extends Record<string, unknown>>(rows: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const row of rows) {
    const key = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
      .map((index) => {
        const item = row[`cat${index}DTO`] as { catId?: number } | null | undefined;
        return item?.catId ?? "";
      })
      .join("-");
    if (!key || key === "----------" || seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}
