import { PROP_CARDS } from './propCardLibrary';

export type ImageCategoryId =
  | 'auto'
  | 'home_kitchen'
  | 'apparel_accessories'
  | 'pet_supplies'
  | 'office_stationery'
  | 'sports_outdoor'
  | 'auto_accessories'
  | 'hand_tools'
  | 'passive_tech'
  | 'beauty_tools'
  | 'adult_games'
  | 'garden_tools'
  | 'general';

export type ImageStyleId =
  | 'auto'
  | 'calm_domestic'
  | 'bright_minimal'
  | 'warm_evening'
  | 'clinical_clean'
  | 'industrial'
  | 'fresh_outdoor'
  | 'editorial_minimal';

export type PhotoLayerKey = 'frame' | 'light' | 'tone' | 'detail' | 'style';

export type PhotoSelection = Record<PhotoLayerKey, string>;

export interface PhotoCardOption {
  id: string;
  label: string;
  description: string;
}

export interface PhotoLayerMeta {
  key: PhotoLayerKey;
  label: string;
  description: string;
}

export interface SimplePreset {
  id: string;
  name: string;
  /** 一句话说明买家一眼能看到什么。 */
  tagline: string;
  /** 视觉签名：机位 / 光影 / 场景，方便和别的模板区分。 */
  signature: string;
  description: string;
  /** 效果图路径：把图片放到 public/assets/image-presets/ 下后填入，例如 /assets/image-presets/home-life.jpg */
  image?: string;
  /** 暂时没有效果图时显示的渐变占位 */
  swatch: string;
  layers: Record<PhotoLayerKey, string>;
  /** 四格逐格选择：小白模板不仅换全局光影，也换每格构图、场景和道具。 */
  panels: PanelSelection[];
}

export const IMAGE_CATEGORY_OPTIONS: { id: ImageCategoryId; label: string; description: string }[] = [
  { id: 'auto', label: '自动识别（默认）', description: '每个链接按标题自动分类，各用各的品类组合' },
  { id: 'home_kitchen', label: '家居与厨房', description: '厨房、收纳、床品、家居装饰' },
  { id: 'apparel_accessories', label: '服装与配饰', description: '成人服装、鞋包、眼镜、首饰' },
  { id: 'pet_supplies', label: '宠物用品', description: '宠物玩具、窝垫、碗、梳理工具' },
  { id: 'office_stationery', label: '办公与文具', description: '桌面收纳、纸品、文具、计算器' },
  { id: 'sports_outdoor', label: '运动户外', description: '瑜伽、健身、露营、钓鱼小件' },
  { id: 'auto_accessories', label: '汽车用品', description: '遮阳挡、缝隙收纳、脚垫、洗车' },
  { id: 'hand_tools', label: '工具与五金', description: '手动工具、紧固件、墙面挂钩' },
  { id: 'passive_tech', label: '纯物理配件', description: '手机壳、内胆包、线材收纳、清洁' },
  { id: 'beauty_tools', label: '美妆个护工具', description: '化妆刷、美甲、修眉、手动护理' },
  { id: 'adult_games', label: '成人玩具游戏', description: '桌游、扑克、拼图、解压玩具' },
  { id: 'garden_tools', label: '园艺庭院工具', description: '花盆、修枝剪、育苗、浇水' },
  { id: 'general', label: '通用兜底', description: '不确定品类时使用中性组合' },
];

export const PHOTO_LAYERS: PhotoLayerMeta[] = [
  { key: 'frame', label: 'L1 构图搭骨架', description: '主体位置、机位、占比、重心' },
  { key: 'light', label: 'L2 光影塑体积', description: '主光、硬软、阴影、对比度' },
  { key: 'tone', label: 'L3 色彩定情绪', description: '冷暖、浓淡、曝光、背景分离' },
  { key: 'detail', label: 'L4 细节撑质感', description: '材质、纹理、边缘、后期' },
  { key: 'style', label: 'L5 风格统全局', description: '四格是否像同一套图' },
];

