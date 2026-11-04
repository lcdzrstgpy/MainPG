/**
 * 品类场景（Scene）与零件（Set）预设。
 *
 * 只负责「按品类把描述拼好」这一层，不触发生图：
 * - Scene 只写台面 / 背景 / 景深，光位统一交给 L1-L5 摄影卡片；
 * - 零件数量跟着场景风格走：极简 1 件、生活 3 件、智能 4 件、花哨最多 6 件；
 * - 不允许同材质、同功能重复；主体材质 / 颜色冲突时先改件、改不了就删件。
 *
 * 分类 id 与 imageCardOptions.ts 的 ImageCategoryId 共用（去掉 auto）。
 */

import type { ImageCategoryId } from './imageCardOptions';

export type SceneCategoryId = Exclude<ImageCategoryId, 'auto'>;

export type ScenePreset = { main: string; alt: string };

/** 与批次设置里的「场景风格」一致（sceneStyle）。 */
export type SceneStyle = 'smart' | 'minimal' | 'lifestyle' | 'ornate';

/** 场景风格 → 零件上限。花哨档是最丰富的一档，封顶 6 件。 */
export const PROP_STYLE_LIMITS: Record<SceneStyle, number> = {
  minimal: 1,
  lifestyle: 3,
  smart: 4,
  ornate: 6,
};

/** 全局硬上限：任何风格都不超过 6 件。 */
export const MAX_PROPS = PROP_STYLE_LIMITS.ornate;

/** 默认按「生活」档给 3 件，和现有出图习惯一致。 */
export const DEFAULT_SCENE_STYLE: SceneStyle = 'lifestyle';

/** 提交给生成模型时，主体识别能给出的线索（都可缺省）。 */
export type SceneSubject = {
  /** 商品标题或主体名称，用来判断「商品本体就是某零件」。 */
  title?: string;
  /** 主体材质描述（木、陶瓷、布、植物……），中英文都行。 */
  material?: string;
  /** 主体颜色族，与 PROP_COLOR_FAMILY 对齐时可命中「同色系删件」。 */
  colorFamily?: string;
};

export type ResolvePropOptions = {
  /** 场景风格；缺省 lifestyle。 */
  style?: SceneStyle;
  /** 直接指定候选零件（人工调整过时用），覆盖品类池。 */
  props?: readonly string[];
};

/** 拼装参数：在零件解析之外，再加摆位与素材两种渲染方式。 */
export type BuildSceneLineOptions = ResolvePropOptions & {
  variant?: 'main' | 'alt';
  /** 给零件加定格摆位（upper-left / lower-right…），默认开启。 */
  placements?: boolean;
  /** 全部零件都有抠图素材时，用简短指代替代逐个描述，默认自动判断。 */
  assets?: boolean;
};

/**
 * 零件固定摆位：按顺序分配，避免 6 件全挤在主体周围。
 * 用不带方向的「上/下/左/右」表述，俯拍与 45° 都成立。
 */
export const PROP_PLACEMENTS = [
  'upper-left',
  'lower-right',
  'upper-right',
  'along-left',
  'along-right',
  'upper-center',
] as const;

/** 已有抠图素材的零件 → 素材路径。填满之后即可走「贴图」模式，提示词里不再逐个描述。 */
export const PROP_ASSETS: Record<string, string> = {};

function renderPropTokens(
  props: readonly string[],
  options: BuildSceneLineOptions,
): string[] {
  const useAssets =
    options.assets !== false
    && props.length > 0
    && props.every((prop) => Boolean(PROP_ASSETS[prop]));
  if (useAssets) return ['supplied prop cutouts'];
  const withPlacement = options.placements !== false;
  return props.map((prop, index) => {
    if (!withPlacement) return prop;
    const placement = PROP_PLACEMENTS[index];
    return placement ? `${placement} ${prop}` : prop;
  });
}

