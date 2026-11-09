/**
 * 系统背景模板库（20 张）。
 *
 * 与后端 `wh_local/modules/product_processing/domain/background_presets.py` 保持一致：
 * - 前 13 张是纯色/纸感/木石/布艺，主图与轮播都能用；
 * - 后 7 张（拱形墙洞、阶梯、节庆）只用于非主图，避免主图抢戏。
 *
 * 当前 `asset` 指向 1200×1200 JPG 底板（豆包四宫格生成、裁掉底部水印后切分），
 * 换成实拍图时同 id 覆盖即可，选择逻辑不变。
 */

export type BackgroundStyleFamily = 'solid' | 'paper' | 'wood_stone' | 'fabric' | 'geo' | 'festive';

export type BackgroundTemplate = {
  id: string;
  name: string;
  styleFamily: BackgroundStyleFamily;
  colorFamily: 'warm' | 'cool' | 'neutral' | 'festive';
  /** 空数组=全品类可用。 */
  categories: string[];
  usableForMain: boolean;
};

export const SYSTEM_BACKGROUNDS: BackgroundTemplate[] = [
  { id: 'bg_warm_white', name: '暖白渐变', styleFamily: 'solid', colorFamily: 'warm', categories: [], usableForMain: true },
  { id: 'bg_fog_gray', name: '雾灰渐变', styleFamily: 'solid', colorFamily: 'cool', categories: [], usableForMain: true },
  { id: 'bg_oatmeal', name: '燕麦纸感', styleFamily: 'solid', colorFamily: 'warm', categories: [], usableForMain: true },
  { id: 'bg_pale_apricot', name: '浅杏渐变', styleFamily: 'solid', colorFamily: 'warm', categories: [], usableForMain: true },
  { id: 'bg_cool_gray', name: '冷灰渐变', styleFamily: 'solid', colorFamily: 'cool', categories: [], usableForMain: true },
  { id: 'bg_paper_ivory', name: '象牙纸纹', styleFamily: 'paper', colorFamily: 'warm', categories: [], usableForMain: true },
  { id: 'bg_plaster_warm', name: '暖灰石膏', styleFamily: 'paper', colorFamily: 'warm', categories: ['home_kitchen', 'pet_supplies', 'general'], usableForMain: true },
  { id: 'bg_paper_coarse', name: '粗纹灰纸', styleFamily: 'paper', colorFamily: 'neutral', categories: ['office_stationery', 'general'], usableForMain: true },
  { id: 'bg_oak_light', name: '浅橡木台面', styleFamily: 'wood_stone', colorFamily: 'warm', categories: ['home_kitchen', 'hand_tools', 'garden_tools'], usableForMain: true },
  { id: 'bg_walnut', name: '胡桃木台面', styleFamily: 'wood_stone', colorFamily: 'warm', categories: ['adult_games', 'garden_tools'], usableForMain: true },
  { id: 'bg_stone_warm', name: '暖灰石板', styleFamily: 'wood_stone', colorFamily: 'neutral', categories: ['hand_tools', 'sports_outdoor'], usableForMain: true },
  { id: 'bg_terrazzo', name: '暖色水磨石', styleFamily: 'wood_stone', colorFamily: 'neutral', categories: ['beauty_tools', 'office_stationery'], usableForMain: true },
  { id: 'bg_marble_white', name: '白大理石', styleFamily: 'wood_stone', colorFamily: 'cool', categories: ['beauty_tools'], usableForMain: true },
  { id: 'bg_linen', name: '米白亚麻', styleFamily: 'fabric', colorFamily: 'warm', categories: ['apparel_accessories'], usableForMain: true },
  { id: 'bg_canvas', name: '棉麻画布', styleFamily: 'fabric', colorFamily: 'warm', categories: ['apparel_accessories', 'home_kitchen'], usableForMain: true },
  { id: 'bg_velvet', name: '深灰绿绒面', styleFamily: 'fabric', colorFamily: 'cool', categories: ['beauty_tools', 'adult_games'], usableForMain: true },
  { id: 'bg_arch', name: '拱形墙洞', styleFamily: 'geo', colorFamily: 'warm', categories: ['home_kitchen', 'beauty_tools'], usableForMain: false },
  { id: 'bg_steps', name: '石质阶梯', styleFamily: 'geo', colorFamily: 'neutral', categories: ['home_kitchen', 'passive_tech'], usableForMain: false },
  { id: 'bg_pine', name: '松枝暖灯', styleFamily: 'festive', colorFamily: 'festive', categories: ['adult_games'], usableForMain: false },
  { id: 'bg_gift_velvet', name: '礼盒丝绒', styleFamily: 'festive', colorFamily: 'festive', categories: ['adult_games', 'beauty_tools'], usableForMain: false },
];

export const BACKGROUND_ASSET_PREFIX = '/assets/backgrounds';

export function backgroundAssetUrl(templateId: string, ext = 'jpg'): string {
  return `${BACKGROUND_ASSET_PREFIX}/${templateId}.${ext}`;
}

export function backgroundById(templateId: string): BackgroundTemplate | undefined {
  return SYSTEM_BACKGROUNDS.find((item) => item.id === templateId);
}

/** 主图可用、且适配该品类的模板；品类不在白名单时退回通用模板。 */
export function backgroundsForCategory(categoryId: string, forMain = true): BackgroundTemplate[] {
  const pool = SYSTEM_BACKGROUNDS.filter((item) => (forMain ? item.usableForMain : true));
  const matched = pool.filter((item) => item.categories.length === 0 || item.categories.includes(categoryId));
  return matched.length ? matched : pool;
}

function stableIndex(seed: string, size: number): number {
  let hash = 2166136261;
  for (let index = 0; index < seed.length; index += 1) {
    hash ^= seed.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash) % Math.max(size, 1);
}

/** 与后端同规则：同店铺同批次稳定，跨批次轮换。 */
export function pickBackgroundTemplate(input: {
  shopId: string;
  batchId: string;
  panel?: number;
  categoryId?: string;
  styleFamily?: BackgroundStyleFamily;
}): BackgroundTemplate | null {
  const panel = input.panel ?? 1;
  let pool = backgroundsForCategory(input.categoryId || 'general', panel === 1);
  if (input.styleFamily) {
    const filtered = pool.filter((item) => item.styleFamily === input.styleFamily);
    pool = filtered.length ? filtered : pool;
  }
  if (!pool.length) return null;
  return pool[stableIndex(`${input.shopId}:${input.batchId}:${panel}`, pool.length)];
}
