import { useEffect, useState } from "react";

import { batchProgress, canRegeneratePodStyle, canRegeneratePodStyleTitle, formatPodBatchWaitingTime, groupPodStyleRows, isActiveBatchStatus, podBatchStatusLabel, podItemStatusLabel, podStyleTitleStatusLabel } from "../data/podCustomizationModel";
import { dianxiaomiExportBlockMessage, isDianxiaomiExportEnabled } from "../data/dianxiaomiExport";
import { PodAssetImage } from "../data/usePodAssetUrl";
import type { PodBatch, PodBatchItem } from "../types";
import { PodListingDetailDrawer } from "./PodListingDetailDrawer";

type Props = {
  batch: PodBatch | null;
  busyAction: string;
  onOpenResult: (item: PodBatchItem, styleIndex: number) => void;
  onRegenerateStyle: (styleIndex: number) => void;
  onRegenerateTitle: (styleIndex: number) => void;
  onRetryFailed: () => void;
  onExportDianxiaomi: () => void;
};

const ROLE_LABELS = ["主图", "细节图 A", "细节图 B", "场景图"] as const;

async function copyTitle(title: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(title);
      return;
    }
    const input = document.createElement("textarea");
    input.value = title;
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.append(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  } catch {
    // Clipboard access can be denied in an embedded or non-secure context.
  }
}

export function PodBatchGallery({ batch, busyAction, onOpenResult, onRegenerateStyle, onRegenerateTitle, onRetryFailed, onExportDianxiaomi }: Props) {
  const [selectedStyleIndex, setSelectedStyleIndex] = useState<number>();
  const [now, setNow] = useState(() => Date.now());
  const showWaitingTime = Boolean(batch && isActiveBatchStatus(batch.status));

  useEffect(() => {
    if (!showWaitingTime) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [showWaitingTime]);

  if (!batch) return <section className="pod-gallery pod-gallery-empty" aria-label="POD 批次画廊"><span className="iconfont icon-skin" aria-hidden="true" /><h2>从一个模板开始本批次</h2><p>生成结果会固定归在对应款式下。</p></section>;

  const progress = batchProgress(batch);
  const styles = groupPodStyleRows(batch);
  const retryableStyles = styles.filter(
    (style) =>
      canRegeneratePodStyle(batch.status, style.status)
      || canRegeneratePodStyleTitle(batch.status, style.title_status, style.results),
  );
  const retryingFailed = busyAction === "batch-retry-failed";
  const exportStatus = batch.dianxiaomi_export;
  const exporting = busyAction === "export-dianxiaomi";
  const exportBlockReason = busyAction
    ? exporting ? "正在生成店小秘导出文件，请稍候。" : "当前批次正在处理其他操作，请稍候。"
    : dianxiaomiExportBlockMessage(exportStatus.block_reason);
  const canExport = isDianxiaomiExportEnabled(batch.dianxiaomi_export.ready, busyAction);
  const selectedStyle = styles.find((style) => style.index === selectedStyleIndex);
  return <><section className="pod-gallery" aria-label="POD 批次画廊">
    <header className="pod-gallery-header">
      <div><span>POD BATCH · {batch.id.slice(0, 8)}</span><h2>{batch.title || `${batch.template_name} 创作批次`}</h2><p>当前批次 <b>{batch.count} 款</b> · {batch.template_name}</p></div>
      <div className="pod-gallery-header-actions">
        <div className={`pod-batch-status status-${batch.status}`}><strong>{podBatchStatusLabel(batch.status)}</strong><span>{batch.processed_count} / {batch.count} 款</span><span>可上架 {batch.listing_ready_count ?? 0} / 总款数 {batch.count}</span></div>
        <div className="pod-dianxiaomi-export">
          {retryableStyles.length > 0 && (
            <button type="button" className="pod-batch-retry-failed" disabled={Boolean(busyAction)} onClick={onRetryFailed}>{retryingFailed ? "批量重试中…" : `重试失败款式 (${retryableStyles.length})`}</button>
          )}
          <button type="button" disabled={!canExport} title={canExport ? `可导出 ${exportStatus.exportable_style_count} 款` : exportBlockReason} onClick={onExportDianxiaomi}>{exporting ? "正在导出店小秘表格" : "导出店小秘表格"}</button>
          {!canExport && <small>{exportBlockReason}</small>}
        </div>
      </div>
    </header>
    <div className="pod-gallery-progress"><div role="progressbar" aria-label="POD 批次进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div><small>{showWaitingTime && <>已等待 {formatPodBatchWaitingTime(batch.created_at, now)} · </>}完成 {batch.completed_count} 款 · 失败 {batch.failed_count} 款 · {progress}%</small></div>
    <div className="pod-style-rows">
      {styles.map((style) => {
        const regenerating = busyAction === `regenerate-style:${style.index}`;
        const regeneratingTitle = busyAction === `regenerate-title:${style.index}`;
        const canRegenerate = Boolean(batch.style_grid) && canRegeneratePodStyle(batch.status, style.status);
        const canRegenerateTitle = !busyAction && canRegeneratePodStyleTitle(batch.status, style.title_status, style.results);
        return <article className={`pod-style-row status-${style.status}`} key={style.index}>
          <header><span className="pod-style-row-status" aria-hidden="true">{style.status === "completed" ? "✓" : style.status === "failed" ? "!" : "·"}</span><div className="pod-style-row-main"><button type="button" className="pod-style-title-button" title={style.title} onClick={() => setSelectedStyleIndex(style.index)}>{style.title}</button><small>{podStyleTitleStatusLabel(style.title_status, style.listing_ready)} · {style.status === "partial_failure" ? "图片部分生成失败" : style.status === "generating" ? "图片正在生成" : podItemStatusLabel(style.status)}</small>{style.title_error_message && <small className="pod-style-title-error" title={style.title_error_message}>标题生成失败，可重新生成</small>}</div><div className="pod-style-row-actions"><button type="button" disabled={!style.title.trim()} onClick={() => void copyTitle(style.title)}>复制标题</button><button type="button" disabled={!canRegenerateTitle || regeneratingTitle} onClick={() => onRegenerateTitle(style.index)}>{regeneratingTitle ? "标题生成中" : "只重生标题"}</button><button type="button" disabled={!canRegenerate || regenerating} onClick={() => onRegenerateStyle(style.index)}>{regenerating ? "重新生成中" : "整款重生成"}</button></div></header>
          <div className="pod-style-result-grid">
            {style.results.map((item, offset) => {
              const preview = item?.composite_preview_url || item?.pattern_preview_url;
              return <button type="button" key={item?.id ?? `style-${style.index}-variant-${offset + 1}`} className={`pod-style-result ${item ? `status-${item.status}` : "status-queued"}`} disabled={!item || !preview} onClick={() => item && onOpenResult(item, style.index)} aria-label={`${style.title}，第 ${offset + 1} 张${preview ? "，查看大图" : "，等待生成"}`}>
                {preview ? <PodAssetImage path={preview} alt="" loading="lazy" decoding="async" /> : <span className="pod-style-result-placeholder">等待生成</span>}
                <small>{ROLE_LABELS[offset]} · {podItemStatusLabel(item?.status ?? "queued")}</small>{item?.error_message && <i title={item.error_message}>!</i>}
              </button>;
            })}
          </div>
        </article>;
      })}
    </div>
  </section><PodListingDetailDrawer batch={batch} style={selectedStyle} onClose={() => setSelectedStyleIndex(undefined)} /></>;
}
