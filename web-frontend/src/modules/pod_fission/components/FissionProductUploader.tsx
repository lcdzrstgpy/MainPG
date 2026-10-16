import { FissionImageUploader, type FissionUploaderProps } from "./FissionImageUploader";

export function FissionProductUploader(props: FissionUploaderProps) {
  return <FissionImageUploader {...props} label="目标产品图" title="B · 白底产品图" description="识别产品结构、材质与可印区域" previewAlt="B 白底产品图预览" />;
}
