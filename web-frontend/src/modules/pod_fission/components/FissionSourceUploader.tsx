import { FissionImageUploader, type FissionUploaderProps } from "./FissionImageUploader";

export function FissionSourceUploader(props: FissionUploaderProps) {
  return <FissionImageUploader {...props} label="来源图" title="A · 爆款样图" description="参考图案、风格、元素与配色" previewAlt="A 样图预览" />;
}
