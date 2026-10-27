/**
 * 预检「本地拼装」配件卡片库。
 *
 * 第一版使用自建扁平 SVG 占位素材，版权可控、无需外部下载；后续可直接把
 * `imageUrl` 换成 CC0/自建 PNG，UI 与匹配逻辑不变。
 */

export type PropCard = {
  id: string;
  label: string;
  categoryIds: string[];
  tags: string[];
  colorFamily: string;
  scale: "small" | "medium" | "large";
  imageUrl: string;
};

function svg(source: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
}

function card(
  id: string,
  label: string,
  categoryIds: string[],
  tags: string[],
  colorFamily: string,
  scale: PropCard["scale"],
  source: string,
): PropCard {
  return {
    id,
    label,
    categoryIds,
    tags,
    colorFamily,
    scale,
    imageUrl: svg(source),
  };
}

export const PROP_CARDS: PropCard[] = [
  card(
    "notebook",
    "笔记本",
    ["stationery"],
    ["flat", "paper", "desk"],
    "warm",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect x="24" y="16" width="72" height="88" rx="10" fill="#e8c68f"/><rect x="32" y="16" width="64" height="88" rx="8" fill="#fff8ea"/><path d="M43 40h42M43 54h42M43 68h30" stroke="#d8b98c" stroke-width="4" stroke-linecap="round"/><rect x="24" y="16" width="10" height="88" rx="5" fill="#c99a5b"/></svg>`,
  ),
  card(
    "ceramic-bowl",
    "陶瓷碗",
    ["kitchen", "home"],
    ["scene", "table", "tableware"],
    "cream",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><ellipse cx="60" cy="58" rx="40" ry="14" fill="#eee7db"/><path d="M20 58c2 26 18 40 40 40s38-14 40-40" fill="#f8f4ec"/><ellipse cx="60" cy="58" rx="32" ry="9" fill="#e4dccd"/></svg>`,
  ),
  card(
    "linen-towel",
    "亚麻巾",
    ["kitchen", "home"],
    ["scene", "fabric", "table"],
    "cream",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect x="18" y="36" width="84" height="50" rx="8" fill="#efe6d6"/><path d="M18 50h84M18 64h84" stroke="#d9c9b2" stroke-width="3"/><path d="M28 36v50M92 36v50" stroke="#e2d4bf" stroke-width="2"/></svg>`,
  ),
  card(
    "pet-blanket",
    "宠物毯",
    ["pet"],
    ["scene", "fabric", "soft"],
    "cream",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect x="18" y="40" width="84" height="46" rx="12" fill="#e7dccb"/><path d="M18 56h84M18 70h84" stroke="#d3c2a8" stroke-width="3"/><circle cx="60" cy="52" r="5" fill="#c9a97e"/><circle cx="52" cy="46" r="3" fill="#c9a97e"/><circle cx="68" cy="46" r="3" fill="#c9a97e"/></svg>`,
  ),
  card(
    "steel-bowl",
    "钢碗",
    ["pet"],
    ["scene", "feeding", "metal"],
    "gray",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><ellipse cx="60" cy="58" rx="38" ry="13" fill="#c8ced2"/><path d="M22 58c2 24 16 38 38 38s36-14 38-38" fill="#dde2e5"/><ellipse cx="60" cy="58" rx="30" ry="9" fill="#aeb7bd"/></svg>`,
  ),
  card(
    "cosmetic-jar",
    "面霜罐",
    ["beauty"],
    ["scene", "jar", "small"],
    "cream",
    "small",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect x="32" y="48" width="56" height="44" rx="12" fill="#f8f4ec"/><rect x="28" y="34" width="64" height="18" rx="9" fill="#d9b98c"/><ellipse cx="60" cy="48" rx="28" ry="8" fill="#efe6d6"/></svg>`,
  ),
  card(
    "plant-pot",
    "花盆",
    ["home", "kitchen"],
    ["scene", "green", "plant"],
    "green",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><path d="M38 58h44l-7 44H45z" fill="#c98a5c"/><rect x="34" y="50" width="52" height="12" rx="6" fill="#b97a4c"/><path d="M60 50c-2-18-16-24-26-22 0 14 10 22 26 22zM62 50c4-16 18-20 28-16-2 12-12 18-28 16z" fill="#7b9e89"/></svg>`,
  ),
  card(
    "woven-basket",
    "编织篮",
    ["home"],
    ["scene", "storage", "woven"],
    "wood",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><path d="M28 46h64l-8 52H36z" fill="#d9b98c"/><path d="M28 46c0-14 10-22 32-22s32 8 32 22" fill="none" stroke="#b98548" stroke-width="7"/><path d="M34 62h52M32 76h56M36 90h48" stroke="#c49a68" stroke-width="4"/></svg>`,
  ),
  card(
    "wooden-block",
    "木块",
    ["general"],
    ["flat", "neutral", "medium"],
    "wood",
    "medium",
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect x="22" y="40" width="76" height="44" rx="8" fill="#c99a5b"/><path d="M22 52h76M40 40v44M80 40v44" stroke="#b38145" stroke-width="4"/></svg>`,
  ),
];

const CATEGORY_KEYWORDS: Record<string, string[]> = {
  stationery: [
    "文具", "笔袋", "文具盒", "铅笔", "钢笔", "橡皮", "尺子", "笔记本", "便签",
    "pencil case", "stationery", "notebook", "pen", "eraser", "ruler",
  ],
  kitchen: [
    "厨房", "餐具", "碗", "盘", "勺", "杯", "水杯", "收纳罐", "保鲜",
    "kitchen", "bowl", "spoon", "cup", "tableware", "jar",
  ],
  pet: [
    "宠物", "猫", "狗", "项圈", "牵引", "窝", "宠物碗", "咬胶",
    "pet", "dog", "cat", "collar", "leash", "pet bed", "chew",
  ],
  beauty: [
    "美妆", "化妆", "刷", "梳", "指甲", "面霜", "个护",
    "beauty", "makeup", "brush", "comb", "nail", "cosmetic",
  ],
  home: [
    "家居", "收纳", "装饰", "花盆", "蜡烛", "摆件", "编织",
    "home", "decor", "storage", "plant", "candle", "basket",
  ],
};

export function detectPropCategory(categoryText: string): string {
  const haystack = String(categoryText || "").toLowerCase();
  if (!haystack) return "general";
  let best = "general";
  let bestHits = 0;
  for (const [categoryId, keywords] of Object.entries(CATEGORY_KEYWORDS)) {
    const hits = keywords.filter((keyword) => haystack.includes(keyword.toLowerCase())).length;
    if (hits > bestHits) {
      best = categoryId;
      bestHits = hits;
    }
  }
  return best;
}

const TEMPLATE_TAG_PRIORITY: Record<string, string[]> = {
  headline: ["flat", "paper", "small", "writing", "measure"],
  spec: ["flat", "measure", "paper", "tool", "small"],
  scene: ["scene", "fabric", "table", "desk", "warm", "green", "wood"],
};

export function matchPropCards(
  categoryText: string,
  templateId: string,
  limit = 6,
): PropCard[] {
  const categoryId = detectPropCategory(categoryText);
  const priorities = TEMPLATE_TAG_PRIORITY[templateId] ?? [];
  const scored = PROP_CARDS
    .filter(
      (item) =>
        item.categoryIds.includes(categoryId) ||
        item.categoryIds.includes("general") ||
        categoryId === "general",
    )
    .map((item) => {
      let score = item.categoryIds.includes(categoryId) ? 6 : 1;
      for (const tag of item.tags) {
        const index = priorities.indexOf(tag);
        if (index >= 0) score += Math.max(1, priorities.length - index);
      }
      if (item.scale === "small") score += 1;
      return { item, score };
    })
    .sort((a, b) => b.score - a.score || a.item.label.localeCompare(b.item.label));
  const picked = scored.slice(0, Math.max(0, limit)).map((entry) => entry.item);
  if (picked.length >= limit) return picked;
  for (const item of PROP_CARDS) {
    if (!picked.some((entry) => entry.id === item.id)) picked.push(item);
    if (picked.length >= limit) break;
  }
  return picked;
}

export function propCardById(id: string): PropCard | undefined {
  return PROP_CARDS.find((item) => item.id === id);
}