export const PHOTO_CARD_OPTIONS: Record<PhotoLayerKey, PhotoCardOption[]> = {
  frame: [
    { id: 'frame_hero', label: '英雄生活场景', description: '50mm · 主体 52-65% · 居中英雄构图' },
    { id: 'frame_macro', label: '微距细节', description: '90mm · 细节 60-75% · 近景裁切' },
    { id: 'frame_environment', label: '环境远景', description: '24mm · 主体 18-38% · 前中后三层' },
    { id: 'frame_flatlay', label: '平铺网格', description: '正俯拍 · 套装 55-68% · 严格网格' },
    { id: 'frame_flatlay_detail', label: '俯拍特写', description: '正俯拍 · 单件 45-60% · 无透视畸变' },
    { id: 'frame_low_angle', label: '低角度', description: '35mm · 主体 52-65% · 非对称平衡' },
    { id: 'frame_portrait', label: '人像压缩', description: '85mm · 主体 52-65% · 三分法' },
  ],
  light: [
    { id: 'light_soft_home', label: '柔和家居', description: '窗侧光 + 白反弹 + 接地阴影' },
    { id: 'light_clean_bright', label: '干净明亮', description: '均匀顶光 + 控高光 + 中等对比' },
    { id: 'light_editorial_sun', label: '编辑日光', description: '低角度暖阳 + 柔投影 + 编辑对比' },
    { id: 'light_workshop_hard', label: '工坊硬光', description: '硬掠射 + 暗侧负补 + 清晰硬影' },
    { id: 'light_overcast', label: '阴天户外', description: '阴天日光 + 负补 + 高对比' },
    { id: 'light_warm_evening', label: '暖夜实践', description: '暖钨丝 + 长柔影 + 高对比' },
    { id: 'light_vanity', label: '临床梳妆', description: '明亮均匀梳妆光 + 低对比' },
    { id: 'light_car_sun', label: '车内硬阳', description: '玻璃硬阳 + 柔补 + 窄缝阴影' },
    { id: 'light_dappled', label: '斑驳园艺', description: '叶影日光 + 白反弹 + 中等对比' },
    { id: 'light_studio_hero', label: '影棚英雄', description: '影棚扫光 + 控高光 + 接地阴影' },
    { id: 'light_warm_sport', label: '暖阳运动', description: '暖阳 + 负补 + 硬影 + 高对比' },
    { id: 'light_window_editorial', label: '窗光编辑', description: '窗侧光 + 白反弹 + 柔投影 + 编辑对比' },
  ],
  tone: [
    { id: 'tone_natural', label: '自然平衡', description: '自然真色 · 平衡曝光 · 4800K' },
    { id: 'tone_bright_airy', label: '明亮通透', description: '低饱和 · 明亮 · 5200K' },
    { id: 'tone_warm_moody', label: '暖调氛围', description: '浓郁 · 低调 · 2700K' },
    { id: 'tone_clinical', label: '临床干净', description: '自然色 · 明亮均匀 · 5000K' },
    { id: 'tone_industrial', label: '工业硬朗', description: '低饱和 · 平衡 · 5000K' },
    { id: 'tone_fresh_outdoor', label: '清新户外', description: '浓郁 · 明亮 · 5600K' },
    { id: 'tone_editorial_muted', label: '编辑低饱和', description: '背景去饱和 · 产品色点缀 · 5200K' },
    { id: 'tone_warm_outdoor', label: '暖阳户外', description: '浓郁 · 明亮 · 4800K' },
  ],
  detail: [
    { id: 'detail_ceramic_fabric', label: '陶瓷织物', description: '釉面微光 + 织纹/折痕' },
    { id: 'detail_soft_goods', label: '软质服装', description: '织纹折痕 + 皮革纹理' },
    { id: 'detail_paper_plastic', label: '纸张塑料', description: '纸边 + 塑料模具线' },
    { id: 'detail_rubber_webbing', label: '橡胶织带', description: '橡胶纹理 + 织带' },
    { id: 'detail_plastic_leather', label: '塑料皮革', description: '塑料模具线 + 皮革纹理' },
    { id: 'detail_metal_wood', label: '金属木纹', description: '金属高光 + 木纹' },
    { id: 'detail_matte_plastic', label: '哑光塑料', description: '哑光微光 + 模具线' },
    { id: 'detail_metal_ceramic', label: '金属陶瓷', description: '金属高光 + 陶瓷釉面' },
    { id: 'detail_paper_felt', label: '纸张毛毡', description: '纸边 + 毛毡织纹' },
    { id: 'detail_soil_terracotta', label: '土壤陶土', description: '湿土 + 陶土气孔' },
  ],
  style: [
    { id: 'calm_domestic', label: '安静生活', description: '暖窗光、低饱和、生活痕迹' },
    { id: 'bright_minimal', label: '明亮极简', description: '高亮、干净、留白' },
    { id: 'warm_evening', label: '温暖夜晚', description: '2700K、柔影、松弛' },
    { id: 'clinical_clean', label: '临床干净', description: '均匀亮、洁净、细节清晰' },
    { id: 'industrial', label: '工业工坊', description: '硬光、金属、真实磨损' },
    { id: 'fresh_outdoor', label: '清新户外', description: '自然日光、绿意、通透' },
    { id: 'editorial_minimal', label: '编辑极简', description: '低饱和、克制、编辑感' },
  ],
};

