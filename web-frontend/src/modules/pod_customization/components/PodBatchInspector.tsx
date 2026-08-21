import { podItemStatusLabel } from "../data/podCustomizationModel";
import { usePodAssetUrl } from "../data/usePodAssetUrl";
import type { PodBatch, PodBatchItem } from "../types";

type Props = {
  batch: PodBatch | null;
  item?: PodBatchItem;
  busyAction: string;
  onDownload: (path: string, filename: string) => Promise<void>;
};

export function PodBatchInspector({ batch, item, busyAction, onDownload }: Props) {
  const compositePreview = usePodAssetUrl(item?.composite_preview_url);
  const patternPreview = usePodAssetUrl(item?.pattern_preview_url);

  if (!batch || !item) {
    return (
      <aside className="pod-inspector pod-inspector-empty" aria-label="结果检查器">
        <span className="iconfont icon-select" aria-hidden="true" />
        <h3>选择一款结果</h3>
        <p>在中央画廊点击图片后，可查看和下载生成结果。</p>
      </aside>
    );
  }

  const filenameRoot = `pod-${batch.id.slice(0, 8)}-${String(item.index).padStart(3, "0")}`;
  const itemBusy = busyAction.endsWith(`:${item.id}`);
  return (
    <aside className="pod-inspector" aria-label="结果检查器">
      <header><span>RESULT INSPECTOR</span><div><h3>款式 #{String(item.index).padStart(3, "0")}</h3><small className={`status-${item.status}`}>{podItemStatusLabel(item.status)}</small></div></header>

      <div className="pod-inspector-preview">
        {compositePreview
          ? <img src={compositePreview} alt={`第 ${item.index} 款固定场景效果`} />
          : <div><span className="iconfont icon-image" /><p>固定场景效果生成中</p></div>}
        {item.scene_optimized && <span className="pod-inspector-optimized"><i className="iconfont icon-star" />AI 场景已优化</span>}
      </div>

      <section className="pod-inspector-section">
        <div className="pod-inspector-section-title"><b>交付文件</b><small>原始图案与固定场景</small></div>
        <div className="pod-inspector-downloads">
          <button type="button" disabled={!item.pattern_download_url || itemBusy} onClick={() => item.pattern_download_url && void onDownload(item.pattern_download_url, `${filenameRoot}-pattern.png`)}><span className="iconfont icon-download" />下载图案</button>
          <button type="button" className="pod-primary-button" disabled={!item.composite_download_url || itemBusy} onClick={() => item.composite_download_url && void onDownload(item.composite_download_url, `${filenameRoot}-scene.png`)}><span className="iconfont icon-download" />下载场景图</button>
        </div>
        {patternPreview && <a className="pod-pattern-thumb" href={patternPreview} target="_blank" rel="noreferrer"><img src={patternPreview} alt="原始图案网格" /><span>查看原始图案</span></a>}
      </section>

      {item.error_message && <p className="pod-inspector-error" role="alert">{item.error_message}</p>}
      <details className="pod-inspector-prompt"><summary>查看本批次创意快照</summary><pre>{batch.creative_prompt}</pre></details>
    </aside>
  );
}
