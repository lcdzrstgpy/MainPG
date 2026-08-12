import type { AiCreationMode, AiModel } from "../types";

export const TEXT_MODELS: AiModel[] = [
  { id: "deepseek-v4-flash", name: "DeepSeek V4 Flash", description: "快速文本对话", capabilities: ["chat"] },
  { id: "deepseek-v4-pro", name: "DeepSeek V4 Pro", description: "高质量文本对话", capabilities: ["chat"] },
  { id: "gpt-5.6-terra", name: "GPT-5.6 Terra", description: "复杂商品创作对话", capabilities: ["chat"] },
];

export const IMAGE_MODELS: AiModel[] = [
  { id: "gpt-image-2-1k", name: "GPT Image 2 · 1K", description: "快速商品图创作", capabilities: ["generate", "edit"] },
  { id: "gpt-image-2-2k", name: "GPT Image 2 · 2K", description: "高品质商品图创作", capabilities: ["generate", "edit"] },
  { id: "gpt-image-2-4k", name: "GPT Image 2 · 4K", description: "高分辨率商品图创作", capabilities: ["generate", "edit"] },
];

export const ALL_MODELS = [...TEXT_MODELS, ...IMAGE_MODELS];

export function modelsForMode(mode: AiCreationMode): AiModel[] {
  return ALL_MODELS.filter((model) => model.capabilities.includes(mode));
}

export function getAvailableModes(model: AiModel): AiCreationMode[] {
  return model.capabilities;
}