export const SCENE_PRESETS: Record<SceneCategoryId, ScenePreset> = {
  home_kitchen: {
    main: 'warm oak countertop, warm gray plaster wall, shallow depth',
    alt: 'linen-draped shelf, soft white wall, mid depth',
  },
  apparel_accessories: {
    main: 'pale oak bench, warm white wall, shallow depth',
    alt: 'stone ledge, soft gray backdrop, shallow depth',
  },
  pet_supplies: {
    main: 'low wool rug on light wood floor, soft white wall, low camera',
    alt: 'entryway tile, warm gray wall, low camera',
  },
  office_stationery: {
    main: 'light oak desk, warm gray paper backdrop, strict top-down',
    alt: 'white laminate desk, pale gray wall, slight top-down',
  },
  sports_outdoor: {
    main: 'pale oak deck, muted greenery backdrop, mid depth',
    alt: 'concrete floor, overcast gray backdrop, low angle',
  },
  auto_accessories: {
    main: 'charcoal trunk carpet, dark gray trim, tight crop',
    alt: 'garage concrete, graphite wall, tight crop',
  },
  hand_tools: {
    main: 'oiled workbench, gray pegboard wall, mid depth',
    alt: 'concrete floor, plaster wall point, low angle',
  },
  passive_tech: {
    main: 'matte white desk, charcoal backdrop, shallow depth',
    alt: 'charcoal felt surface, light gray backdrop, top-down',
  },
  beauty_tools: {
    main: 'white marble vanity, pale pink-gray wall, shallow depth',
    alt: 'acrylic shelf, clean white backdrop, top-down',
  },
  adult_games: {
    main: 'walnut table, deep green felt inlay, warm dark backdrop',
    alt: 'dark leather side table, charcoal wall, top-down',
  },
  garden_tools: {
    main: 'weathered potting bench, mossy stone backdrop, mid depth',
    alt: 'dark soil bed edge, painted railing backdrop, low angle',
  },
  general: {
    main: 'neutral plaster corner, pale gray paper backdrop, soft floor transition, shallow depth',
    alt: 'neutral seamless sweep, soft gray gradient backdrop, top-down',
  },
};

/**
 * 每类的默认 3 件（生活档），顺序即优先级：
 * 1 垫高 / 结构，2 承载 / 质感，3 点缀 / 颜色。
 */
export const PROP_PRESETS: Record<SceneCategoryId, readonly string[]> = {
  home_kitchen: ['walnut cutting board', 'white ceramic dish', 'eucalyptus sprig'],
  apparel_accessories: ['oak hanger', 'washed linen cloth', 'brass clip'],
  pet_supplies: ['woven floor mat', 'white ceramic dish', 'eucalyptus sprig'],
  office_stationery: ['warm gray paper stack', 'light oak block', 'brass clip'],
  sports_outdoor: ['rolled yoga mat', 'washed linen cloth', 'warm gray stone slab'],
  auto_accessories: ['microfiber towel', 'warm gray stone slab', 'brass clip'],
  hand_tools: ['pegboard square', 'light oak block', 'warm gray stone slab'],
  passive_tech: ['felt sleeve', 'light oak block', 'brass clip'],
  beauty_tools: ['acrylic tray', 'washed linen cloth', 'frosted glass cup'],
  adult_games: ['green felt mat', 'light oak block', 'brass clip'],
  garden_tools: ['terracotta pot', 'kraft paper sheet', 'eucalyptus sprig'],
  general: ['light oak block', 'washed linen cloth', 'warm gray stone slab'],
};

/**
 * 每类的完整候选池（花哨档会按顺序取到 6 件）。
 * 前 3 件与 PROP_PRESETS 一致，后面的都是不同材质族的补充件。
 */
