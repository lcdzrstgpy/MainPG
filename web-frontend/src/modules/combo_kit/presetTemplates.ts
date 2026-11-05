export type ComboDirectorLayerKey = 'frame' | 'light' | 'tone' | 'detail' | 'style';

/** 导演模式：与商品处理共用同一套五层卡片 id。 */
export type ComboDirectorSelection = {
  scene: string;
  frame: string;
  light: string;
  tone: string;
  detail: string;
  style: string;
};

export type ComboDirectorRole = 'carousel_2' | 'carousel_3' | 'white_bg' | 'detail_shot';

export type ComboDirectorPayload = {
  layers: ComboDirectorSelection;
  panels: Partial<Record<ComboDirectorRole, ComboDirectorSelection>>;
  /** 两张轮播图复用同一套五层，只换地点与时段。 */
  reuseCarousel: boolean;
};

export type ComboPresetTemplate = {
  id: string;
  name: string;
  description: string;
  base_prompt_a: string;
  // carousel_2 / carousel_3 为历史 key，业务语义分别是「使用场景图 1 / 使用场景图 2」。
  // detail_shot 只做补充要求，后端固定模板为底座，用户输入不覆盖模板。
  role_directions: {
    carousel_2: string;
    carousel_3: string;
    white_bg: string;
    detail_shot: string;
  };
  /** 导演模式参数（仅自定义模板使用；标准模板走上面的文案）。 */
  layers?: ComboDirectorSelection;
  panels?: Partial<Record<ComboDirectorRole, ComboDirectorSelection>>;
  reuseCarousel?: boolean;
};

const ACTIVE_TEMPLATE_KEY = 'mainpg.combo-kit.active-template';
const CUSTOM_TEMPLATE_KEY = 'mainpg.combo-kit.custom-template';

const WHITE_HERO =
  'complete bundled set on a pure white seamless background (#FFFFFF to #FAFAFA), centred and ' +
  'filling 78%-85% of the frame with even clear margins, no props, no room, no horizon, no text, ' +
  'professional studio lighting with a soft contact shadow';

