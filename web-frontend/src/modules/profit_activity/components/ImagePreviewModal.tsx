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

  // 容器不再固定成 92vw×88vh（那会把竖图垫出大白边），改为图片自身尺寸 + 最大边界。
  return createPortal(
    <div className="profit-image-preview-mask" onClick={onClose}>
      <div className="profit-image-preview-modal" style={{ left: "50%", top: "50%", transform: "translate(-50%, -50%)" }} onClick={(event) => event.stopPropagation()}>
        <button type="button" className="profit-image-preview-close" onClick={onClose} title="关闭">×</button>
        <img className="profit-image-preview-img" src={current.url} alt={current.label} referrerPolicy="no-referrer" />
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
