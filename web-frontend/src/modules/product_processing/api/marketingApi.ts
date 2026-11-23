import { ppRequest, type ApiContext } from './client';

const API_BASE = '/api/product-processing';

export type MarketingTemplate = 'headline_hero' | 'action_proof';

export const MARKETING_TEMPLATES: Array<{ key: MarketingTemplate; label: string; hint: string }> = [
  { key: 'headline_hero', label: '大字卖点', hint: '顶部大标题 + 副文案 + 产品图（对标高端 listing 首图）' },
  { key: 'action_proof', label: '场景动作', hint: '使用动作特写 + 左上卖点标题（卖点实证）' },
];

export type MarketingResult = {
  draft_id: number;
  template: MarketingTemplate;
  headline: string;
  subline: string;
  image_path: string;
  message?: string;
};

/** 精选处理独立生成：一个草稿一个版式一张营销图（每张 = 1 次生图计费） */
export function generateMarketingImage(
  ctx: ApiContext,
  draftId: number,
  template: MarketingTemplate
): Promise<MarketingResult> {
  return ppRequest<MarketingResult>(ctx, `${API_BASE}/marketing/generate`, {
    body: { draft_id: draftId, template },
  });
}