export const PROP_POOLS: Record<SceneCategoryId, readonly string[]> = {
  home_kitchen: [
    'walnut cutting board',
    'white ceramic dish',
    'eucalyptus sprig',
    'warm gray stone slab',
    'kraft paper sheet',
    'rattan storage box',
  ],
  apparel_accessories: [
    'oak hanger',
    'washed linen cloth',
    'brass clip',
    'warm gray stone slab',
    'kraft paper sheet',
    'frosted glass cup',
  ],
  pet_supplies: [
    'woven floor mat',
    'white ceramic dish',
    'eucalyptus sprig',
    'light oak block',
    'warm gray stone slab',
    'rattan storage box',
  ],
  office_stationery: [
    'warm gray paper stack',
    'light oak block',
    'brass clip',
    'white ceramic dish',
    'cork coaster',
    'frosted glass cup',
  ],
  sports_outdoor: [
    'rolled yoga mat',
    'washed linen cloth',
    'warm gray stone slab',
    'light oak block',
    'brass clip',
    'kraft paper sheet',
  ],
  auto_accessories: [
    'microfiber towel',
    'warm gray stone slab',
    'brass clip',
    'light oak block',
    'kraft paper sheet',
    'dark leather tray',
  ],
  hand_tools: [
    'pegboard square',
    'light oak block',
    'warm gray stone slab',
    'concrete cube',
    'kraft paper sheet',
    'washed linen cloth',
  ],
  passive_tech: [
    'felt sleeve',
    'light oak block',
    'brass clip',
    'warm gray stone slab',
    'kraft paper sheet',
    'frosted glass cup',
  ],
  beauty_tools: [
    'acrylic tray',
    'washed linen cloth',
    'frosted glass cup',
    'white ceramic dish',
    'cork coaster',
    'pale marble coaster',
  ],
  adult_games: [
    'green felt mat',
    'light oak block',
    'brass clip',
    'warm gray stone slab',
    'kraft paper sheet',
    'matte black ceramic cup',
  ],
  garden_tools: [
    'terracotta pot',
    'kraft paper sheet',
    'eucalyptus sprig',
    'warm gray stone slab',
    'light oak block',
    'rattan storage box',
  ],
  general: [
    'light oak block',
    'washed linen cloth',
    'warm gray stone slab',
    'white ceramic dish',
    'brass clip',
    'kraft paper sheet',
  ],
};

/** 零件材质族：同类只允许出现一件。 */
export const PROP_MATERIAL_FAMILY: Record<string, string> = {
  'light oak block': 'wood',
  'walnut cutting board': 'wood',
  'oak hanger': 'wood',
  'white ceramic dish': 'ceramic',
  'terracotta pot': 'ceramic',
  'matte black ceramic cup': 'ceramic',
  'acrylic tray': 'acrylic',
  'warm gray stone slab': 'stone',
  'pale marble coaster': 'stone',
  'washed linen cloth': 'fabric',
  'woven floor mat': 'fabric',
  'green felt mat': 'fabric',
  'felt sleeve': 'fabric',
  'microfiber towel': 'fabric',
  'kraft paper sheet': 'paper',
  'warm gray paper stack': 'paper',
  'brass clip': 'metal',
  'pegboard square': 'metal',
  'eucalyptus sprig': 'plant',
  'frosted glass cup': 'glass',
  'rolled yoga mat': 'foam',
  'cork coaster': 'cork',
  'concrete cube': 'concrete',
  'dark leather tray': 'leather',
  'rattan storage box': 'rattan',
};

/** 零件颜色族：与主体同色系时直接删件（不替换）。 */
export const PROP_COLOR_FAMILY: Record<string, string> = {
  'light oak block': 'wood',
  'walnut cutting board': 'wood',
  'oak hanger': 'wood',
  'white ceramic dish': 'white',
  'acrylic tray': 'clear',
  'frosted glass cup': 'clear',
  'warm gray stone slab': 'gray',
  'pale marble coaster': 'gray',
  'microfiber towel': 'gray',
  'pegboard square': 'gray',
  'concrete cube': 'gray',
  'washed linen cloth': 'ivory',
  'woven floor mat': 'sand',
  'green felt mat': 'green',
  'felt sleeve': 'charcoal',
  'rolled yoga mat': 'charcoal',
  'matte black ceramic cup': 'charcoal',
  'dark leather tray': 'charcoal',
  'kraft paper sheet': 'kraft',
  'warm gray paper stack': 'kraft',
  'brass clip': 'brass',
  'eucalyptus sprig': 'green',
  'terracotta pot': 'terracotta',
  'cork coaster': 'sand',
  'rattan storage box': 'sand',
};

