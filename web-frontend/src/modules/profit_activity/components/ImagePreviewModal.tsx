import { useEffect } from "react";
import { createPortal } from "react-dom";

export type PreviewImage = { url: string; label: string };

/** 大图预览浮层：始终位于当前可视区域中央，可在货源图片之间切换。 */
export function ImagePreviewModal({ images, index, onIndexChange, onClose }: { images: PreviewImage[]; index: number; onIndexChange: (index: number) => void; onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const current = images[index] ?? images[0];
  if (!current) return null;

  // 与「货源与利润」的大图预览保持一致：尽量铺满可视区域（92vw × 88vh），
  // 小图也放大显示，便于核对货源细节。
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;
  const previewWidth = Math.max(320, Math.round(viewportWidth * 0.92));
  const previewHeight = Math.max(320, Math.round(viewportHeight * 0.88));

  return createPortal(
    <div className="profit-image-preview-mask" onClick={onClose}>
      <div className="profit-image-preview-modal" style={{ left: "50%", top: "50%", width: previewWidth, height: previewHeight, transform: "translate(-50%, -50%)" }} onClick={(event) => event.stopPropagation()}>
        <button type="button" className="profit-image-preview-close" onClick={onClose} title="关闭">×</button>
        <img className="profit-image-preview-img" src={current.url} alt={current.label} />
        {images.length > 1 ? (
          <div className="profit-image-preview-pager">
            <button type="button" onClick={() => onIndexChange((index - 1 + images.length) % images.length)}>上一张</button>
            <span>{index + 1} / {images.length}</span>
            <button type="button" onClick={() => onIndexChange((index + 1) % images.length)}>下一张</button>
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
