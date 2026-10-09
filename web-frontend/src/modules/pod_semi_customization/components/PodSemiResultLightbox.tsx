import { createPortal } from "react-dom";

import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";
import { semiItemStatusLabel, semiItemTag } from "../data/podSemiCustomizationModel";
import type { SemiBatch, SemiBatchItem } from "../types";

type Props = {
  batch: SemiBatch | null;
  item?: SemiBatchItem;
  busy: boolean;
  onClose: () => void;
  onDownload: (path: string, filename: string) => Promise<void>;
};

/**
 * 点击结果图后的大图查看层。与全定制的 PodResultLightbox 同一套外壳样式
 * （复用 pod-result-lightbox* 类名），区别是半定制每款只有一张纯图案，
 * 所以没有「原始直出 / 当前商品图」两个下载口，只留一个。
 */
export function PodSemiResultLightbox({ batch, item, busy, onClose, onDownload }: Props) {
  if (!batch || !item) return null;
  const filename = `pod-semi-${batch.id.slice(0, 8)}-${semiItemTag(item.index)}`;
  // portal 到 body：tab 面板 fill-mode 动画的层叠上下文会锁住 fixed 层 z-index，被顶栏盖住
  return createPortal(
    <div className="pod-result-lightbox-layer" role="dialog" aria-modal="true" aria-label="查看半定制图案大图">
      <button className="pod-result-lightbox-backdrop" type="button" aria-label="关闭大图" onClick={onClose} />
      <section className="pod-result-lightbox">
        <header>
          <div>
            <span>{semiItemTag(item.index)}</span>
            <h2>{semiItemStatusLabel(item.status)}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭大图">×</button>
        </header>
        <div className="pod-result-lightbox-media">
          {item.pattern_preview_url
            ? <PodAssetImage path={item.pattern_preview_url} alt="半定制图案大图" />
            : <p>图片生成中</p>}
        </div>
        <div className="pod-result-lightbox-actions pod-semi-lightbox-actions">
          <button
            type="button"
            disabled={!item.pattern_download_url || busy}
            onClick={() => item.pattern_download_url && void onDownload(item.pattern_download_url, `${filename}.png`)}
          >
            下载该款图案
          </button>
        </div>
        {item.error_message && <p className="pod-inspector-error">{item.error_message}</p>}
      </section>
    </div>,
    document.body,
  );
}