/** 冲突时的替换候选（顺序即优先级；都被占用就删件）。 */
const PROP_REPLACEMENTS: Record<string, readonly string[]> = {
  'light oak block': ['warm gray stone slab', 'kraft paper sheet'],
  'walnut cutting board': ['warm gray stone slab', 'kraft paper sheet'],
  'oak hanger': ['warm gray stone slab', 'kraft paper sheet'],
  'white ceramic dish': ['warm gray stone slab', 'brass clip'],
  'acrylic tray': ['warm gray stone slab', 'brass clip'],
  'eucalyptus sprig': ['kraft paper sheet'],
  'washed linen cloth': ['warm gray stone slab'],
  'terracotta pot': ['warm gray stone slab', 'kraft paper sheet'],
  'matte black ceramic cup': ['warm gray stone slab', 'brass clip'],
  'rattan storage box': ['kraft paper sheet'],
  'dark leather tray': ['kraft paper sheet'],
  'concrete cube': ['warm gray stone slab'],
  'cork coaster': ['kraft paper sheet'],
  'pale marble coaster': ['brass clip'],
};

/** 主体材质关键词 → 需要避开的零件材质族。 */
const SUBJECT_MATERIAL_TRIGGERS: Record<string, readonly string[]> = {
  wood: ['木', '木质', '实木', '竹', 'wood', 'oak', 'walnut', 'bamboo'],
  ceramic: ['陶瓷', '瓷', '陶', 'ceramic', 'porcelain', 'stoneware'],
  plant: ['植物', '绿植', '盆栽', '花艺', 'plant', 'flower', 'leaf', 'greenery'],
  fabric: ['布', '棉', '麻', '毛', '丝', '纺织', 'fabric', 'cotton', 'linen', 'wool', 'silk', 'textile'],
  stone: ['石', '大理石', '花岗岩', 'stone', 'marble', 'granite'],
  glass: ['玻璃', 'glass'],
  leather: ['皮革', 'leather', 'pu 皮', 'pu皮'],
};

/** 商品本体就是某零件时，该零件必须移除。 */
export const PROP_SUBJECT_KEYWORDS: Record<string, readonly string[]> = {
  'rolled yoga mat': ['瑜伽垫', 'yoga mat'],
  'terracotta pot': ['花盆', '花器', 'planter', 'flower pot'],
  'green felt mat': ['桌垫', '鼠标垫', 'felt mat', 'play mat'],
  'woven floor mat': ['地垫', '地毯', 'floor mat', 'rug'],
  'warm gray paper stack': ['打印纸', '纸张', 'paper stack', 'notebook', '笔记本'],
  'light oak block': ['木块', 'wood block', '摆件', 'wooden block'],
  'white ceramic dish': ['碗', '盘', '碟', 'bowl', 'dish', 'plate'],
  'felt sleeve': ['内胆包', '收纳袋', 'sleeve', 'pouch'],
  'microfiber towel': ['毛巾', '抹布', 'towel'],
  'oak hanger': ['衣架', 'hanger'],
  'pegboard square': ['洞洞板', 'pegboard'],
  'acrylic tray': ['托盘', 'tray'],
  'brass clip': ['夹子', 'clip'],
  'frosted glass cup': ['水杯', '杯子', 'cup', 'mug'],
  'eucalyptus sprig': ['尤加利', 'eucalyptus'],
  'walnut cutting board': ['砧板', '菜板', 'cutting board'],
  'kraft paper sheet': ['牛皮纸', 'kraft paper'],
  'warm gray stone slab': ['石板', 'stone slab'],
  'pale marble coaster': ['杯垫', 'coaster', '大理石杯垫'],
  'cork coaster': ['杯垫', 'coaster', '软木'],
  'matte black ceramic cup': ['水杯', '杯子', 'cup', 'mug'],
  'concrete cube': ['水泥块', '水泥摆件', 'concrete'],
  'dark leather tray': ['皮革托盘', 'leather tray'],
  'rattan storage box': ['藤编', '收纳筐', '收纳篮', 'rattan'],
};