// 四套标准模板与商品处理一致：影棚无缝 / 人景叙事 / 静物平铺 / 环境远景。
// 组件生成的是 4 张独立成品图（主图融合 + 两张轮播 + 白底 + 细节），
// 所以这里不给「四宫格」措辞；两张轮播图直接复用同一套风格，只换地点与时段。
export const COMBO_PRESET_TEMPLATES: ComboPresetTemplate[] = [
  {
    id: 'studio-seamless',
    name: '影棚无缝主图',
    description:
      '纯色无缝背景 + 硬光投影，没有房间、没有地平线；两张轮播图用不同色纸与基座区分，适合平台主图与干净的目录页。',
    base_prompt_a:
      'professional studio product photography on a seamless backdrop, controlled softbox lighting, ' +
      'sharp focus, accurate color and material, no human, no text overlay, no watermark',
    role_directions: {
      carousel_2:
        'studio scene: the complete bundled set on a pale stone plinth against a warm-grey seamless ' +
        'sweep, no room and no horizon, product on the RIGHT golden line at x≈62%, at least four ' +
        'studio props (a low riser, a folded linen square, a ceramic dish, a dried stem) beside it ' +
        'that never cover it, hard directional key light with a crisp contact shadow, every member ' +
        'product clearly visible',
      carousel_3:
        'second studio scene: the same set on a charcoal seamless sweep shot from a lower angle, ' +
        'product on the LEFT golden line at x≈38%, at least four different studio props (an acrylic ' +
        'cube, a matte block, a ceramic bowl, a folded grey cloth), a rim light separating the set ' +
        'from the backdrop, every member product clearly recognizable',
      white_bg: WHITE_HERO,
      detail_shot: '',
    },
  },
  {
    id: 'standard-commerce',
    name: '人景叙事',
    description:
      '真实生活台面 + 窗光侧逆 + 浅景深，像博主随手拍的生活照；两张轮播图换成不同房间与时段，适合家居、日用、礼品类组合。',
    base_prompt_a:
      'professional lifestyle e-commerce photography, real home environment, soft natural window ' +
      'light, sharp focus, accurate color and material, no watermark',
    role_directions: {
      carousel_2:
        'lifestyle scene: the complete bundled set on a real home surface beside a window (warm ' +
        'wooden dining table or living-room sideboard), placed on the RIGHT golden line at x≈62% - ' +
        'never dead centre. Surround it with at least four source-compatible props (a mug, a folded ' +
        'linen napkin, a small potted plant, a wooden tray) that sit behind or beside the set and ' +
        'never cover it. Build this set fresh: never reuse a backdrop, surface or prop from the ' +
        'reference photos. Unified soft window light, no hands in this frame, one small kraft tag ' +
        'with a single English word allowed, every member product clearly visible',
      carousel_3:
        'second lifestyle scene: the complete bundled set used in a different real home setting ' +
        '(sunlit kitchen island or cosy reading corner) at a different time of day, placed on the ' +
        'LEFT golden line at x≈38% with a tighter crop. At least four different props (an open book, ' +
        'a ceramic bowl, a folded throw, a floor plant) that never cover the products; invent a new ' +
        'set, never a crop of the first scene. Realistic adult hands allowed only if anatomically ' +
        'correct (Caucasian / Western European, light-to-medium skin tone, no face), consistent soft ' +
        'light, every member product clearly recognizable',
      white_bg: WHITE_HERO,
      detail_shot: '',
    },
  },
  {
    id: 'flat-lay',
    name: '静物平铺俯拍',
    description:
      '正俯视 knolling，只有平面没有墙，物体按行列对齐；适合文具、餐具、工具等可铺开的组合。',
    base_prompt_a:
      'professional overhead flat-lay product photography, camera perpendicular to the surface, ' +
      'soft even top light, sharp focus, accurate color and material, no human, no text overlay, ' +
      'no watermark',
    role_directions: {
      carousel_2:
        'overhead flat-lay: the complete bundled set arranged on a pale oak tabletop seen from ' +
        'directly above, product centre on the RIGHT golden line at x≈62%, at least four props (a ' +
        'kraft box, a folded linen cloth, a ceramic cup, a dried branch) laid in even rows with ' +
        'equal spacing, soft even top light and short soft shadows, no wall and no vertical object',
      carousel_3:
        'second overhead flat-lay: the same set on a light grey stone slab or a deep felt surface, ' +
        'product centre on the LOWER-LEFT golden intersection at x≈38% y≈62%, at least four ' +
        'different props laid flat, camera still perpendicular, tighter crop on material and ' +
        'texture, short directional shadows for depth',
      white_bg: WHITE_HERO,
      detail_shot: '',
    },
  },
  {
    id: 'environment-wide',
    name: '环境远景',
    description:
      '广角建立镜头，商品很小、空间很大；两张轮播图换房间与时段，适合家具、收纳、场景感强的组合。',
    base_prompt_a:
      'professional wide environmental e-commerce photography, real inhabited room, natural ' +
      'daylight, deep three-layer depth, sharp focus on the product, accurate color and material, ' +
      'no watermark',
    role_directions: {
      carousel_2:
        'wide establishing scene: the complete bundled set on a console in a sunlit living room, ' +
        'product small at 18%-28% of the frame on the LEFT golden line at x≈38%, the whole room ' +
        'readable with at least four environment elements (a sofa, a rug, a floor lamp, a potted ' +
        'plant), natural window light, deep foreground / midground / background layers',
      carousel_3:
        'second wide scene: a different room and time of day (entryway console or a balcony), ' +
        'product small at 18%-28% of the frame on the RIGHT golden intersection at x≈62% y≈44%, at ' +
        'least four different environment elements, no repeated furniture from the first scene, ' +
        'natural light consistent with the new time of day',
      white_bg: WHITE_HERO,
      detail_shot: '',
    },
  },
];

export const DEFAULT_COMBO_PRESET = COMBO_PRESET_TEMPLATES[1];

function readStorage(key: string): string {
  if (typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(key) || '';
  } catch {
    return '';
  }
}

function writeStorage(key: string, value: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Local storage can be disabled by the browser; the built-in preset remains usable.
  }
}

