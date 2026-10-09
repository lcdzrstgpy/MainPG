import { useCallback, useEffect, useRef, useState } from 'react';

type Props = {
  /** 待裁剪的图片地址（优先使用同源预览地址，避免跨域导致无法读取像素） */
  url: string;
  /** 归属提示文案，例如「12#白色波浪发箍」 */
  label: string;
  /** 裁剪产物上传中 */
  busy?: boolean;
  /** 上传失败信息 */
  error?: string;
  onCancel: () => void;
  onConfirm: (file: File) => void;
};

type Rect = { x: number; y: number; w: number; h: number };

const MIN_SELECTION = 12;

function normalizeRect(startX: number, startY: number, endX: number, endY: number): Rect {
  const x = Math.min(startX, endX);
  const y = Math.min(startY, endY);
  return { x, y, w: Math.abs(endX - startX), h: Math.abs(endY - startY) };
}

/**
 * 在「处理之后的图片」上框选一块区域，裁剪成新图作为该 SKU 的规格图。
 * 默认选中整张图（可直接确认当整图使用），按住拖动可重新框选。
 */
export function PrecheckSkuImageCropper({ url, label, busy = false, error = '', onCancel, onConfirm }: Props) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [stage, setStage] = useState({ w: 0, h: 0 });
  const [loaded, setLoaded] = useState(false);
  const [localError, setLocalError] = useState('');

  const fullRect = useCallback((): Rect => ({ x: 0, y: 0, w: stage.w, h: stage.h }), [stage]);

  const onImageLoad = () => {
    const img = imgRef.current;
    if (!img) return;
    const box = img.getBoundingClientRect();
    setStage({ w: box.width, h: box.height });
    setRect({ x: 0, y: 0, w: box.width, h: box.height });
    setLoaded(true);
  };

  const pointOf = (event: React.PointerEvent<HTMLDivElement>) => {
    const overlay = overlayRef.current;
    if (!overlay) return { x: 0, y: 0 };
    const box = overlay.getBoundingClientRect();
    return {
      x: Math.min(Math.max(event.clientX - box.left, 0), box.width),
      y: Math.min(Math.max(event.clientY - box.top, 0), box.height),
    };
  };

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (busy) return;
    const point = pointOf(event);
    dragRef.current = point;
    event.currentTarget.setPointerCapture(event.pointerId);
    setRect({ x: point.x, y: point.y, w: 0, h: 0 });
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = dragRef.current;
    if (!start) return;
    const point = pointOf(event);
    setRect(normalizeRect(start.x, start.y, point.x, point.y));
  };

  const onPointerUp = () => {
    if (!dragRef.current) return;
    dragRef.current = null;
    setRect((current) => (
      current && (current.w >= MIN_SELECTION && current.h >= MIN_SELECTION) ? current : fullRect()
    ));
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onCancel]);

  const naturalScale = () => {
    const img = imgRef.current;
    if (!img || stage.w <= 0 || stage.h <= 0) return null;
    return { x: img.naturalWidth / stage.w, y: img.naturalHeight / stage.h };
  };

  const confirm = async () => {
    const img = imgRef.current;
    const scale = naturalScale();
    const selection = rect;
    if (!img || !scale || !selection) return;
    const sx = Math.round(selection.x * scale.x);
    const sy = Math.round(selection.y * scale.y);
    const sw = Math.max(1, Math.round(selection.w * scale.x));
    const sh = Math.max(1, Math.round(selection.h * scale.y));
    const canvas = document.createElement('canvas');
    canvas.width = sw;
    canvas.height = sh;
    const context = canvas.getContext('2d');
    if (!context) {
      setLocalError('当前浏览器不支持图片裁剪');
      return;
    }
    try {
      context.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
      const blob = await new Promise<Blob | null>((resolve) => {
        canvas.toBlob((value) => resolve(value), 'image/png');
      });
      if (!blob) throw new Error('crop failed');
      setLocalError('');
      onConfirm(new File([blob], `sku-crop-${Date.now()}.png`, { type: 'image/png' }));
    } catch {
      setLocalError('裁剪失败：浏览器无法读取这张图片的像素。请关闭后重新点「框选裁剪」再试（会先经图床转存），或改用「本地导入的图片」。');
    }
  };

  const scale = naturalScale();
  const outputSize = scale && rect
    ? `${Math.max(1, Math.round(rect.w * scale.x))} × ${Math.max(1, Math.round(rect.h * scale.y))} px`
    : '';

  return (
    <div className="precheck-crop-root" role="dialog" aria-modal="true" aria-label="裁剪图片">
      <div className="precheck-crop-mask" onClick={onCancel} />
      <section className="precheck-crop-panel">
        <header className="precheck-crop-head">
          <div>
            <h3>裁剪图片作为规格图</h3>
            <p>在图片上按住鼠标拖动，框选要作为「{label || '该 SKU'}」规格图的区域。</p>
          </div>
          <button type="button" className="verify-drawer-close" onClick={onCancel} aria-label="关闭">×</button>
        </header>

        <div className="precheck-crop-body">
          <div className="precheck-crop-stage">
            <img
              ref={imgRef}
              src={url}
              alt="待裁剪图片"
              referrerPolicy="no-referrer"
              onLoad={onImageLoad}
              draggable={false}
            />
            <div
              ref={overlayRef}
              className="precheck-crop-overlay"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            >
              {loaded && rect && (
                <div
                  className="precheck-crop-selection"
                  style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
                />
              )}
            </div>
          </div>
          <div className="precheck-crop-meta">
            <span>框选区域：{outputSize || '拖动画框，或直接使用整张图片'}</span>
            <div className="precheck-crop-meta-actions">
              <button type="button" className="btn-mini" onClick={() => setRect(fullRect())} disabled={busy}>使用整张图片</button>
            </div>
          </div>
          {(localError || error) && <div className="verify-message error">{localError || error}</div>}
        </div>

        <footer className="precheck-crop-foot">
          <button type="button" onClick={onCancel} disabled={busy}>取消</button>
          <button type="button" className="primary" onClick={() => { void confirm(); }} disabled={busy || !loaded}>
            {busy ? '上传中…' : '裁剪并用作该 SKU 规格图'}
          </button>
        </footer>
      </section>
    </div>
  );
}