export const DEFAULT_PHOTO_SELECTION: PhotoSelection = {
  frame: 'auto',
  light: 'auto',
  tone: 'auto',
  detail: 'auto',
  style: 'auto',
};

export type PanelSelection = Record<PhotoLayerKey, string> & {
  /** 场景/台面卡；auto=按品类该格默认。 */
  scene: string;
  /** 辅助道具卡；null=按品类默认，[]=明确不放。 */
  props: string[] | null;
  /** 第 1 格是否保持纯白底；默认 true。 */
  whiteHero: boolean;
};

export const DEFAULT_PANEL_SELECTIONS: PanelSelection[] = [
  {
    frame: 'auto',
    light: 'auto',
    tone: 'auto',
    detail: 'auto',
    style: 'auto',
    scene: 'auto',
    props: null,
    whiteHero: true,
  },
  {
    frame: 'auto',
    light: 'auto',
    tone: 'auto',
    detail: 'auto',
    style: 'auto',
    scene: 'auto',
    props: null,
    whiteHero: false,
  },
  {
    frame: 'auto',
    light: 'auto',
    tone: 'auto',
    detail: 'auto',
    style: 'auto',
    scene: 'auto',
    props: null,
    whiteHero: false,
  },
  {
    frame: 'auto',
    light: 'auto',
    tone: 'auto',
    detail: 'auto',
    style: 'auto',
    scene: 'auto',
    props: null,
    whiteHero: false,
  },
];

export const PANEL_META = [
  { label: '第 1 格 · 主图', role: '主图/白底英雄', note: '默认纯白底、零道具；关闭后可自由布景。' },
  { label: '第 2 格 · 45°', role: '完整角度', note: '展示产品另一完整角度，可加 1-2 个辅助道具。' },
  { label: '第 3 格 · 生活', role: '生活场景', note: '真实使用空间与配套道具，体现使用方式。' },
  { label: '第 4 格 · 细节', role: '微距细节', note: '材质、结构或工艺特写；默不放道具。' },
];

export const AUX_SCENE_OPTIONS: PhotoCardOption[] = [
  { id: 'auto', label: '自动（品类默认）', description: '按该品类的第 1/2/3 套场景分配' },
  { id: 'scene_white_sweep', label: '纯白无缝', description: '主图专用，零道具' },
  { id: 'scene_light_oak', label: '浅橡木桌面', description: '书桌/桌面/家居' },
  { id: 'scene_butcher_block', label: '厨房木台面', description: '厨房、餐具、收纳' },
  { id: 'scene_linen', label: '亚麻台面', description: '床品、布艺、家居' },
  { id: 'scene_marble', label: '白色大理石', description: '美妆、饰品、家居' },
  { id: 'scene_dark_marble', label: '深色大理石', description: '咖啡馆、配饰' },
  { id: 'scene_felt', label: '深色毛毡', description: '办公、游戏、科技' },
  { id: 'scene_concrete', label: '水泥工坊', description: '工具、汽车、工业' },
  { id: 'scene_plaster', label: '灰泥墙台', description: '中性家居、通用' },
  { id: 'scene_stone', label: '浅色石台', description: '服装、园艺、通用' },
  { id: 'scene_terracotta', label: '陶土花台', description: '园艺、花盆' },
  { id: 'scene_rug', label: '羊毛地毯', description: '宠物、家居' },
  { id: 'scene_rubber', label: '健身房橡胶地', description: '运动、健身' },
  { id: 'scene_bedroom_mirror', label: '卧室镜前', description: '服装、配饰' },
  { id: 'scene_entryway_console', label: '玄关台', description: '配饰、宠物、家居' },
  { id: 'scene_workbench', label: '工坊工作台', description: '工具、五金' },
  { id: 'scene_vanity', label: '梳妆台', description: '美妆、个护' },
  { id: 'scene_potting_bench', label: '育苗台', description: '园艺、庭院' },
  { id: 'scene_gaming_table', label: '游戏茶几', description: '桌游、成人玩具' },
];

// 只保留大块、简单道具；尺子/笔/刷子等精细物件已从卡片库移除（生图易畸变）。
const AUX_PROP_ID_MAP: Record<string, string> = {
  notebook: 'prop_notebook',
  'ceramic-bowl': 'prop_ceramic_bowl',
  'linen-towel': 'prop_linen_towel',
  'pet-blanket': 'prop_pet_blanket',
  'steel-bowl': 'prop_steel_bowl',
  'cosmetic-jar': 'prop_cosmetic_jar',
  'plant-pot': 'prop_plant_pot',
  'woven-basket': 'prop_woven_basket',
  'wooden-block': 'prop_wooden_block',
};

