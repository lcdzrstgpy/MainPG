/**
 * 本地拼装模板（2000 × 2000 坐标）。
 *
 * 预检页的 Konva 画布按 2000 设计，预览时整体缩放；导出时 toDataURL
 * 使用 pixelRatio 还原到 2000，保证字体与位置一致。
 */

export type CompositionTemplateId = "headline" | "spec" | "scene";

export type CompositionTemplate = {
  id: CompositionTemplateId;
  label: string;
  description: string;
  background: string;
  product: { x: number; y: number; width: number; height: number; fit: "contain" | "cover" };
  headline: { x: number; y: number; width: number; fontSize: number; color: string };
  subtitle?: {
    x: number;
    y: number;
    width: number;
    fontSize: number;
    background: string;
    color: string;
  };
  verticalLabel?: { x: number; y: number; fontSize: number; color: string; text: string };
  props: Array<{ x: number; y: number; size: number }>;
  spec?: { x: number; y: number; width: number; rowHeight: number; fontSize: number };
};

export const COMPOSITION_TEMPLATES: CompositionTemplate[] = [
  {
    id: "headline",
    label: "大标题卖点",
    description: "大标题 + 产品主图 + 一枚辅助道具，适合详情图第一屏",
    background: "#f6efe3",
    product: { x: 420, y: 640, width: 1160, height: 1140, fit: "contain" },
    headline: { x: 180, y: 140, width: 1640, fontSize: 138, color: "#5b4632" },
    subtitle: {
      x: 180,
      y: 500,
      width: 980,
      fontSize: 54,
      background: "#8a6748",
      color: "#ffffff",
    },
    verticalLabel: { x: 1840, y: 170, fontSize: 40, color: "#8a6748", text: "PRODUCT" },
    props: [
      { x: 1620, y: 1240, size: 190 },
      { x: 230, y: 1270, size: 160 },
    ],
  },
  {
    id: "spec",
    label: "产品信息",
    description: "左侧产品图 + 右侧参数卡，适合尺寸/材质/重量说明",
    background: "#fbf7f0",
    product: { x: 100, y: 300, width: 980, height: 1400, fit: "contain" },
    headline: { x: 1160, y: 190, width: 720, fontSize: 88, color: "#3f3429" },
    spec: { x: 1140, y: 470, width: 720, rowHeight: 140, fontSize: 46 },
    props: [{ x: 1700, y: 1460, size: 180 }],
  },
  {
    id: "scene",
    label: "场景氛围",
    description: "产品 + 2-3 枚道具 + 标题，适合生活方式与使用场景",
    background: "#efe7da",
    product: { x: 560, y: 560, width: 880, height: 1080, fit: "contain" },
    headline: { x: 170, y: 130, width: 1660, fontSize: 120, color: "#4a3b2c" },
    subtitle: {
      x: 170,
      y: 450,
      width: 820,
      fontSize: 46,
      background: "#6f7f68",
      color: "#ffffff",
    },
    verticalLabel: { x: 1850, y: 520, fontSize: 38, color: "#6f7f68", text: "LIFESTYLE" },
    props: [
      { x: 130, y: 1150, size: 210 },
      { x: 1670, y: 1050, size: 200 },
      { x: 1510, y: 1500, size: 175 },
    ],
  },
];

export const COMPOSITION_BACKGROUNDS = [
  "#f6efe3",
  "#fbf7f0",
  "#efe7da",
  "#eef1ec",
  "#f3ecec",
  "#e9eef1",
];

export function compositionTemplateById(
  id: CompositionTemplateId,
): CompositionTemplate {
  return (
    COMPOSITION_TEMPLATES.find((template) => template.id === id) ??
    COMPOSITION_TEMPLATES[0]
  );
}
