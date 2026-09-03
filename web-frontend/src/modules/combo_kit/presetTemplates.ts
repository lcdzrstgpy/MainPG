export type ComboPresetTemplate = {
  id: string;
  name: string;
  description: string;
  base_prompt_a: string;
  role_directions: {
    carousel_2: string;
    carousel_3: string;
    white_bg: string;
  };
};

const ACTIVE_TEMPLATE_KEY = 'mainpg.combo-kit.active-template';
const CUSTOM_TEMPLATE_KEY = 'mainpg.combo-kit.custom-template';

export const COMBO_PRESET_TEMPLATES: ComboPresetTemplate[] = [
  {
    id: 'standard-commerce',
    name: '标准电商套装',
    description: '适合大多数商品组合，保持颜色与材质准确，并分别生成角度图、细节图和白底图。',
    base_prompt_a:
      'professional e-commerce product photography, studio lighting, sharp focus, clean neutral background, accurate color and material, no human, no text overlay, no watermark',
    role_directions: {
      carousel_2: 'alternate angle emphasizing shape and key silhouette, set fully visible',
      carousel_3: 'detail-oriented angle highlighting materials and edges',
      white_bg:
        'complete bundled set on a clean white background, full product visible, balanced layout, professional studio shot',
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