/** 主体颜色关键词 → 颜色族（与 PROP_COLOR_FAMILY 对齐）。 */
const SUBJECT_COLOR_TRIGGERS: Record<string, readonly string[]> = {
  white: ['白', '米白', '奶白', '纯白', 'white', 'ivory', 'cream'],
  gray: ['灰', '烟灰', '浅灰', '灰白', 'gray', 'grey'],
  charcoal: ['黑', '炭黑', '深灰', 'black', 'charcoal'],
  green: ['绿', '墨绿', '军绿', 'green', 'olive'],
  wood: ['木色', '原木', '原木色', '胡桃', '橡木', 'wood', 'oak', 'walnut'],
  sand: ['米色', '沙色', '杏色', '驼色', 'beige', 'sand', 'tan'],
  brass: ['金', '黄铜', '香槟金', 'gold', 'brass'],
  terracotta: ['陶土', '红陶', '砖红', 'terracotta'],
  clear: ['透明', '玻璃', 'clear', 'transparent'],
  kraft: ['牛皮纸', '卡其', 'kraft'],
};

/** 「仿木纹 / 仿皮 / 贴皮」这类不是真材质，不能按真材质去做冲突换件。 */
const FAUX_MATERIAL_HINTS = ['仿', '假', '贴皮', 'faux', 'imitation', 'looks like'];

function normalizeCategory(categoryId: string): SceneCategoryId {
  return Object.prototype.hasOwnProperty.call(SCENE_PRESETS, categoryId)
    ? (categoryId as SceneCategoryId)
    : 'general';
}

function haystackOf(subject: SceneSubject): string {
  return `${subject.title || ''} ${subject.material || ''}`.toLowerCase();
}

function matchesSubject(haystack: string, keywords: readonly string[]): boolean {
  return keywords.some((keyword) => haystack.includes(keyword.toLowerCase()));
}

/**
 * 主体命中的材质族：wood / ceramic / plant / fabric / stone / glass / leather。
 * 标题里出现「仿木纹」这类词时不做材质判定，避免把合适的木道具误换掉。
 */
export function detectSubjectFamilies(subject: SceneSubject): Set<string> {
  const haystack = haystackOf(subject);
  const families = new Set<string>();
  if (matchesSubject(haystack, FAUX_MATERIAL_HINTS)) return families;
  for (const [family, keywords] of Object.entries(SUBJECT_MATERIAL_TRIGGERS)) {
    if (matchesSubject(haystack, keywords)) families.add(family);
  }
  return families;
}

/** 主体颜色族：显式传 colorFamily 优先，否则从标题/材质里猜；同样跳过「仿色」。 */
export function detectSubjectColor(subject: SceneSubject): string {
  const explicit = (subject.colorFamily || '').trim().toLowerCase();
  if (explicit) return explicit;
  const haystack = haystackOf(subject);
  if (matchesSubject(haystack, FAUX_MATERIAL_HINTS)) return '';
  for (const [family, keywords] of Object.entries(SUBJECT_COLOR_TRIGGERS)) {
    if (matchesSubject(haystack, keywords)) return family;
  }
  return '';
}

/**
 * 按冲突规则整理零件：本体撞件 → 材质冲突换件 → 同色系删件 → 同材质去重 → 截断到风格上限。
 * 数量上限由 style 决定（minimal 1 / lifestyle 3 / smart 4 / ornate 6）。
 */
