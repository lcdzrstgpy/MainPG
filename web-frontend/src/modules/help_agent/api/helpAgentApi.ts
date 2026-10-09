import { httpJson } from "../../../transport/http/client";

export type HelpAgentBootstrap = {
  faq_count: number;
  version: number;
  data_path: string;
};

export type HelpAgentCandidate = {
  faq_id: string;
  question: string;
  category: string;
};

export type HelpAgentHit = {
  type: "hit";
  faq_id: string;
  question: string;
  answer: string;
  category: string;
  matched_layer: string;
};

export type HelpAgentCandidates = {
  type: "candidates";
  candidates: HelpAgentCandidate[];
  matched_layer: string;
};

export type HelpAgentFallback = {
  type: "fallback";
  matched_layer: string;
};

export type HelpAgentSearchResult = HelpAgentHit | HelpAgentCandidates | HelpAgentFallback;

export type HelpAgentAnswer = {
  faq_id: string;
  question: string;
  answer: string;
  category: string;
};

export const helpAgentApi = {
  bootstrap: () => httpJson<HelpAgentBootstrap>("/api/help-agent/bootstrap"),
  search: (question: string) =>
    httpJson<HelpAgentSearchResult>("/api/help-agent/search", {
      method: "POST",
      body: { question },
    }),
  /** 用户点选候选后才取答案，候选阶段接口不下发答案。 */
  confirm: (faqId: string) =>
    httpJson<HelpAgentAnswer>("/api/help-agent/confirm", {
      method: "POST",
      body: { faq_id: faqId },
    }),
};

/** FAQ 分类的中文展示名。 */
export const helpAgentCategoryNames: Record<string, string> = {
  system: "系统操作",
  business: "业务问题",
  account: "账号与积分",
  workflow: "流程与策略",
};

/**
 * 取分类的展示文案。
 *
 * `faqs.json` 里的 `category` 本来就写成了用户看得懂的中文（如「产品处理 · SKU规格图」
 * 「产品库 · 查询」），直接原样展示即可；上面那张表只兜住早期用英文枚举写的那批数据。
 *
 * ⚠️ 别在这里枚举全部分类：分类是数据侧的字段，新增分类时不该再改前端代码 ——
 * 之前正是因为只列了 system/business/account/workflow 四个键，而数据里一个都没用到，
 * 结果每条 FAQ 的标签都退化成「常见问题」。
 */
export function categoryLabel(category: string): string {
  const key = category.trim();
  if (!key) return "常见问题";
  return helpAgentCategoryNames[key] ?? key;
}
