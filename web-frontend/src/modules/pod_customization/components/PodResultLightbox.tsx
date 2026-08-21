import { podItemStatusLabel } from "../data/podCustomizationModel";
import { PodAssetImage } from "../data/usePodAssetUrl";
import type { PodBatch, PodBatchItem } from "../types";

type Props = {
  batch: PodBatch | null;
  item?: PodBatchItem;
  busyAction: string;
  onClose: () => void;
  onDownload: (path: string, filename: string) => Promise<void>;
};

export function PodResultLightbox({ batch, item, busyAction, onClose, onDownload }: Props) {
  if (!batch || !item) return null;
  const itemBusy = busyAction.endsWith(`:${item.id}`);
  const filename = `pod-${batch.id.slice(0, 8)}-${String(item.index).padStart(3, "0")}`;
  return <div className="pod-result-lightbox-layer" role="dialog" aria-modal="true" aria-label="查看 POD 结果大图">
    <button className="pod-result-lightbox-backdrop" type="button" aria-label="关闭大图" onClick={onClose} />
    <section className="pod-result-lightbox">
      <header><div><span>款式 #{String(item.style_index ?? item.index).padStart(3, "0")} · 图 {item.variant_index ?? 1}</span><h2>{podItemStatusLabel(item.status)}</h2></div><button type="button" onClick={onClose} aria-label="关闭大图">×</button></header>
      <div className="pod-result-lightbox-media">{item.composite_preview_url || item.pattern_preview_url ? <PodAssetImage path={item.composite_preview_url || item.pattern_preview_url} alt="POD 生成结果大图" /> : <p>图片生成中</p>}</div>
      <div className="pod-result-lightbox-actions">
        <button type="button" disabled={!item.pattern_download_url || itemBusy} onClick={() => item.pattern_download_url && void onDownload(item.pattern_download_url, `${filename}-original.png`)}>下载原始直出图</button>
        <button type="button" disabled={!item.composite_download_url || itemBusy} onClick={() => item.composite_download_url && void onDownload(item.composite_download_url, `${filename}-listing.png`)}>下载当前商品图</button>
      </div>
      {item.error_message && <p className="pod-inspector-error">{item.error_message}</p>}
    </section>
  </div>;
}
