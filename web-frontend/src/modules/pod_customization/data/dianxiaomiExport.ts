const DIANXIAOMI_EXPORT_BLOCK_MESSAGES: Record<string, string> = {
  active_batch: "批次完成后才能导出店小秘表格。",
  listing_fields_missing: "历史批次缺少店小秘上架信息，无法导出。",
  style_copy_missing: "完整标题、英文标题和产品描述生成后才能导出。",
  no_exportable_styles: "当前批次没有图片完整且符合要求的款式。",
};

export function dianxiaomiExportBlockMessage(blockReason: string | null): string {
  return blockReason && DIANXIAOMI_EXPORT_BLOCK_MESSAGES[blockReason]
    ? DIANXIAOMI_EXPORT_BLOCK_MESSAGES[blockReason]
    : "当前批次暂不满足店小秘导出条件。";
}

export function parseDianxiaomiExportHeaderCount(value: string | null): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
}

export function parseDianxiaomiExportFilename(contentDisposition: string | null, fallback: string): string {
  const encoded = contentDisposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded); } catch { return fallback; }
  }
  const quoted = contentDisposition?.match(/filename="([^"]+)"/i)?.[1];
  return quoted || fallback;
}

export function isDianxiaomiExportEnabled(ready: boolean, busyAction: string): boolean {
  return ready && !busyAction;
}