export function resolvePropConflicts(
  categoryId: string,
  subject: SceneSubject = {},
  options: ResolvePropOptions = {},
): string[] {
  const id = normalizeCategory(categoryId);
  const pool = options.props ?? PROP_POOLS[id];
  const limit = PROP_STYLE_LIMITS[options.style ?? DEFAULT_SCENE_STYLE];
  const haystack = haystackOf(subject);
  const subjectFamilies = detectSubjectFamilies(subject);
  const subjectColor = detectSubjectColor(subject);

  const picked: string[] = [];
  const usedFamilies = new Set<string>();

  const isFree = (name: string) => {
    if (picked.includes(name)) return false;
    const family = PROP_MATERIAL_FAMILY[name] || '';
    if (family && usedFamilies.has(family)) return false;
    return true;
  };

  for (const prop of pool) {
    if (picked.length >= limit) break;

    // 规则 6：商品本体就是这件零件，直接移除。
    const subjectWords = PROP_SUBJECT_KEYWORDS[prop] || [];
    if (subjectWords.length && matchesSubject(haystack, subjectWords)) continue;

    let next: string | undefined = prop;

    // 规则 1-4：主体材质与零件材质冲突，换候选；候选都占用则删件。
    const family = PROP_MATERIAL_FAMILY[prop] || '';
    if (family && subjectFamilies.has(family)) {
      next = (PROP_REPLACEMENTS[prop] || []).find(
        (candidate) => isFree(candidate) && (PROP_MATERIAL_FAMILY[candidate] || '') !== family,
      );
      if (!next) continue;
    }

    // 规则 5：主体与零件同色系，删件不替换。
    const color = PROP_COLOR_FAMILY[next] || '';
    if (subjectColor && color && color === subjectColor) continue;

    // 同材质去重（比如同时命中 stone slab 与 warm gray stone slab）。
    if (!isFree(next)) continue;

    picked.push(next);
    if (PROP_MATERIAL_FAMILY[next]) usedFamilies.add(PROP_MATERIAL_FAMILY[next]);
  }

  return picked.slice(0, limit);
}

/**
 * 拼出可直接塞进品类提示词的 Scene/Set 片段。
 * 零件为空时省略 `Set:`，不留空段。
 */
export function buildScenePropLine(
  categoryId: string,
  subject: SceneSubject = {},
  options: BuildSceneLineOptions = {},
): string {
  const id = normalizeCategory(categoryId);
  const scene = SCENE_PRESETS[id][options.variant === 'alt' ? 'alt' : 'main'];
  const props = resolvePropConflicts(id, subject, options);
  const tokens = renderPropTokens(props, options);
  return tokens.length ? `Scene: ${scene}; Set: ${tokens.join(', ')};` : `Scene: ${scene};`;
}

/** 四格与零件档位的对应：null 表示该格不出零件。 */
export const GRID_PANEL_STYLES: Record<SceneStyle, readonly (SceneStyle | null)[]> = {
  minimal: [null, null, 'minimal', 'minimal'],
  lifestyle: [null, null, 'lifestyle', 'lifestyle'],
  smart: [null, null, 'lifestyle', 'ornate'],
  ornate: [null, null, 'ornate', 'ornate'],
};

/** 品类段（Scene + Set）词预算：与各档零件数量匹配。 */
export const SCENE_PROMPT_BUDGET: Record<SceneStyle, number> = {
  minimal: 20,
  lifestyle: 28,
  smart: 32,
  ornate: 40,
};

/** 接入后端前自检：词数是否超出该档位预算。 */
export function validateScenePropLine(
  line: string,
  style: SceneStyle = DEFAULT_SCENE_STYLE,
): { words: number; budget: number; overBudget: boolean } {
  const words = line.trim().split(/\s+/).filter(Boolean).length;
  const budget = SCENE_PROMPT_BUDGET[style];
  return { words, budget, overBudget: words > budget };
}
