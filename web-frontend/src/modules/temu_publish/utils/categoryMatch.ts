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
 *
 * 2026-11 实测发现英文标题还有个大坑：托特包被匹配到隐藏类目（属性问卷是「木材类型」
 * 这种无关题），答错问卷后属性全是瞎填。所以查询词升级成「采集属性锚定优先」：
 * 属性里的中文品类词（单肩斜挎包）比英文标题可靠得多，网关对中文查询很稳。
 */

import { PLACEHOLDER_VALUE } from "../data/attrAutofill.ts";
import type { TemuCategory } from "../types.ts";

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

/* ------------------------------------------------------------------ */
/* 属性锚定 + 多查询互验（2026-11 类目配错修复）                        */
/* ------------------------------------------------------------------ */

/** 属性名带这些词的值才是品类词（「单肩斜挎包」）；「材质类型=PU」这类不算 */
const ANCHOR_NAME = /款式|品类|品名|种类/;
/** 品类词至少两个字：「大/中/小」这类尺寸词是噪音 */
const ANCHOR_MIN_LEN = 2;

/**
 * 从采集属性里挑「品类查询词」。
 *
 * 实测样本：托特包的 `箱包潮流款式=单肩斜挎包` 拿去打 category.match，
 * 比整条英文标题靠谱一个量级。多值（"黑色,浅棕色"）拆开取，够长的才算。
 */
export function categoryAnchorTexts(
  sourceAttributes?: Array<{ name: string; value: string }>,
): string[] {
  const out: string[] = [];
  for (const attr of sourceAttributes ?? []) {
    if (!ANCHOR_NAME.test(String(attr?.name ?? ""))) continue;
    for (const part of String(attr?.value ?? "").split(/[,，、;；/]/)) {
      const text = part.trim();
      if (text.length < ANCHOR_MIN_LEN) continue;
      if (PLACEHOLDER_VALUE.test(text)) continue;
      if (!out.includes(text)) out.push(text);
    }
  }
  return out.slice(0, 2);
}

export type CategoryQuery = { text: string; kind: "anchor" | "title" };

/** 一条草稿的查询计划：锚定词优先（中文品类词），标题兜底，最多 3 个查询。 */
export function categoryQueryPlan(input: {
  title?: string;
  sourceAttributes?: Array<{ name: string; value: string }>;
}): CategoryQuery[] {
  const plan: CategoryQuery[] = [];
  const seen = new Set<string>();
  const push = (raw: string, kind: "anchor" | "title") => {
    const text = raw.trim().slice(0, MAX_QUERY_CHARS);
    if (text.length < 2 || seen.has(text)) return;
    seen.add(text);
    plan.push({ text, kind });
  };
  for (const anchor of categoryAnchorTexts(input.sourceAttributes)) push(anchor, "anchor");
  for (const text of categorySearchTexts(input.title ?? "")) push(text, "title");
  return plan.slice(0, 3);
}

export type CategoryQueryResult = { kind: "anchor" | "title"; paths: TemuCategory[][] };

/**
 * 多查询结果裁决（纯函数），按证据强度排：
 *
 * 1. **跨证据源共识**（锚定 + 标题指向同一叶子）：最硬，认领；
 * 2. **锚定 top1**：中文品类词 > 英文标题的猜测 —— 同一条标题切出来的多个查询
 *    是同源自查，一致了也不算互证，压不过采集属性里的品类词；
 * 3. 标题查询内部 ≥2 一致：认领；
 * 4. 只有一个查询有结果：认领（维持原有覆盖率）；
 * 5. 冲突且没有锚定 → **不认领**（宁缺勿错，绝不硬塞）。
 *
 * 隐藏类目一律不当候选：实测 29158 连类目树都不在，认领它等于答错问卷。
 */
export function decideCategoryPath(results: CategoryQueryResult[]): TemuCategory[] | null {
  const usable = results.map((result) => ({
    kind: result.kind,
    paths: result.paths.filter((path) => {
      const leaf = path[path.length - 1];
      return Boolean(leaf?.isLeaf && !leaf.isHidden);
    }),
  }));
  const counts = new Map<string, { path: TemuCategory[]; anchor: number; title: number }>();
  for (const result of usable) {
    for (const path of result.paths) {
      const key = String(path[path.length - 1].catId);
      const hit = counts.get(key);
      if (hit) {
        if (result.kind === "anchor") hit.anchor += 1;
        else hit.title += 1;
      } else {
        counts.set(key, {
          path,
          anchor: result.kind === "anchor" ? 1 : 0,
          title: result.kind === "title" ? 1 : 0,
        });
      }
    }
  }
  // ① 跨证据源共识：锚定 + 标题都指向同一个叶子
  const cross = [...counts.values()].find((item) => item.anchor > 0 && item.title > 0);
  if (cross) return cross.path;
  // ② 锚定 top1（中文品类词 > 英文标题的猜测）
  const anchorTop = usable.find((result) => result.kind === "anchor")?.paths[0];
  if (anchorTop) return anchorTop;
  // ③ 标题查询内部 ≥2 一致
  const majority = [...counts.values()].find((item) => item.title >= 2);
  if (majority) return majority.path;
  // ④ 只有一个查询有结果：认领（维持原有覆盖率）
  const withResults = usable.filter((result) => result.paths.length > 0);
  if (withResults.length === 1) return withResults[0].paths[0];
  // ⑤ 冲突且没有锚定：不硬认领
  return null;
}

/**
 * 端到端的「查询计划 → 打 category.match → 裁决」由调用方拼装
 * （api 层依赖不进这里，保持本文件纯函数可测）。见 TemuDraftLibraryPage 里的
 * `matchEntryCategoryPath`。
 */