const EMPTY_LAYERS: ComboDirectorSelection = {
  scene: 'auto',
  frame: 'auto',
  light: 'auto',
  tone: 'auto',
  detail: 'auto',
  style: 'auto',
};

export function emptyDirectorSelection(): ComboDirectorSelection {
  return { ...EMPTY_LAYERS };
}

function normalizeSelection(value: unknown): ComboDirectorSelection | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const source = value as Record<string, unknown>;
  return {
    scene: String(source.scene || 'auto'),
    frame: String(source.frame || 'auto'),
    light: String(source.light || 'auto'),
    tone: String(source.tone || 'auto'),
    detail: String(source.detail || 'auto'),
    style: String(source.style || 'auto'),
  };
}

function hasSelection(value: ComboDirectorSelection | undefined): boolean {
  if (!value) return false;
  return Object.values(value).some((item) => item && item !== 'auto');
}

export function loadCustomTemplate(): ComboPresetTemplate | null {
  const raw = readStorage(CUSTOM_TEMPLATE_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<ComboPresetTemplate>;
    if (!value.base_prompt_a || !value.role_directions) return null;
    const panels: Partial<Record<ComboDirectorRole, ComboDirectorSelection>> = {};
    const rawPanels = (value.panels || {}) as Record<string, unknown>;
    (['carousel_2', 'carousel_3', 'white_bg', 'detail_shot'] as ComboDirectorRole[]).forEach((role) => {
      const selection = normalizeSelection(rawPanels[role]);
      if (selection) panels[role] = selection;
    });
    return {
      id: 'custom',
      name: String(value.name || '自定义模板'),
      description: String(value.description || '当前账号在本机保存的组合生图模板。'),
      base_prompt_a: String(value.base_prompt_a),
      role_directions: {
        carousel_2: String(value.role_directions.carousel_2 || ''),
        carousel_3: String(value.role_directions.carousel_3 || ''),
        white_bg: String(value.role_directions.white_bg || ''),
        // 旧版本保存的模板没有该字段，回退为空字符串。
        detail_shot: String(value.role_directions.detail_shot || ''),
      },
      layers: normalizeSelection(value.layers),
      panels,
      reuseCarousel: Boolean(value.reuseCarousel),
    };
  } catch {
    return null;
  }
}

export function saveCustomTemplate(template: ComboPresetTemplate): ComboPresetTemplate {
  const normalized: ComboPresetTemplate = {
    ...template,
    id: 'custom',
    name: String(template.name || '自定义模板').trim() || '自定义模板',
  };
  writeStorage(CUSTOM_TEMPLATE_KEY, JSON.stringify(normalized));
  return normalized;
}

export function getActiveTemplateId(): string {
  return readStorage(ACTIVE_TEMPLATE_KEY) || DEFAULT_COMBO_PRESET.id;
}

export function setActiveTemplateId(templateId: string): void {
  writeStorage(ACTIVE_TEMPLATE_KEY, templateId);
}

export function resolveActiveTemplate(): ComboPresetTemplate {
  const activeId = getActiveTemplateId();
  if (activeId === 'custom') return loadCustomTemplate() || DEFAULT_COMBO_PRESET;
  return COMBO_PRESET_TEMPLATES.find((template) => template.id === activeId) || DEFAULT_COMBO_PRESET;
}

/** 把模板里的导演模式选择整理成后端可读的 `__director` 载荷；没配过返回 null。 */
export function buildDirectorPayload(template: ComboPresetTemplate): ComboDirectorPayload | null {
  const layers = template.layers && hasSelection(template.layers) ? template.layers : undefined;
  const panels: Partial<Record<ComboDirectorRole, ComboDirectorSelection>> = {};
  Object.entries(template.panels || {}).forEach(([role, selection]) => {
    if (hasSelection(selection)) panels[role as ComboDirectorRole] = selection as ComboDirectorSelection;
  });
  const reuse = Boolean(template.reuseCarousel);
  if (!layers && !Object.keys(panels).length && !reuse) return null;
  return {
    layers: layers || emptyDirectorSelection(),
    panels,
    reuseCarousel: reuse,
  };
}
