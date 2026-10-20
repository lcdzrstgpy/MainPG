/**
 * 拼装用短标题/宣传语。
 *
 * 商品标题通常很长，直接放进拼装会换行或截断。这里优先从标题提取一个
 * 可读短句；标题过长时用品类宣传语兜底。只用于本地拼装的画面文案，
 * 不改变导出标题。
 */

const CATEGORY_SLOGANS: Record<string, string[]> = {
  stationery: ['Big Capacity, One Case', 'Layer Up, Learn Smarter', 'Cute Look, Ready to Go'],
  kitchen: ['Tidy Kitchen, Easy Living', 'Made for Everyday Cooking'],
  pet: ['Comfort They Deserve', 'Easy Care, Happy Days'],
  beauty: ['Precision in Every Detail', 'Clean Routine, Better Look'],
  home: ['Order Made Simple', 'Details That Matter'],
  sports: ['Ready for Every Move', 'Built for the Outdoors'],
  tools: ['Built to Last', 'Precision You Can Feel'],
  tech: ['Perfect Fit, Clean Look', 'Protection in Every Detail'],
  games: ['Game Night, Upgraded', 'Fun in Every Piece'],
  garden: ['Grow Something Beautiful', 'Fresh Green, Every Day'],
  auto: ['Clean Fit, Everyday Drive', 'Details for the Road'],
  general: ['Details That Matter'],
};

const CATEGORY_KEYWORDS: Record<string, string[]> = {
  stationery: ['文具', '笔袋', '文具盒', '铅笔', 'pencil', 'stationery', 'notebook'],
  kitchen: ['厨房', '餐具', '碗', '盘', '勺', 'kitchen', 'bowl', 'spoon'],
  pet: ['宠物', '猫', '狗', 'pet', 'dog', 'cat', 'collar', 'leash'],
  beauty: ['美妆', '化妆', '刷', '梳', 'beauty', 'makeup', 'brush', 'comb'],
  home: ['家居', '收纳', '装饰', 'home', 'storage', 'decor'],
  sports: ['运动', '瑜伽', '健身', '露营', 'sport', 'yoga', 'gym', 'camping'],
  tools: ['工具', '五金', '螺丝', 'tool', 'wrench', 'screwdriver'],
  tech: ['手机', '平板', '电脑', '数据线', 'phone', 'tablet', 'laptop', 'cable'],
  games: ['桌游', '扑克', '拼图', 'game', 'puzzle', 'card'],
  garden: ['园艺', '花盆', '植物', 'garden', 'plant', 'pot'],
  auto: ['汽车', '车载', 'car', 'auto', 'vehicle'],
};

function detectCategory(text: string): string {
  const haystack = String(text || '').toLowerCase();
  let best = 'general';
  let bestHits = 0;
  for (const [category, keywords] of Object.entries(CATEGORY_KEYWORDS)) {
    const hits = keywords.filter((keyword) => haystack.includes(keyword.toLowerCase())).length;
    if (hits > bestHits) {
      best = category;
      bestHits = hits;
    }
  }
  return best;
}

function firstReadableClause(title: string): string {
  const cleaned = String(title || '').replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';
  // English: cut at the first comma / semicolon / dash, then cap at 7 words.
  const firstClause = cleaned.split(/[,;，；|–—]/)[0].trim();
  const words = firstClause.split(' ');
  const short = words.slice(0, 7).join(' ');
  // Chinese: cap at 16 characters.
  if (/[\u4e00-\u9fff]/.test(short)) return short.slice(0, 16);
  return short.length > 44 ? `${short.slice(0, 41).trimEnd()}…` : short;
}

export function compositionSlogan(title: string, categoryPath = ''): string {
  const category = detectCategory(`${categoryPath} ${title}`);
  const fromTitle = firstReadableClause(title);
  if (fromTitle.length >= 8) return fromTitle;
  const slogans = CATEGORY_SLOGANS[category] ?? CATEGORY_SLOGANS.general;
  return slogans[0] ?? 'Details That Matter';
}

export function compositionSubtitle(categoryPath = ''): string {
  const category = detectCategory(categoryPath);
  const labels: Record<string, string> = {
    stationery: 'STATIONERY',
    kitchen: 'KITCHEN',
    pet: 'PET SUPPLIES',
    beauty: 'BEAUTY TOOLS',
    home: 'HOME',
    sports: 'SPORTS & OUTDOOR',
    tools: 'HAND TOOLS',
    tech: 'TECH ACCESSORIES',
    games: 'GAME NIGHT',
    garden: 'GARDEN',
    auto: 'AUTO ACCESSORIES',
    general: 'PRODUCT DETAILS',
  };
  return labels[category] ?? 'PRODUCT DETAILS';
}
