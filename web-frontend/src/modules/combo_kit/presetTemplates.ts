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
};

const ACTIVE_TEMPLATE_KEY = 'mainpg.combo-kit.active-template';
const CUSTOM_TEMPLATE_KEY = 'mainpg.combo-kit.custom-template';

export const COMBO_PRESET_TEMPLATES: ComboPresetTemplate[] = [
  {
    id: 'standard-commerce',
    name: '标准电商套装',
    description:
      '适合大多数商品组合：两张真实使用场景图 + 白底尺寸图，保持颜色与材质准确；细节图与详情图由系统固定模板生成。',
    base_prompt_a:
      'professional e-commerce product photography, studio lighting, sharp focus, clean neutral background, accurate color and material, no human, no text overlay, no watermark',
    role_directions: {
      carousel_2:
        'lifestyle scene: the complete bundled set placed on a real home surface in a modern American living room or game-night table, minimal natural props (glass, snack, small plant, linen) that never cover the products, unified soft window light, every member product clearly visible',
      carousel_3:
        'second lifestyle scene: the complete bundled set used in a different real home setting (sunlit dining table or cozy reading corner), relaxed natural arrangement, realistic adult hands allowed, consistent soft light, every member product clearly recognizable',
      white_bg:
        'complete bundled set on a clean white background, full product visible, balanced layout, professional studio shot',
      detail_shot: '',
    },
  },
];

export const DEFAULT_COMBO_PRESET = COMBO_PRESET_TEMPLATES[0];

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

export function loadCustomTemplate(): ComboPresetTemplate | null {
  const raw = readStorage(CUSTOM_TEMPLATE_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<ComboPresetTemplate>;
    if (!value.base_prompt_a || !value.role_directions) return null;
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