export const AUX_PROP_OPTIONS: PhotoCardOption[] = PROP_CARDS.map((card) => ({
  id: AUX_PROP_ID_MAP[card.id] ?? card.id,
  label: card.label,
  description: `${card.scale} · ${card.colorFamily}`,
}));

function panel(overrides: Partial<PanelSelection>): PanelSelection {
  return {
    frame: 'auto',
    light: 'auto',
    tone: 'auto',
    detail: 'auto',
    style: 'auto',
    scene: 'auto',
    props: null,
    whiteHero: false,
    ...overrides,
  };
}

function whiteHeroPanel(frame = 'frame_hero'): PanelSelection {
  return panel({
    frame,
    light: 'light_studio_hero',
    scene: 'scene_white_sweep',
    props: [],
    whiteHero: true,
  });
}

/**
 * 四个小白模板不是“四套滤镜”，而是四种买家视角：
 * 1) 卖点主图：高调干净，尺寸/数量/结构一眼可见；
 * 2) 真实使用：真实空间 + 使用位置（画面不出现手部）；
 * 3) 材质工艺：微距 + 硬掠光，给价格理由；
 * 4) 全套平铺：90° 俯拍，所有配件和数量一次看清。
 * 每个模板都显式改写四格构图、光影、场景和道具，避免只换全局色调导致同质化。
 */
