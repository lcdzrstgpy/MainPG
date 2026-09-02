export type ComboPresetTemplate = {
  id: string;
  name: string;
  subtitle: string;
  base_prompt_a: string;
  role_directions: {
    carousel_2: string;
    carousel_3: string;
    white_bg: string;
  };
};

const ACTIVE_TEMPLATE_STORAGE_KEY = "mainpg.combo-kit.active-preset";

export const COMBO_PRESET_TEMPLATES: ComboPresetTemplate[] = [
  {
    id: "studio",
    name: "通用电商棚拍",
    subtitle: "适用于大多数日用、家居与数码配件套装",
    base_prompt_a: "professional e-commerce product photography, studio lighting, sharp focus, clean neutral background, accurate color and material, no human, no text overlay, no watermark",
    role_directions: {
      carousel_2: "alternate angle emphasizing shape and key silhouette, set fully visible",
      carousel_3: "detail-oriented angle highlighting materials and edges",
      white_bg: "complete bundled set on a clean white background, full product visible, balanced layout, professional studio shot",
    },
  },
  {
    id: "lifestyle",
    name: "生活方式场景",
    subtitle: "突出套装的使用氛围，保留完整商品与真实材质",
    base_prompt_a: "premium e-commerce lifestyle product photography, natural soft lighting, sharp focus, tidy contemporary setting, accurate color and material, no people, no text overlay, no watermark",
    role_directions: {
      carousel_2: "alternate angle in a clean lifestyle setting, entire bundled set visible and easy to understand",
      carousel_3: "close product detail with the complete set still visible, emphasizing useful materials and finish",
      white_bg: "complete bundled set isolated on a pure white background, balanced composition, catalog-ready studio lighting",
    },
  },
  {
    id: "gift",
    name: "礼品套装展示",
    subtitle: "强调多件套的完整性、层次与送礼感",
    base_prompt_a: "premium gift set e-commerce photography, soft studio lighting, sharp focus, elegant balanced arrangement, accurate color and material, no people, no text overlay, no watermark",
    role_directions: {
      carousel_2: "three-quarter angle showing every member product in a premium bundled gift arrangement",
      carousel_3: "detail-oriented view highlighting the coordinated materials and thoughtful bundled presentation",
      white_bg: "all products in the gift bundle fully visible on a clean white background, tidy premium layout, professional catalog shot",
    },
  },
];

function readStoredTemplateId(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_TEMPLATE_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function getActiveTemplateId(): string {
  const stored = readStoredTemplateId();
  return COMBO_PRESET_TEMPLATES.some((template) => template.id === stored)
    ? stored!
    : COMBO_PRESET_TEMPLATES[0].id;
}

export function setActiveTemplateId(templateId: string): void {
  if (!COMBO_PRESET_TEMPLATES.some((template) => template.id === templateId)) return;
  try {
    window.localStorage.setItem(ACTIVE_TEMPLATE_STORAGE_KEY, templateId);
  } catch {
    // 浏览器处于受限存储模式时，当前会话仍可使用默认模板。
  }
}

export function resolveActiveTemplate(): ComboPresetTemplate {
  return COMBO_PRESET_TEMPLATES.find((template) => template.id === getActiveTemplateId())
    ?? COMBO_PRESET_TEMPLATES[0];
}