export const SIMPLE_PRESETS: SimplePreset[] = [
  {
    id: 'hero_proof',
    name: '卖点主图',
    tagline: '一眼看懂：尺寸、数量、关键结构',
    signature: '45°/85mm · 高调白光 · 零道具',
    description: '干净高调，主体占满画面；用角度、比例和结构直接证明卖点。',
    image: '/assets/image-presets/hero-proof.jpg',
    swatch: 'linear-gradient(135deg, #ffffff 0%, #eef2f7 55%, #cbd5e1 100%)',
    layers: {
      frame: 'frame_hero',
      light: 'light_clean_bright',
      tone: 'tone_bright_airy',
      detail: 'detail_matte_plastic',
      style: 'bright_minimal',
    },
    panels: [
      whiteHeroPanel(),
      panel({
        frame: 'frame_portrait',
        light: 'light_clean_bright',
        tone: 'tone_natural',
        detail: 'detail_matte_plastic',
        style: 'bright_minimal',
        scene: 'scene_stone',
        props: [],
      }),
      panel({
        frame: 'frame_low_angle',
        light: 'light_clean_bright',
        tone: 'tone_bright_airy',
        detail: 'detail_matte_plastic',
        style: 'bright_minimal',
        scene: 'scene_plaster',
        props: [],
      }),
      panel({
        frame: 'frame_macro',
        light: 'light_clean_bright',
        tone: 'tone_bright_airy',
        detail: 'detail_matte_plastic',
        style: 'bright_minimal',
        scene: 'scene_plaster',
        props: [],
      }),
    ],
  },
  {
    id: 'lifestyle_use',
    name: '真实使用',
    tagline: '场景带入：真实位置 + 手部使用',
    signature: '24-50mm · 暖窗光 · 真实使用位置',
    description: '把商品放回它真正被使用的位置（钥匙扣挂在裤腰带或包带上），并用手部操作或局部人物（不露正脸）演示怎么用。',
    image: '/assets/image-presets/lifestyle-use.jpg',
    swatch: 'linear-gradient(135deg, #f5e6d3 0%, #d9c3a5 55%, #b08968 100%)',
    layers: {
      frame: 'frame_environment',
      light: 'light_soft_home',
      tone: 'tone_natural',
      detail: 'detail_ceramic_fabric',
      style: 'calm_domestic',
    },
    panels: [
      whiteHeroPanel(),
      panel({
        frame: 'frame_hero',
        light: 'light_soft_home',
        tone: 'tone_natural',
        detail: 'detail_ceramic_fabric',
        style: 'calm_domestic',
        scene: 'auto',
        props: null,
      }),
      panel({
        frame: 'frame_environment',
        light: 'light_editorial_sun',
        tone: 'tone_warm_moody',
        detail: 'detail_ceramic_fabric',
        style: 'calm_domestic',
        scene: 'auto',
        props: null,
      }),
      panel({
        frame: 'frame_macro',
        light: 'light_window_editorial',
        tone: 'tone_natural',
        detail: 'detail_ceramic_fabric',
        style: 'calm_domestic',
        scene: 'auto',
        props: [],
      }),
    ],
  },
  {
    id: 'macro_craft',
    name: '材质工艺',
    tagline: '价格理由：纹理、接缝、做工',
    signature: '90mm 微距 · 硬掠光 · 深色编辑',
    description: '把材质、边缘和工艺填满画面，适合高客单和差异化卖点。',
    image: '/assets/image-presets/macro-craft.jpg',
    swatch: 'linear-gradient(135deg, #1f2937 0%, #4b5563 55%, #b45309 100%)',
    layers: {
      frame: 'frame_macro',
      light: 'light_workshop_hard',
      tone: 'tone_editorial_muted',
      detail: 'detail_metal_wood',
      style: 'editorial_minimal',
    },
    panels: [
      // 主图必须完整可辨，不能是微距裁切；材质工艺的力量放在第 2/3 格。
      whiteHeroPanel(),
      panel({
        frame: 'frame_macro',
        light: 'light_workshop_hard',
        tone: 'tone_industrial',
        detail: 'detail_metal_wood',
        style: 'industrial',
        scene: 'scene_concrete',
        props: [],
      }),
      panel({
        frame: 'frame_low_angle',
        light: 'light_workshop_hard',
        tone: 'tone_warm_moody',
        detail: 'detail_metal_wood',
        style: 'editorial_minimal',
        scene: 'scene_dark_marble',
        props: [],
      }),
      panel({
        frame: 'frame_macro',
        light: 'light_editorial_sun',
        tone: 'tone_warm_moody',
        detail: 'detail_metal_wood',
        style: 'editorial_minimal',
        scene: 'scene_plaster',
        props: [],
      }),
    ],
  },
  {
    id: 'knolling_set',
    name: '全套平铺',
    tagline: '全套数量：配件、结构一次看清',
    signature: '90° 俯拍 · 均匀顶光 · 全套排列',
    description: '所有配件、数量和收纳结构一次展示，适合套装、文具和配件。',
    image: '/assets/image-presets/knolling-set.jpg',
    swatch: 'linear-gradient(135deg, #f8fafc 0%, #e2e8f0 55%, #94a3b8 100%)',
    layers: {
      frame: 'frame_flatlay',
      light: 'light_clean_bright',
      tone: 'tone_bright_airy',
      detail: 'detail_paper_plastic',
      style: 'bright_minimal',
    },
    panels: [
      whiteHeroPanel('frame_flatlay'),
      panel({
        frame: 'frame_flatlay',
        light: 'light_clean_bright',
        tone: 'tone_bright_airy',
        detail: 'detail_paper_plastic',
        style: 'bright_minimal',
        scene: 'scene_felt',
        props: null,
      }),
      panel({
        frame: 'frame_flatlay',
        light: 'light_clean_bright',
        tone: 'tone_natural',
        detail: 'detail_paper_plastic',
        style: 'bright_minimal',
        scene: 'scene_marble',
        props: null,
      }),
      panel({
        frame: 'frame_flatlay_detail',
        light: 'light_clean_bright',
        tone: 'tone_bright_airy',
        detail: 'detail_paper_plastic',
        style: 'bright_minimal',
        scene: 'scene_felt',
        props: [],
      }),
    ],
  },
];

export function imageCategoryLabel(id: ImageCategoryId): string {
  return IMAGE_CATEGORY_OPTIONS.find((option) => option.id === id)?.label ?? '自动识别（默认）';
}

export function photoCardLabel(layer: PhotoLayerKey, id: string): string {
  if (id === 'auto') return '自动（品类推荐）';
  return PHOTO_CARD_OPTIONS[layer].find((option) => option.id === id)?.label ?? id;
}

export function photoCardDescription(layer: PhotoLayerKey, id: string): string {
  if (id === 'auto') return '由该品类的默认卡片决定';
  return PHOTO_CARD_OPTIONS[layer].find((option) => option.id === id)?.description ?? '';
}

export function findSimplePreset(
  selection: PhotoSelection,
  panels?: PanelSelection[],
): SimplePreset | undefined {
  const panelsKey = panels && panels.length ? JSON.stringify(panels) : '';
  return SIMPLE_PRESETS.find((preset) => {
    const layersMatch = (Object.keys(preset.layers) as PhotoLayerKey[]).every(
      (layer) => preset.layers[layer] === selection[layer],
    );
    if (!layersMatch) return false;
    if (!panelsKey) return true;
    return JSON.stringify(preset.panels) === panelsKey;
  });
}
